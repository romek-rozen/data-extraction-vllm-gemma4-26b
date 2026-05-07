"""Sidebar filtry: run, kategoria, język, domena, only-OK."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_filters(
    step1: pd.DataFrame,
    step2: pd.DataFrame,
    runs: list[str],
    onestep: pd.DataFrame | None = None,
) -> dict:
    st.sidebar.markdown("### Filtry")

    selected_runs = st.sidebar.multiselect(
        "Run", runs, default=runs, help="Katalogi w final_results/"
    )

    s1 = step1[step1["run"].isin(selected_runs)] if not step1.empty else step1
    s2 = step2[step2["run"].isin(selected_runs)] if not step2.empty else step2
    os1 = (
        onestep[onestep["run"].isin(selected_runs)]
        if onestep is not None and not onestep.empty
        else (onestep if onestep is not None else pd.DataFrame())
    )

    only_ok = st.sidebar.checkbox("Tylko OK (ok==True)", value=True)
    if only_ok and "ok" in s1.columns:
        s1 = s1[s1["ok"] == True]  # noqa: E712
    if only_ok and "ok" in s2.columns:
        s2 = s2[s2["ok"] == True]  # noqa: E712
    if only_ok and not os1.empty and "ok" in os1.columns:
        os1 = os1[os1["ok"] == True]  # noqa: E712

    def _filter_os_by_url_hash(df: pd.DataFrame, hashes) -> pd.DataFrame:
        if df.empty or "url_hash" not in df.columns:
            return df
        return df[df["url_hash"].isin(hashes)]

    if not s1.empty and "category" in s1.columns:
        cats = sorted([c for c in s1["category"].dropna().unique().tolist() if c])
        selected_cats = st.sidebar.multiselect("Kategoria artykułu", cats, default=[])
        if selected_cats:
            s1 = s1[s1["category"].isin(selected_cats)]
            s2 = s2[s2["url_hash"].isin(s1["url_hash"])] if not s2.empty else s2
            if not os1.empty and "category" in os1.columns:
                os1 = os1[os1["category"].isin(selected_cats)]

    if not s1.empty and "language" in s1.columns:
        langs = sorted([l for l in s1["language"].dropna().unique().tolist() if l])
        selected_langs = st.sidebar.multiselect("Język", langs, default=[])
        if selected_langs:
            s1 = s1[s1["language"].isin(selected_langs)]
            s2 = s2[s2["url_hash"].isin(s1["url_hash"])] if not s2.empty else s2
            if not os1.empty and "language" in os1.columns:
                os1 = os1[os1["language"].isin(selected_langs)]

    if not s1.empty and "domain" in s1.columns:
        domains = sorted([d for d in s1["domain"].dropna().unique().tolist() if d])
        if len(domains) <= 200:
            selected_domains = st.sidebar.multiselect("Domena", domains, default=[])
            if selected_domains:
                s1 = s1[s1["domain"].isin(selected_domains)]
                s2 = s2[s2["url_hash"].isin(s1["url_hash"])] if not s2.empty else s2
                if not os1.empty and "domain" in os1.columns:
                    os1 = os1[os1["domain"].isin(selected_domains)]

    return {
        "step1": s1.reset_index(drop=True),
        "step2": s2.reset_index(drop=True),
        "onestep": os1.reset_index(drop=True) if not os1.empty else os1,
        "runs": selected_runs,
    }
