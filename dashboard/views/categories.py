"""Widok kategorii artykułów — rozkład 42 enum (Cooking, Health, Medicine, junkey, ...).

Pokazuje:
- Rozkład kategorii per run (bar chart + tabela %)
- Cross-run pivot (pokrycie kategorii w runach)
- Top domeny per kategoria (kto pisze o czym)
- Junkey rate — domeny z dużym % junkey (sygnał: indeks/tag pages)
- Drill-down: wybierz kategorię → lista URL w tej kategorii
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render(filters: dict, data: dict):
    st.title("Kategorie artykułów (Step 1)")
    st.caption(
        "Kategoria z Step 1 (jeden z 42 enum). Klucz w danych: `category` w `entity_layer.jsonl`. "
        "**junkey** = strona-śmiec (taxonomy/index/404/login) — tracking ważny dla budżetu pipeline'u."
    )

    s1 = filters["step1"]
    if s1.empty:
        st.info("Brak Step 1 po filtrach.")
        return

    if "category" not in s1.columns:
        st.warning("Brak kolumny `category` w danych Step 1.")
        return

    df = s1[["run", "category", "url", "domain", "language"]].copy()
    df["category"] = df["category"].fillna("(brak)")

    # ---------- KPI ----------
    n_total = len(df)
    n_junkey = int((df["category"] == "junkey").sum())
    n_other = int((df["category"] == "Other themes").sum())
    n_unique_cats = df["category"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Artykułów (po filtrach)", n_total)
    c2.metric("Unikatowych kategorii", n_unique_cats)
    c3.metric("junkey", n_junkey,
              delta=f"{100*n_junkey/n_total:.1f}%" if n_total else None,
              delta_color="inverse")
    c4.metric("Other themes", n_other,
              delta=f"{100*n_other/n_total:.1f}%" if n_total else None,
              delta_color="inverse")

    # ---------- rozkład per run ----------
    st.subheader("Rozkład kategorii per run")

    cat_run = (
        df.groupby(["run", "category"]).size().reset_index(name="count")
    )
    totals = cat_run.groupby("run")["count"].transform("sum")
    cat_run["pct"] = (cat_run["count"] / totals * 100).round(2)

    # Bar chart - top 20 kategorii agregując po wszystkich runach
    top_cats = (
        cat_run.groupby("category")["count"].sum()
        .sort_values(ascending=False).head(25).index.tolist()
    )
    pivot = (
        cat_run[cat_run["category"].isin(top_cats)]
        .pivot(index="category", columns="run", values="count")
        .fillna(0).astype(int)
        .loc[top_cats]
    )
    st.bar_chart(pivot, use_container_width=True, height=400)

    st.markdown("##### Tabela counts + %")
    cat_run_sorted = cat_run.sort_values(["run", "count"], ascending=[True, False])
    st.dataframe(cat_run_sorted, use_container_width=True, hide_index=True, height=380)

    # ---------- coverage matrix (cross-run) ----------
    if len(filters["runs"]) >= 2:
        st.subheader("Cross-run pivot — pokrycie kategorii")
        st.caption("Liczba artykułów w danej kategorii × run. Puste = kategoria nieobecna w runie.")
        pv = (
            cat_run.pivot(index="category", columns="run", values="count")
            .fillna(0).astype(int)
            .sort_values(by=cat_run["run"].iloc[0] if not cat_run.empty else cat_run.columns[0],
                         ascending=False)
        )
        st.dataframe(pv, use_container_width=True, height=420)

    # ---------- top domeny per kategoria ----------
    st.subheader("Top domeny per kategoria")
    st.caption("Które domeny dominują w danej kategorii (top 5 per kategoria).")
    cats_for_select = sorted(df["category"].unique().tolist())
    sel_cat = st.selectbox("Kategoria", cats_for_select, key="cat_top_domains",
                           index=cats_for_select.index("junkey")
                           if "junkey" in cats_for_select else 0)
    cat_subset = df[df["category"] == sel_cat]
    if not cat_subset.empty:
        dom_counts = (
            cat_subset.groupby("domain").size().reset_index(name="count")
            .sort_values("count", ascending=False).head(20)
        )
        dom_counts["pct_of_cat"] = (dom_counts["count"] / len(cat_subset) * 100).round(1)
        st.dataframe(dom_counts, use_container_width=True, hide_index=True)

    # ---------- junkey rate per domain ----------
    st.subheader("⚠️ Junkey rate per domena")
    st.caption(
        "Domeny gdzie wysoki % URL klasyfikowanych jako junkey. "
        "Sygnał: domena to głównie strony archiwum/tagów/indeksów, "
        "albo problem z extraction (trafilatura nie złapała artykułu)."
    )
    if "domain" in df.columns:
        dom_stat = df.groupby("domain").agg(
            total=("url", "count"),
            junkey=("category", lambda s: (s == "junkey").sum()),
        ).reset_index()
        dom_stat = dom_stat[dom_stat["total"] >= 3]  # tylko domeny z ≥3 URL
        dom_stat["junkey_rate"] = (dom_stat["junkey"] / dom_stat["total"] * 100).round(1)
        dom_stat = dom_stat.sort_values("junkey_rate", ascending=False)
        st.dataframe(dom_stat.head(40), use_container_width=True, hide_index=True)

    # ---------- drill-down: URL-e per kategoria ----------
    st.subheader("URL-e per kategoria")
    sel_cat2 = st.selectbox("Kategoria do podglądu", cats_for_select,
                            key="cat_drill", index=0)
    subset = df[df["category"] == sel_cat2]
    st.caption(f"{len(subset)} artykułów w kategorii **{sel_cat2}**")
    st.dataframe(
        subset[["run", "domain", "language", "url"]].head(500),
        use_container_width=True, hide_index=True, height=400,
    )

    # ---------- rozkład języków per kategoria ----------
    st.subheader("Rozkład języków per kategoria (top 15 kategorii)")
    if "language" in df.columns:
        top_15 = (
            df.groupby("category").size().sort_values(ascending=False).head(15).index.tolist()
        )
        lang_cat = (
            df[df["category"].isin(top_15)]
            .groupby(["category", "language"]).size().unstack(fill_value=0)
            .loc[top_15]
        )
        st.dataframe(lang_cat, use_container_width=True, height=380)
