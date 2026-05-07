"""Eksplorator artykułów — URL → encje + meta SEO."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

SEO_LIMITS = {"title": 70, "meta_description": 160, "h1": 100, "article_summary": 400}


def render(filters: dict, data: dict):
    st.title("Eksplorator artykułów")

    s1 = filters["step1"]
    s2 = filters["step2"]

    if s1.empty:
        st.info("Brak Step 1 po filtrach.")
        return

    cols = ["run", "domain", "url", "category", "language", "entities_count", "latency_s", "ok"]
    cols = [c for c in cols if c in s1.columns]
    table = s1[cols].copy()

    st.subheader(f"Artykuły ({len(table)})")
    st.dataframe(table, use_container_width=True, hide_index=True, height=300)

    # Selectbox — URL
    options = [f"{r['run']} · {r['domain']} · {r['url']}" for _, r in s1.iterrows()]
    if not options:
        return
    selected = st.selectbox("Wybierz artykuł", options, index=0)
    sel_idx = options.index(selected)
    row1 = s1.iloc[sel_idx]

    url_hash = row1.get("url_hash")
    run = row1.get("run")
    row2 = pd.Series(dtype=object)
    if not s2.empty and url_hash and run:
        m = s2[(s2["url_hash"] == url_hash) & (s2["run"] == run)]
        if not m.empty:
            row2 = m.iloc[0]

    st.markdown(f"### {row1.get('domain', '')} — `{row1.get('url', '')}`")
    st.link_button("Otwórz URL", row1.get("url", "#"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Kategoria", row1.get("category", "—"))
    c2.metric("Język", row1.get("language", "—"))
    c3.metric("Encji", int(row1.get("entities_count", 0) or 0))
    c4.metric("Latencja S1 (s)", round(float(row1.get("latency_s", 0) or 0), 2))

    # Meta SEO (Step 2)
    if not row2.empty:
        st.subheader("Meta SEO (Step 2)")
        for f, lim in SEO_LIMITS.items():
            v = row2.get(f, "")
            ln = len(v) if isinstance(v, str) else 0
            warn = " ⚠️" if ln > lim else ""
            st.markdown(f"**{f}** ({ln}/{lim}{warn})")
            st.write(v)
        st.caption(f"Latencja S2: {round(float(row2.get('latency_s', 0) or 0), 2)}s")
    else:
        st.info("Brak Step 2 dla tego artykułu.")

    # Encje
    ents = row1.get("entities") or []
    if ents:
        st.subheader(f"Encje ({len(ents)})")
        ent_df = pd.DataFrame(ents)
        if "metadata" in ent_df.columns:
            ent_df["metadata"] = ent_df["metadata"].apply(
                lambda m: json.dumps(m, ensure_ascii=False) if isinstance(m, dict) else ""
            )
        st.dataframe(ent_df, use_container_width=True, hide_index=True, height=300)

    # Surowy JSON
    with st.expander("Raw Step 1 JSON"):
        st.json(row1.dropna().to_dict())
    if not row2.empty:
        with st.expander("Raw Step 2 JSON"):
            st.json(row2.dropna().to_dict())
