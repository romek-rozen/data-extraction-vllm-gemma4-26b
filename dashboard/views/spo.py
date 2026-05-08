"""Widok 🕸️ SPO / Knowledge Graph — analiza wyników run_spo_v1.py.

Niezależny od wspólnego data_loader (SPO ma swój `entities_spo.jsonl`, nie pasuje
do schematu fourstep / twostep). Czyta `final_results/<run>/{final.jsonl, run_meta.json, SUMMARY.md}`
bezpośrednio dla runów oznaczonych `__spo_v1`.

Sekcje:
- Run picker
- Top metrics
- Predicate distribution (top 50 bar chart)
- Predicate clustering hint (Levenshtein <=2 → kandydaci do zlewki)
- Top central entities
- Entity type × is_central
- Sample browser (artykuły + ich entities + triples)
- Predicate length histogram
- Raw SUMMARY.md (jeśli istnieje)
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_BASE = ROOT / "final_results"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _list_spo_runs() -> list[Path]:
    if not RESULTS_BASE.exists():
        return []
    runs = []
    for d in sorted(RESULTS_BASE.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        if (d / "entities_spo.jsonl").exists() or "spo_v1" in d.name:
            runs.append(d)
    return runs


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            ins = curr[j-1] + 1
            dele = prev[j] + 1
            sub = prev[j-1] + (ca != cb)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]


def _cluster_predicates(pred_counter: Counter, max_distance: int = 2, min_count: int = 5) -> list[tuple[str, list[tuple[str, int]]]]:
    """Greedy clustering — dla każdego TOP-N predicate znajdź podobne (Levenshtein ≤ max_distance)."""
    items = [(p, c) for p, c in pred_counter.most_common(200) if c >= min_count]
    used = set()
    clusters = []
    for i, (p, c) in enumerate(items):
        if p in used:
            continue
        cluster = [(p, c)]
        used.add(p)
        for q, cc in items[i+1:]:
            if q in used:
                continue
            if _levenshtein(p, q) <= max_distance:
                cluster.append((q, cc))
                used.add(q)
        if len(cluster) > 1:
            clusters.append((p, cluster))
    return clusters


def render(filters: dict | None = None, data: dict | None = None):
    st.title("🕸️ SPO / Knowledge Graph")
    st.markdown("Analiza wyników `run_spo_v1.py` — encje kanoniczne (z `is_central`) + free-form SPO triples (bootstrap discovery dla closed vocab v2).")

    runs = _list_spo_runs()
    if not runs:
        st.warning(f"Brak runów SPO w `{RESULTS_BASE}`. Uruchom: `python3 scripts/run_spo_v1.py --limit 5 --concurrency 4 --tag spo_smoke`")
        return

    run_names = [r.name for r in runs]
    selected_name = st.selectbox("Wybierz run SPO", run_names, index=0)
    run_dir = next(r for r in runs if r.name == selected_name)

    final_path = run_dir / "final.jsonl"
    meta_path = run_dir / "run_meta.json"
    summary_path = run_dir / "SUMMARY.md"

    if not final_path.exists():
        st.warning(f"Brak `{final_path.name}` w {run_dir.name}")
        return

    records_raw = _read_jsonl(final_path)
    by_hash: dict[str, dict] = {}
    for r in records_raw:
        h = r.get("url_hash")
        if h:
            by_hash[h] = r
    records = list(by_hash.values())
    run_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    n_total = len(records)
    n_junk = sum(1 for r in records if r.get("is_junk"))
    n_ok = sum(1 for r in records if r.get("ok") and not r.get("is_junk"))
    n_fail = sum(1 for r in records if not r.get("ok"))

    # Aggregacje
    pred_counter: Counter = Counter()
    central_counter: Counter = Counter()
    type_overall: Counter = Counter()
    type_central: Counter = Counter()
    triples_total = 0
    entities_total = 0
    s_unmatched_total = 0

    for r in records:
        if r.get("is_junk") or not r.get("ok"):
            continue
        ents = r.get("entities", [])
        triples = r.get("triples", [])
        entities_total += len(ents)
        triples_total += len(triples)
        s_unmatched_total += r.get("triples_s_unmatched", 0)
        seen_central = set()
        for e in ents:
            t = e.get("type", "?")
            type_overall[t] += 1
            if e.get("is_central"):
                type_central[t] += 1
                nm = e.get("name", "?")
                if nm not in seen_central:
                    central_counter[nm] += 1
                    seen_central.add(nm)
        for tr in triples:
            pred_counter[tr.get("p", "")] += 1

    # ───── Top metrics
    st.subheader("📊 Run metrics")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Articles", n_total)
    c2.metric("Junk", f"{n_junk} ({n_junk/max(n_total,1)*100:.1f}%)")
    c3.metric("Non-junk OK", n_ok)
    c4.metric("Triples", f"{triples_total} (avg {triples_total/max(n_ok,1):.1f})")
    c5.metric("Unique predicates", len(pred_counter))

    if run_meta:
        wall = run_meta.get("wall_s", 0)
        st.caption(f"Wall: {wall:.0f}s ({wall/3600:.2f}h) · concurrency={run_meta.get('concurrency', '?')} · seed={run_meta.get('seed', '?')}")

    # ───── Predicate distribution
    st.subheader("🔤 Predicate distribution (top 50)")
    if pred_counter:
        top = pred_counter.most_common(50)
        df_p = pd.DataFrame(top, columns=["predicate", "count"])
        df_p["pct"] = df_p["count"] / triples_total * 100
        st.bar_chart(df_p.set_index("predicate")["count"], height=400)
        with st.expander("Tabela top-200"):
            df_full = pd.DataFrame(pred_counter.most_common(200), columns=["predicate", "count"])
            df_full["pct"] = df_full["count"] / triples_total * 100
            st.dataframe(df_full, use_container_width=True, height=400)
    else:
        st.info("Brak triples w tym runie")

    # ───── Predicate clustering hint
    st.subheader("🧩 Predicate clustering hint (Levenshtein ≤ 2)")
    st.caption("Kandydaci do zlewki w closed vocab v2 — predykaty różniące się o ≤2 znaki (`founded by` vs `founded in`).")
    clusters = _cluster_predicates(pred_counter, max_distance=2, min_count=3)
    if clusters:
        rows = []
        for canonical, cluster in clusters[:50]:
            members = ", ".join(f"`{p}`({c})" for p, c in cluster)
            rows.append({"canonical": canonical, "members": members, "n_members": len(cluster),
                         "total_count": sum(c for _, c in cluster)})
        df_c = pd.DataFrame(rows)
        st.dataframe(df_c, use_container_width=True, height=300)
    else:
        st.info("Brak klastrów (potrzeba więcej danych)")

    # ───── Predicate length histogram
    st.subheader("📏 Predicate word-length distribution")
    word_len = Counter()
    for p, c in pred_counter.items():
        n_words = len(p.split())
        word_len[n_words] += c
    if word_len:
        df_w = pd.DataFrame(sorted(word_len.items()), columns=["n_words", "count"])
        df_w["pct"] = df_w["count"] / triples_total * 100
        st.dataframe(df_w, use_container_width=True)

    # ───── Top central entities
    st.subheader("⭐ Top 50 central entities (cross-article)")
    if central_counter:
        df_ce = pd.DataFrame(central_counter.most_common(50), columns=["name", "n_articles"])
        st.dataframe(df_ce, use_container_width=True, height=400)

    # ───── Type × is_central
    st.subheader("🎯 Entity type × is_central")
    rows = []
    for t in sorted(type_overall.keys(), key=lambda k: -type_overall[k]):
        total = type_overall[t]
        central = type_central.get(t, 0)
        rows.append({"type": t, "total": total, "central": central,
                     "pct_central": round(central/total*100, 2) if total else 0})
    df_t = pd.DataFrame(rows)
    st.dataframe(df_t, use_container_width=True, height=400)

    # ───── Triples grounding quality
    st.subheader("🔗 Triple grounding (subject ∈ entities)")
    grounded = triples_total - s_unmatched_total
    pct = grounded/max(triples_total,1)*100
    st.metric("Triples z s ∈ entities", f"{grounded} / {triples_total}", f"{pct:.2f}%")
    st.caption("Subject powinien być nazwą encji z listy entities (canonical match). Niski % = model halucynuje encje w triplets.")

    # ───── Sample browser
    st.subheader("🔍 Sample article browser")
    non_junk = [r for r in records if r.get("ok") and not r.get("is_junk") and (r.get("entities") or r.get("triples"))]
    if non_junk:
        n_show = st.slider("Liczba artykułów do pokazania", 1, min(50, len(non_junk)), value=10)
        import random as _rnd
        rng = _rnd.Random(42)
        sample = rng.sample(non_junk, min(n_show, len(non_junk)))
        for r in sample:
            with st.expander(f"📄 {r.get('url', '?')}  ·  {len(r.get('entities', []))} encji  ·  {len(r.get('triples', []))} triples"):
                ents = r.get("entities", [])
                if ents:
                    df_e = pd.DataFrame(ents)
                    st.markdown("**Entities:**")
                    st.dataframe(df_e, use_container_width=True)
                trs = r.get("triples", [])
                if trs:
                    df_tr = pd.DataFrame(trs)
                    st.markdown("**Triples:**")
                    st.dataframe(df_tr, use_container_width=True)

    # ───── Raw SUMMARY.md
    if summary_path.exists():
        with st.expander("📜 Raw SUMMARY.md (auto-generated)"):
            st.markdown(summary_path.read_text(encoding="utf-8"))
