"""Widok porównawczy one-step vs two-step.

Skanuje `final_results/` w poszukiwaniu runów zawierających `onestep.jsonl`
(produkty `scripts/compare_onestep_vs_twostep.py`). UI w tabsach:

  Verdict   — wskaźniki D7b: speedup ≥1.5×, category match ≥90%, Jaccard ≥0.5,
              fail rate one ≤ two. Banner ✅/⚠️/❌ na górze.
  Speed     — wall time, throughput URL/min/h, latency mean/p50/p95, tokens
              prompt+completion per phase, total tokens.
  Quality   — language/category match %, Jaccard mean+median, histogram Jaccard,
              długości i braki SEO meta, tabela per-URL.
  Eyeball   — pojedynczy artykuł side-by-side (title/meta/h1/summary + diff
              encji).

Niezależny od `load_results()` — własny scanner, żeby nie kolidować z istniejącymi
widokami two-step.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.data_loader import RESULTS_BASE

SEO_FIELDS = ["title", "meta_description", "h1", "article_summary"]

# Decision criteria (D7b — patrz DECISIONS.md).
CRIT_SPEEDUP_WALL = 1.5
CRIT_SPEEDUP_PER_URL = 1.5
CRIT_CAT_MATCH = 0.90
CRIT_LANG_MATCH = 0.95
CRIT_JACCARD = 0.5


# ---------------- helpers ----------------

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _dedup_last(records: list[dict]) -> dict[str, dict]:
    by_hash: dict[str, dict] = {}
    for r in records:
        h = r.get("url_hash")
        if h:
            by_hash[h] = r
    return by_hash


def _entity_set(rec: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for e in rec.get("entities") or []:
        name = (e.get("name") or "").strip().lower()
        typ = e.get("type") or ""
        if name:
            out.add((name, typ))
    return out


def _stat(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "sum": 0.0}
    s = sorted(values)
    n = len(s)
    p = lambda q: s[min(int(q * n), n - 1)]
    return {
        "n": n,
        "mean": statistics.fmean(s),
        "p50": p(0.50),
        "p95": p(0.95),
        "min": s[0],
        "max": s[-1],
        "sum": sum(s),
    }


def _fmt_hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _ok(v: float, threshold: float, mode: str = "ge") -> bool:
    if mode == "ge":
        return v >= threshold
    if mode == "le":
        return v <= threshold
    return False


@st.cache_data(ttl=10, show_spinner=False)
def _list_compare_runs() -> list[str]:
    if not RESULTS_BASE.exists():
        return []
    out: list[str] = []
    for d in sorted(RESULTS_BASE.iterdir()):
        if not d.is_dir():
            continue
        if (d / "onestep.jsonl").exists():
            out.append(d.name)
    return out


@st.cache_data(ttl=10, show_spinner=False)
def _load_run(run_name: str) -> dict:
    d = RESULTS_BASE / run_name
    onestep = _dedup_last(_read_jsonl(d / "onestep.jsonl"))
    step1 = _dedup_last(_read_jsonl(d / "entity_layer.jsonl"))
    step2 = _dedup_last(_read_jsonl(d / "final.jsonl"))
    meta = {}
    mp = d / "compare_meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
        except json.JSONDecodeError:
            pass
    return {"onestep": onestep, "step1": step1, "step2": step2, "meta": meta, "dir": d}


def _compute_metrics(payload: dict) -> dict:
    """Zlicza wszystkie metryki potrzebne we wszystkich tabsach (raz, na cache)."""
    onestep = payload["onestep"]
    step1 = payload["step1"]
    step2 = payload["step2"]
    meta = payload["meta"]

    common = sorted(set(onestep) & set(step2))

    # --- speed ---
    one_lat = [r["latency_s"] for r in onestep.values() if r.get("ok")]
    s1_lat = [r["latency_s"] for r in step1.values() if r.get("ok")]
    s2_lat = [r["latency_s"] for r in step2.values() if r.get("ok")]
    two_combined = [
        float(step1[h]["latency_s"]) + float(step2[h]["latency_s"])
        for h in common
        if step1.get(h, {}).get("ok") and step2.get(h, {}).get("ok")
    ]

    one_in = [int((r.get("usage") or {}).get("prompt_tokens", 0)) for r in onestep.values() if r.get("ok")]
    one_out = [int((r.get("usage") or {}).get("completion_tokens", 0)) for r in onestep.values() if r.get("ok")]
    s1_in = [int((r.get("usage") or {}).get("prompt_tokens", 0)) for r in step1.values() if r.get("ok")]
    s1_out = [int((r.get("usage") or {}).get("completion_tokens", 0)) for r in step1.values() if r.get("ok")]
    s2_in = [int((r.get("usage") or {}).get("prompt_tokens", 0)) for r in step2.values() if r.get("ok")]
    s2_out = [int((r.get("usage") or {}).get("completion_tokens", 0)) for r in step2.values() if r.get("ok")]

    one_wall = float(meta.get("onestep_wall_s") or 0.0)
    two_wall = float(meta.get("twostep_wall_s") or 0.0)
    speedup_wall = (two_wall / one_wall) if one_wall > 0 else 0.0
    one_mean = _stat(one_lat)["mean"]
    two_mean = _stat(two_combined)["mean"]
    speedup_per_url = (two_mean / one_mean) if one_mean > 0 else 0.0

    # throughput URL/h (na bazie wall time)
    n_one = len([r for r in onestep.values() if r.get("ok")])
    n_two = len(two_combined)
    thr_one = (n_one / one_wall * 3600) if one_wall > 0 else 0.0
    thr_two = (n_two / two_wall * 3600) if two_wall > 0 else 0.0

    # --- quality (na common OK subset) ---
    lang_match = cat_match = 0
    jaccard_vals: list[float] = []
    intersect_vals: list[int] = []
    one_counts: list[int] = []
    two_counts: list[int] = []
    field_lens: dict = {f: {"one": [], "two": []} for f in SEO_FIELDS}
    missing: dict = {f: {"one": 0, "two": 0} for f in SEO_FIELDS}
    per_url_rows: list[dict] = []

    for h in common:
        one = onestep[h]; two = step2[h]
        if not (one.get("ok") and two.get("ok")):
            continue
        lm = (one.get("language") or "") == (two.get("language") or "")
        cm = (one.get("category") or "") == (two.get("category") or "")
        lang_match += int(lm); cat_match += int(cm)
        a = _entity_set(one); b = _entity_set(two)
        jacc = (len(a & b) / len(a | b)) if (a or b) else 0.0
        jaccard_vals.append(jacc); intersect_vals.append(len(a & b))
        one_counts.append(len(a)); two_counts.append(len(b))
        for f in SEO_FIELDS:
            v1 = one.get(f); v2 = two.get(f)
            if isinstance(v1, str): field_lens[f]["one"].append(len(v1))
            else: missing[f]["one"] += 1
            if isinstance(v2, str): field_lens[f]["two"].append(len(v2))
            else: missing[f]["two"] += 1
        per_url_rows.append({
            "url": one.get("url"),
            "lang_match": lm,
            "cat_match": cm,
            "category_one": one.get("category"),
            "category_two": two.get("category"),
            "ent_one": len(a),
            "ent_two": len(b),
            "jaccard": round(jacc, 3),
            "lat_one": round(float(one.get("latency_s") or 0), 2),
            "lat_two_combined": round(
                float(step1.get(h, {}).get("latency_s") or 0)
                + float(two.get("latency_s") or 0), 2),
            "url_hash": h,
        })

    n_q = max(len(per_url_rows), 1)

    # --- ok/fail rates ---
    one_total = len(onestep)
    one_ok = sum(1 for r in onestep.values() if r.get("ok"))
    s1_total = len(step1)
    s1_ok = sum(1 for r in step1.values() if r.get("ok"))
    s2_total = len(step2)
    s2_ok = sum(1 for r in step2.values() if r.get("ok"))
    one_fail_rate = (one_total - one_ok) / one_total if one_total > 0 else 0.0
    # two-step fail rate = jakikolwiek step zawalił dla danego URL (Step 1 lub Step 2)
    two_total = len(set(step1) | set(step2)) or s1_total
    two_ok_combined = sum(
        1 for h in (set(step1) | set(step2))
        if step1.get(h, {}).get("ok") and step2.get(h, {}).get("ok")
    )
    two_fail_rate = (two_total - two_ok_combined) / two_total if two_total > 0 else 0.0

    return {
        "common": common,
        "one_lat": one_lat, "s1_lat": s1_lat, "s2_lat": s2_lat,
        "two_combined": two_combined,
        "one_in": one_in, "one_out": one_out,
        "s1_in": s1_in, "s1_out": s1_out, "s2_in": s2_in, "s2_out": s2_out,
        "one_wall": one_wall, "two_wall": two_wall,
        "speedup_wall": speedup_wall,
        "speedup_per_url": speedup_per_url,
        "one_mean_lat": one_mean, "two_mean_lat": two_mean,
        "thr_one": thr_one, "thr_two": thr_two,
        "lang_match": lang_match, "cat_match": cat_match,
        "n_q": n_q,
        "lang_match_rate": lang_match / n_q,
        "cat_match_rate": cat_match / n_q,
        "jaccard_vals": jaccard_vals,
        "jaccard_mean": statistics.fmean(jaccard_vals) if jaccard_vals else 0.0,
        "jaccard_median": statistics.median(jaccard_vals) if jaccard_vals else 0.0,
        "intersect_vals": intersect_vals,
        "one_counts": one_counts, "two_counts": two_counts,
        "field_lens": field_lens, "missing": missing,
        "per_url_rows": per_url_rows,
        "one_total": one_total, "one_ok": one_ok, "one_fail_rate": one_fail_rate,
        "s1_total": s1_total, "s1_ok": s1_ok,
        "s2_total": s2_total, "s2_ok": s2_ok,
        "two_total": two_total, "two_ok_combined": two_ok_combined,
        "two_fail_rate": two_fail_rate,
    }


# ---------------- render ----------------

def render(filters: dict, data: dict):
    st.title("One-step vs Two-step")

    runs = _list_compare_runs()
    if not runs:
        st.warning(
            "Brak runów porównawczych. Uruchom:\n\n"
            "```bash\n"
            "python3 scripts/compare_onestep_vs_twostep.py --limit 20 --concurrency 4 --tag mini\n"
            "```\n\n"
            f"Skrypt szuka katalogów w `{RESULTS_BASE}/` zawierających `onestep.jsonl`."
        )
        return

    sel = st.selectbox("Run", runs, index=len(runs) - 1)
    payload = _load_run(sel)
    meta = payload["meta"]
    M = _compute_metrics(payload)

    sample_info = (
        f"random=True · seed={meta.get('seed', '?')}"
        if meta.get("random_sample") else "first-N (sorted)"
    )
    st.caption(
        f"Sample: **{sample_info}** · limit={meta.get('limit', '?')} · "
        f"concurrency={meta.get('concurrency', '?')} · "
        f"common OK={len(M['common'])} · "
        f"dir: `{payload['dir'].name}`"
    )

    # ---------- VERDICT BANNER (zawsze widoczny) ----------
    _render_verdict_banner(M)

    tabs = st.tabs(["📊 Verdict", "🚀 Speed", "🎯 Quality", "🔍 Eyeball"])
    with tabs[0]:
        _render_verdict_tab(M)
    with tabs[1]:
        _render_speed_tab(M, meta)
    with tabs[2]:
        _render_quality_tab(M, payload)
    with tabs[3]:
        _render_eyeball_tab(M, payload)


# ---------------- verdict banner ----------------

def _render_verdict_banner(M: dict):
    """Krótkie podsumowanie kto wygrywa (zawsze widoczne nad tabsami)."""
    speed_ok = _ok(M["speedup_wall"], CRIT_SPEEDUP_WALL)
    quality_ok = (
        _ok(M["cat_match_rate"], CRIT_CAT_MATCH)
        and _ok(M["lang_match_rate"], CRIT_LANG_MATCH)
        and _ok(M["jaccard_mean"], CRIT_JACCARD)
    )
    fails_ok = M["one_fail_rate"] <= M["two_fail_rate"]

    if speed_ok and quality_ok and fails_ok:
        st.success(
            f"✅ **One-step jest kandydatem na prod.** "
            f"speedup={M['speedup_wall']:.2f}× · cat={100*M['cat_match_rate']:.1f}% · "
            f"Jaccard={M['jaccard_mean']:.2f} · fail one≤two."
        )
    elif speed_ok and not quality_ok:
        st.warning(
            f"⚠️ **One-step szybsze, ale traci na jakości.** "
            f"speedup={M['speedup_wall']:.2f}× · cat={100*M['cat_match_rate']:.1f}% "
            f"(target ≥{int(100*CRIT_CAT_MATCH)}%) · Jaccard={M['jaccard_mean']:.2f} "
            f"(target ≥{CRIT_JACCARD}). Two-step zostaje defaultem."
        )
    elif not speed_ok and quality_ok:
        st.info(
            f"ℹ️ **One-step jakość OK, ale brak istotnego speedupu.** "
            f"speedup={M['speedup_wall']:.2f}× (target ≥{CRIT_SPEEDUP_WALL}×). "
            f"Two-step zostaje defaultem (więcej kontroli, pipe note)."
        )
    else:
        st.error(
            f"❌ **One-step przegrywa: speed={M['speedup_wall']:.2f}× "
            f"cat={100*M['cat_match_rate']:.1f}% Jaccard={M['jaccard_mean']:.2f}.** "
            f"Two-step zostaje defaultem."
        )


# ---------------- tab: VERDICT ----------------

def _render_verdict_tab(M: dict):
    st.subheader("Decision criteria (D7b)")
    st.caption(
        "Reguła: **WSZYSTKIE** kryteria muszą być spełnione, żeby uzasadnić zmianę "
        "defaultu z two-step na one-step. Patrz DECISIONS.md → D7."
    )

    rows = [
        {
            "criterion": "Speedup wall (two/one)",
            "target": f"≥ {CRIT_SPEEDUP_WALL:.1f}×",
            "actual": f"{M['speedup_wall']:.2f}×",
            "pass": _ok(M["speedup_wall"], CRIT_SPEEDUP_WALL),
        },
        {
            "criterion": "Speedup per-URL latency",
            "target": f"≥ {CRIT_SPEEDUP_PER_URL:.1f}×",
            "actual": f"{M['speedup_per_url']:.2f}×",
            "pass": _ok(M["speedup_per_url"], CRIT_SPEEDUP_PER_URL),
        },
        {
            "criterion": "Category match",
            "target": f"≥ {int(100*CRIT_CAT_MATCH)}%",
            "actual": f"{100*M['cat_match_rate']:.1f}%",
            "pass": _ok(M["cat_match_rate"], CRIT_CAT_MATCH),
        },
        {
            "criterion": "Language match",
            "target": f"≥ {int(100*CRIT_LANG_MATCH)}%",
            "actual": f"{100*M['lang_match_rate']:.1f}%",
            "pass": _ok(M["lang_match_rate"], CRIT_LANG_MATCH),
        },
        {
            "criterion": "Entity Jaccard mean",
            "target": f"≥ {CRIT_JACCARD:.2f}",
            "actual": f"{M['jaccard_mean']:.3f}",
            "pass": _ok(M["jaccard_mean"], CRIT_JACCARD),
        },
        {
            "criterion": "Fail rate (one ≤ two)",
            "target": f"one_fail ≤ two_fail",
            "actual": f"{100*M['one_fail_rate']:.1f}% ≤ {100*M['two_fail_rate']:.1f}%",
            "pass": M["one_fail_rate"] <= M["two_fail_rate"],
        },
    ]
    df = pd.DataFrame(rows)
    df["pass"] = df["pass"].map({True: "✅ pass", False: "❌ fail"})
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Co wybrać — speed vs jakość?")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🟢 One-step lepszy gdy")
        st.markdown(
            "- bottleneck to **wall time** (np. prod 21M URL × $)\n"
            "- jakość wystarczająca: cat ≥90%, Jaccard ≥0.5\n"
            "- nie potrzebujemy reusable **pipe note** (entities jako warstwa pośrednia)\n"
            "- speed gain > 1.5× wall **i** jakość spełnia próg"
        )
    with c2:
        st.markdown("##### 🔵 Two-step lepszy gdy")
        st.markdown(
            "- entity layer jest produktem (knowledge graph, multilingual expansion)\n"
            "- prompty step1/step2 mają osobne sampling/temp (D12)\n"
            "- chcemy explicit fallback: jeśli step2 falsuje, mamy entity layer\n"
            "- speed gap < 1.5× albo Jaccard <0.5 — czyli teraz"
        )


# ---------------- tab: SPEED ----------------

def _render_speed_tab(M: dict, meta: dict):
    history = meta.get("history") or []
    n_two_segm = sum(1 for h in history if h.get("phase") == "twostep")
    n_one_segm = sum(1 for h in history if h.get("phase") == "onestep")

    st.subheader("Wall time + throughput")
    st.caption(
        "**Wall time** = subprocess time **sumowany przez wszystkie segmenty** "
        "(każdy run / resume dopisuje segment do `compare_meta.json` → `history`). "
        "Obejmuje load_articles, batchowanie, finalizację. Model **nie zwraca wall time** — "
        "to nasz zewnętrzny pomiar. Per-request `latency_s` (poniżej) to round-trip HTTP do vLLM."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Wall one-step", f"{M['one_wall']:.1f}s",
              help=f"suma {n_one_segm} segmentów")
    c2.metric("Wall two-step", f"{M['two_wall']:.1f}s",
              help=f"suma {n_two_segm} segmentów")
    c3.metric("Speedup wall", f"{M['speedup_wall']:.2f}×",
              delta=f"{(M['speedup_wall']-1)*100:+.0f}%")
    c4.metric("Speedup per-URL", f"{M['speedup_per_url']:.2f}×",
              delta=f"{(M['speedup_per_url']-1)*100:+.0f}%")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Throughput one-step", f"{M['thr_one']:.0f} URL/h")
    c2.metric("Throughput two-step", f"{M['thr_two']:.0f} URL/h")
    eta_one = (21_000_000 / M['thr_one'] / 24) if M['thr_one'] > 0 else 0
    eta_two = (21_000_000 / M['thr_two'] / 24) if M['thr_two'] > 0 else 0
    c3.metric("ETA 21M URL (one-step)", f"{eta_one:.0f} dni")
    c4.metric("ETA 21M URL (two-step)", f"{eta_two:.0f} dni")
    st.caption(
        f"ETA = ekstrapolacja z bieżącego throughputu (concurrency={meta.get('concurrency', '?')}, "
        f"DGX Spark). Prod target: RTX 5090 — ~2-3× szybciej."
    )

    # ---------- historia segmentów ----------
    if history:
        st.subheader("Historia segmentów (każdy run / resume)")
        st.caption(
            "Wall time u góry = **suma segmentów**. Resume nie nadpisuje — kumuluje. "
            "`przetworzono` = ile nowych OK rekordów dopisał ten segment (po dedupie po url_hash)."
        )
        rows = []
        for i, h in enumerate(history, 1):
            rows.append({
                "#": i,
                "phase": h.get("phase", "?"),
                "started_at": h.get("started_at", "?"),
                "ended_at": h.get("ended_at", "?"),
                "wall (s)": round(float(h.get("wall_s", 0) or 0), 1),
                "wall (h:m:s)": _fmt_hms(float(h.get("wall_s", 0) or 0)),
                "ok before": h.get("ok_records_before", 0),
                "ok after": h.get("ok_records_after", 0),
                "+processed": h.get("ok_processed_in_segment", 0),
                "rc": h.get("rc", "?"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Per-request latency (s) — z odpowiedzi vLLM")
    rows = []
    for label, lat in [
        ("one-step", M["one_lat"]),
        ("two-step S1", M["s1_lat"]),
        ("two-step S2", M["s2_lat"]),
        ("two-step combined", M["two_combined"]),
    ]:
        s = _stat(lat)
        rows.append({
            "phase": label, "n": s["n"],
            "mean": round(s["mean"], 2),
            "p50": round(s["p50"], 2),
            "p95": round(s["p95"], 2),
            "min": round(s["min"], 2),
            "max": round(s["max"], 2),
            "sum": round(s["sum"], 1),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Histogram latency one-step vs two-step combined
    st.subheader("Histogram latency (per-URL)")
    if M["one_lat"] and M["two_combined"]:
        df_hist = pd.DataFrame({
            "latency_s": M["one_lat"] + M["two_combined"],
            "phase": ["one-step"] * len(M["one_lat"]) + ["two-step combined"] * len(M["two_combined"]),
        })
        try:
            import altair as alt
            chart = (
                alt.Chart(df_hist)
                .mark_bar(opacity=0.6)
                .encode(
                    x=alt.X("latency_s:Q", bin=alt.Bin(maxbins=30), title="latency (s)"),
                    y=alt.Y("count()", title="# URL"),
                    color=alt.Color("phase:N"),
                )
                .properties(height=240)
            )
            st.altair_chart(chart, use_container_width=True)
        except ImportError:
            st.bar_chart(df_hist.groupby("phase")["latency_s"].apply(list))

    st.subheader("Tokens — prompt + completion (per URL)")
    def _m(xs): return round(statistics.fmean(xs), 0) if xs else 0
    tok = pd.DataFrame([
        {"phase": "one-step",
         "prompt mean": _m(M["one_in"]), "completion mean": _m(M["one_out"]),
         "prompt sum": sum(M["one_in"]), "completion sum": sum(M["one_out"]),
         "total per URL": _m(M["one_in"]) + _m(M["one_out"])},
        {"phase": "two-step S1",
         "prompt mean": _m(M["s1_in"]), "completion mean": _m(M["s1_out"]),
         "prompt sum": sum(M["s1_in"]), "completion sum": sum(M["s1_out"]),
         "total per URL": _m(M["s1_in"]) + _m(M["s1_out"])},
        {"phase": "two-step S2",
         "prompt mean": _m(M["s2_in"]), "completion mean": _m(M["s2_out"]),
         "prompt sum": sum(M["s2_in"]), "completion sum": sum(M["s2_out"]),
         "total per URL": _m(M["s2_in"]) + _m(M["s2_out"])},
        {"phase": "two-step combined",
         "prompt mean": _m(M["s1_in"]) + _m(M["s2_in"]),
         "completion mean": _m(M["s1_out"]) + _m(M["s2_out"]),
         "prompt sum": sum(M["s1_in"]) + sum(M["s2_in"]),
         "completion sum": sum(M["s1_out"]) + sum(M["s2_out"]),
         "total per URL": _m(M["s1_in"]) + _m(M["s2_in"]) + _m(M["s1_out"]) + _m(M["s2_out"])},
    ])
    st.dataframe(tok, use_container_width=True, hide_index=True)
    st.caption(
        "**prefix caching ON** — `prompt sum` w one-step jest niemal cały zcache'owany "
        "(system prompt v6 ≈5k tok). Realny koszt $ na RunPod = całkowite **completion** tokens "
        "+ uncached prefix przy pierwszym requeście. Two-step ma 2× więcej cached prefix "
        "(2 system prompty), ale generuje też 2× requestów."
    )


# ---------------- tab: QUALITY ----------------

def _render_quality_tab(M: dict, payload: dict):
    n = M["n_q"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("language match", f"{M['lang_match']}/{n}",
              f"{100*M['lang_match_rate']:.1f}%")
    c2.metric("category match", f"{M['cat_match']}/{n}",
              f"{100*M['cat_match_rate']:.1f}%")
    c3.metric("Jaccard mean", f"{M['jaccard_mean']:.3f}")
    c4.metric("Jaccard median", f"{M['jaccard_median']:.3f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entities one-step (mean)",
              f"{statistics.fmean(M['one_counts']) if M['one_counts'] else 0:.1f}")
    c2.metric("Entities two-step (mean)",
              f"{statistics.fmean(M['two_counts']) if M['two_counts'] else 0:.1f}")
    c3.metric("Intersection mean",
              f"{statistics.fmean(M['intersect_vals']) if M['intersect_vals'] else 0:.1f}")
    c4.metric("one-step OK", f"{M['one_ok']}/{M['one_total']}",
              f"fail {100*M['one_fail_rate']:.1f}%")

    # Histogram Jaccard
    st.subheader("Rozkład Jaccard encji per URL")
    if M["jaccard_vals"]:
        df_j = pd.DataFrame({"jaccard": M["jaccard_vals"]})
        try:
            import altair as alt
            chart = (
                alt.Chart(df_j)
                .mark_bar()
                .encode(
                    x=alt.X("jaccard:Q", bin=alt.Bin(step=0.05), title="Jaccard (one ∩ two / one ∪ two)"),
                    y=alt.Y("count()", title="# URL"),
                )
                .properties(height=220)
            )
            st.altair_chart(chart, use_container_width=True)
        except ImportError:
            st.bar_chart(df_j["jaccard"])
    st.caption(
        f"Próg D7b: Jaccard mean ≥ {CRIT_JACCARD}. Niska wartość = one-step ekstrahuje "
        "inne encje niż two-step (nie znaczy gorsze — wymaga eyeballa)."
    )

    # SEO meta lengths
    st.subheader("SEO meta — długości pól (znaki)")
    seo_rows = []
    for f in SEO_FIELDS:
        seo_rows.append({
            "field": f,
            "one len mean": round(statistics.fmean(M["field_lens"][f]["one"]), 0)
                if M["field_lens"][f]["one"] else None,
            "two len mean": round(statistics.fmean(M["field_lens"][f]["two"]), 0)
                if M["field_lens"][f]["two"] else None,
            "missing one": M["missing"][f]["one"],
            "missing two": M["missing"][f]["two"],
        })
    st.dataframe(pd.DataFrame(seo_rows), use_container_width=True, hide_index=True)
    st.caption("Targety SEO: title 50-60, meta_description 140-160, h1 ~50-80, summary ~250-350.")

    # ---------- Category mismatches (one vs two) ----------
    st.subheader("Kategorie — one-step vs two-step")
    st.caption(
        "Liczba URL gdzie one-step i two-step **przypisały tę samą / inną kategorię**. "
        "Tabela mismatchy: które kategorie one-step zwraca w miejscu two-step (sygnał gdzie "
        "prompty się rozjeżdżają)."
    )

    cat_pairs: list[tuple[str, str]] = []
    for r in M["per_url_rows"]:
        cat_pairs.append((r.get("category_one") or "(brak)",
                          r.get("category_two") or "(brak)"))
    if cat_pairs:
        df_pairs = pd.DataFrame(cat_pairs, columns=["one_step", "two_step"])
        # Mismatchy → grupowanie
        mismatch = df_pairs[df_pairs["one_step"] != df_pairs["two_step"]]
        if not mismatch.empty:
            grouped = (
                mismatch.groupby(["two_step", "one_step"]).size()
                .reset_index(name="count")
                .sort_values("count", ascending=False)
            )
            st.markdown(f"**Mismatchy: {len(mismatch)}/{len(df_pairs)} URL "
                        f"({100*len(mismatch)/len(df_pairs):.1f}%)**")
            st.dataframe(grouped, use_container_width=True, hide_index=True, height=280)
        else:
            st.success("✅ Wszystkie URL: identyczna kategoria w one-step i two-step.")

        # Top kategorie zwracane przez each
        st.markdown("**Top 15 kategorii — agreement:**")
        cat_one = df_pairs["one_step"].value_counts().head(15)
        cat_two = df_pairs["two_step"].value_counts().head(15)
        all_cats = sorted(set(cat_one.index) | set(cat_two.index))
        comp = pd.DataFrame({
            "one-step": [cat_one.get(c, 0) for c in all_cats],
            "two-step": [cat_two.get(c, 0) for c in all_cats],
        }, index=all_cats).sort_values("two-step", ascending=False).head(15)
        st.bar_chart(comp, height=300, use_container_width=True)
        st.dataframe(comp, use_container_width=True)

    # Per-URL table
    st.subheader("Per-URL diff")
    if M["per_url_rows"]:
        df = pd.DataFrame(M["per_url_rows"])
        only_diff = st.checkbox(
            "Pokaż tylko URL z różnicami (lang/cat mismatch albo Jaccard<0.5)",
            value=False,
        )
        if only_diff:
            df = df[(~df["lang_match"]) | (~df["cat_match"]) | (df["jaccard"] < 0.5)]
        st.dataframe(df.drop(columns=["url_hash"]), use_container_width=True, hide_index=True)


# ---------------- tab: EYEBALL ----------------

def _render_eyeball_tab(M: dict, payload: dict):
    onestep = payload["onestep"]
    step1 = payload["step1"]
    step2 = payload["step2"]

    if not M["per_url_rows"]:
        st.info("Brak common OK URL — uruchom oba pipeline'y na tym samym sample'u.")
        return

    url_options = [(r["url"], r["url_hash"]) for r in M["per_url_rows"]]
    labels = [u for u, _ in url_options]
    sel_label = st.selectbox("URL", labels, key="onestep_eyeball")
    sel_hash = next(h for u, h in url_options if u == sel_label)

    one = onestep.get(sel_hash) or {}
    s1 = step1.get(sel_hash) or {}
    s2 = step2.get(sel_hash) or {}

    cols = st.columns(2)
    with cols[0]:
        st.markdown("#### one-step")
        st.caption(
            f"lang={one.get('language')!r} · cat={one.get('category')!r} · "
            f"lat={one.get('latency_s')}s · attempts={one.get('attempts')} · "
            f"out_tok={(one.get('usage') or {}).get('completion_tokens', '?')}"
        )
        for f in SEO_FIELDS:
            v = one.get(f) or "—"
            n = len(v) if isinstance(v, str) else 0
            st.markdown(f"**{f}** ({n} chars)")
            st.write(v)
        ents = one.get("entities") or []
        if ents:
            st.markdown(f"**entities ({len(ents)})**")
            st.dataframe(pd.DataFrame(ents)[["name", "type", "strength"]],
                         use_container_width=True, hide_index=True, height=240)

    with cols[1]:
        st.markdown("#### two-step")
        s1_lat = s1.get("latency_s"); s2_lat = s2.get("latency_s")
        combined = (float(s1_lat or 0) + float(s2_lat or 0)) if (s1_lat and s2_lat) else None
        st.caption(
            f"lang={s1.get('language')!r} · cat={s1.get('category')!r} · "
            f"S1={s1_lat}s + S2={s2_lat}s = {combined}s · "
            f"out_tok S1={(s1.get('usage') or {}).get('completion_tokens', '?')} "
            f"+ S2={(s2.get('usage') or {}).get('completion_tokens', '?')}"
        )
        for f in SEO_FIELDS:
            v = s2.get(f) or "—"
            n = len(v) if isinstance(v, str) else 0
            st.markdown(f"**{f}** ({n} chars)")
            st.write(v)
        ents = s1.get("entities") or []
        if ents:
            st.markdown(f"**entities ({len(ents)})**")
            st.dataframe(pd.DataFrame(ents)[["name", "type", "strength"]],
                         use_container_width=True, hide_index=True, height=240)

    a = _entity_set(one); b = _entity_set(s1)
    only_one = sorted(a - b); only_two = sorted(b - a); both = sorted(a & b)
    jacc = (len(a & b) / len(a | b)) if (a or b) else 0.0
    st.markdown("---")
    st.markdown(f"**Encje — Jaccard = {jacc:.3f}, intersection = {len(both)}, "
                f"|one|={len(a)} |two|={len(b)}**")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Tylko one-step ({len(only_one)})**")
    c1.write(only_one[:50] or "—")
    c2.markdown(f"**Tylko two-step ({len(only_two)})**")
    c2.write(only_two[:50] or "—")
    c3.markdown(f"**Wspólne ({len(both)})**")
    c3.write(both[:50] or "—")
