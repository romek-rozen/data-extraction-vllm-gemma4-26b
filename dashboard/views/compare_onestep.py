"""Widok porównawczy one-step vs two-step.

Skanuje `final_results/` w poszukiwaniu runów zawierających `onestep.jsonl`
(produkty `scripts/compare_onestep_vs_twostep.py`). Per run renderuje:
- KPI speed (wall time z compare_meta.json, latency, tokens)
- Quality (language/category match, Jaccard encji)
- Tabela per-URL z różnicami
- Eyeball: side-by-side title/meta/h1/summary + diff encji dla wybranego URL.

Niezależny od `load_results()` — ma własny minimalny scanner, żeby nie kolidować
z istniejącymi widokami two-step.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.data_loader import RESULTS_BASE

SEO_FIELDS = ["title", "meta_description", "h1", "article_summary"]


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
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    s = sorted(values)
    n = len(s)
    p = lambda q: s[min(int(q * n), n - 1)]
    return {"n": n, "mean": statistics.fmean(s), "p50": p(0.50), "p95": p(0.95)}


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
    onestep = payload["onestep"]
    step1 = payload["step1"]
    step2 = payload["step2"]
    meta = payload["meta"]

    sample_info = (
        f"random=True · seed={meta.get('seed', '?')}"
        if meta.get("random_sample") else "first-N (sorted)"
    )
    st.caption(
        f"Sample: **{sample_info}** · limit={meta.get('limit', '?')} · "
        f"concurrency={meta.get('concurrency', '?')}"
    )

    common = sorted(set(onestep) & set(step2))
    one_ok = sum(1 for r in onestep.values() if r.get("ok"))
    s1_ok = sum(1 for r in step1.values() if r.get("ok"))
    s2_ok = sum(1 for r in step2.values() if r.get("ok"))

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

    # ---------- header KPI ----------
    one_wall = float(meta.get("onestep_wall_s") or 0.0)
    two_wall = float(meta.get("twostep_wall_s") or 0.0)
    speedup_wall = (two_wall / one_wall) if one_wall > 0 else 0.0
    one_mean = _stat(one_lat)["mean"]
    two_mean = _stat(two_combined)["mean"]
    speedup_per_url = (two_mean / one_mean) if one_mean > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Common OK", len(common))
    c2.metric("Wall one-step (s)", f"{one_wall:.1f}")
    c3.metric("Wall two-step (s)", f"{two_wall:.1f}")
    c4.metric("Speedup wall", f"{speedup_wall:.2f}×")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("one-step lat mean", f"{one_mean:.2f}s")
    c2.metric("two-step combined mean", f"{two_mean:.2f}s")
    c3.metric("Speedup per-URL", f"{speedup_per_url:.2f}×")
    c4.metric("one-step OK", f"{one_ok}/{len(onestep)}")

    # ---------- speed table ----------
    st.subheader("Latency (s) — mean / p50 / p95")
    rows = []
    for label, lat in [("one-step", one_lat), ("two-step S1", s1_lat),
                       ("two-step S2", s2_lat), ("two-step combined", two_combined)]:
        s = _stat(lat)
        rows.append({"phase": label, "n": s["n"], "mean": round(s["mean"], 2),
                     "p50": round(s["p50"], 2), "p95": round(s["p95"], 2)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Tokens (mean per URL)")
    def _m(xs): return round(statistics.fmean(xs), 0) if xs else 0
    tok = pd.DataFrame([
        {"phase": "one-step", "prompt_mean": _m(one_in), "completion_mean": _m(one_out),
         "prompt_sum": sum(one_in), "completion_sum": sum(one_out)},
        {"phase": "two-step S1", "prompt_mean": _m(s1_in), "completion_mean": _m(s1_out),
         "prompt_sum": sum(s1_in), "completion_sum": sum(s1_out)},
        {"phase": "two-step S2", "prompt_mean": _m(s2_in), "completion_mean": _m(s2_out),
         "prompt_sum": sum(s2_in), "completion_sum": sum(s2_out)},
    ])
    st.dataframe(tok, use_container_width=True, hide_index=True)

    # ---------- quality ----------
    st.subheader("Quality (na common OK subset)")

    lang_match = cat_match = 0
    jaccard_vals: list[float] = []
    intersect_vals: list[int] = []
    one_counts: list[int] = []
    two_counts: list[int] = []
    field_lens = {f: {"one": [], "two": []} for f in SEO_FIELDS}
    missing = {f: {"one": 0, "two": 0} for f in SEO_FIELDS}
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

    n = max(len(per_url_rows), 1)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("language match", f"{lang_match}/{len(per_url_rows)}",
              f"{100*lang_match/n:.1f}%")
    c2.metric("category match", f"{cat_match}/{len(per_url_rows)}",
              f"{100*cat_match/n:.1f}%")
    c3.metric("entity Jaccard mean",
              f"{statistics.fmean(jaccard_vals) if jaccard_vals else 0:.3f}")
    c4.metric("intersection mean",
              f"{statistics.fmean(intersect_vals) if intersect_vals else 0:.1f}")

    seo_rows = []
    for f in SEO_FIELDS:
        seo_rows.append({
            "field": f,
            "one len mean": round(statistics.fmean(field_lens[f]["one"]), 0)
                if field_lens[f]["one"] else None,
            "two len mean": round(statistics.fmean(field_lens[f]["two"]), 0)
                if field_lens[f]["two"] else None,
            "missing one": missing[f]["one"],
            "missing two": missing[f]["two"],
        })
    st.markdown("**SEO meta — długości pól (znaki):**")
    st.dataframe(pd.DataFrame(seo_rows), use_container_width=True, hide_index=True)

    # ---------- per-URL table ----------
    st.subheader("Per-URL")
    if per_url_rows:
        df = pd.DataFrame(per_url_rows)
        only_diff = st.checkbox("Pokaż tylko URL z różnicami (lang/cat/Jaccard<0.5)", value=False)
        if only_diff:
            df = df[(~df["lang_match"]) | (~df["cat_match"]) | (df["jaccard"] < 0.5)]
        st.dataframe(df.drop(columns=["url_hash"]), use_container_width=True, hide_index=True)

        # ---------- eyeball ----------
        st.subheader("Eyeball — pojedynczy artykuł side-by-side")
        url_options = [(r["url"], r["url_hash"]) for r in per_url_rows]
        labels = [u for u, _ in url_options]
        sel_label = st.selectbox("URL", labels, key="onestep_eyeball")
        sel_hash = next(h for u, h in url_options if u == sel_label)
        _render_diff(sel_hash, onestep, step1, step2)

    st.caption(f"dir: `{payload['dir']}`")


def _render_diff(url_hash: str, onestep: dict, step1: dict, step2: dict):
    one = onestep.get(url_hash) or {}
    s1 = step1.get(url_hash) or {}
    s2 = step2.get(url_hash) or {}

    cols = st.columns(2)
    with cols[0]:
        st.markdown("#### one-step")
        st.caption(f"lang={one.get('language')!r} · cat={one.get('category')!r} · "
                   f"lat={one.get('latency_s')}s · attempts={one.get('attempts')}")
        for f in SEO_FIELDS:
            st.markdown(f"**{f}**")
            st.write(one.get(f) or "—")
        ents = one.get("entities") or []
        if ents:
            st.markdown(f"**entities ({len(ents)})**")
            st.dataframe(pd.DataFrame(ents)[["name", "type", "strength"]],
                         use_container_width=True, hide_index=True, height=240)

    with cols[1]:
        st.markdown("#### two-step")
        st.caption(f"lang={s1.get('language')!r} · cat={s1.get('category')!r} · "
                   f"S1 lat={s1.get('latency_s')}s · S2 lat={s2.get('latency_s')}s")
        for f in SEO_FIELDS:
            st.markdown(f"**{f}**")
            st.write(s2.get(f) or "—")
        ents = s1.get("entities") or []
        if ents:
            st.markdown(f"**entities ({len(ents)})**")
            st.dataframe(pd.DataFrame(ents)[["name", "type", "strength"]],
                         use_container_width=True, hide_index=True, height=240)

    a = _entity_set(one); b = _entity_set(s1)
    only_one = sorted(a - b); only_two = sorted(b - a); both = sorted(a & b)
    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Tylko one-step ({len(only_one)})**")
    c1.write(only_one[:50] or "—")
    c2.markdown(f"**Tylko two-step ({len(only_two)})**")
    c2.write(only_two[:50] or "—")
    c3.markdown(f"**Wspólne ({len(both)})**")
    c3.write(both[:50] or "—")
