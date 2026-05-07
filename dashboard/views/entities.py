"""Statystyki encyjne — typy, kategorie, strength, off-list, top encje."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data_loader import explode_entities, KNOWN_TYPES


def render(filters: dict, data: dict):
    st.title("Encje — typy, kategorie, jakość")

    s1 = filters["step1"]
    if s1.empty:
        st.info("Brak Step 1 po filtrach.")
        return

    ents = explode_entities(s1)
    if ents.empty:
        st.info("Brak encji.")
        return

    total = len(ents)
    off_list = int(ents["off_list"].sum())
    n_strong = int((ents["strength"] == "strong").sum())
    n_weak = int((ents["strength"] == "weak").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Wszystkie encje", total)
    c2.metric("Off-list (poza 51 typów)", off_list, delta=f"{off_list/total*100:.2f}%" if total else None)
    c3.metric("Strong", n_strong)
    c4.metric("Weak", n_weak)

    # Top typy
    st.subheader("Rozkład typów encji")
    type_counts = (
        ents.groupby(["run", "type"]).size().reset_index(name="count")
    )
    type_counts["pct_in_run"] = (
        type_counts["count"] / type_counts.groupby("run")["count"].transform("sum") * 100
    ).round(2)
    type_counts["off_list"] = ~type_counts["type"].isin(KNOWN_TYPES)
    type_counts = type_counts.sort_values(["run", "count"], ascending=[True, False])

    st.dataframe(type_counts, use_container_width=True, height=420, hide_index=True)

    # Off-list — szczegóły
    off = ents[ents["off_list"]]
    if not off.empty:
        st.subheader(f"⚠️ Off-list typy ({len(off)})")
        st.dataframe(
            off[["run", "url", "name", "type", "category"]].head(200),
            use_container_width=True,
            hide_index=True,
        )

    # Top encje (potencjalne hallucynacje)
    st.subheader("Top encje (group by name + type)")
    top_ents = (
        ents.groupby(["name", "type"]).size().reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(100)
    )
    st.dataframe(top_ents, use_container_width=True, hide_index=True, height=400)

    # Per kategoria high-level
    st.subheader("Per kategoria high-level (Azure)")
    cat_counts = (
        ents.groupby(["run", "category"]).size().reset_index(name="count")
        .sort_values(["run", "count"], ascending=[True, False])
    )
    st.dataframe(cat_counts, use_container_width=True, hide_index=True)

    # Strength per typ
    st.subheader("Strength per typ (top 20)")
    strength = (
        ents.groupby(["type", "strength"]).size().unstack(fill_value=0)
        .assign(total=lambda x: x.sum(axis=1))
        .sort_values("total", ascending=False)
        .head(20)
    )
    st.dataframe(strength, use_container_width=True)
