"""Porównanie ≥2 runów side-by-side."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from dashboard.data_loader import explode_entities

SEO_FIELDS = ["title", "meta_description", "h1", "article_summary"]


def render(filters: dict, data: dict):
    st.title("Porównanie runów")

    runs = filters["runs"]
    s1 = filters["step1"]
    s2 = filters["step2"]

    if len(runs) < 2:
        st.info("Wybierz co najmniej 2 runy w sidebar (filtr Run).")
        return

    # KPI side-by-side
    rows = []
    for run in runs:
        s1r = s1[s1["run"] == run] if not s1.empty else s1
        s2r = s2[s2["run"] == run] if not s2.empty else s2
        ents = explode_entities(s1r)
        row = {
            "run": run,
            "Step1 OK": int(s1r["ok"].sum()) if "ok" in s1r else len(s1r),
            "Step1 N": len(s1r),
            "Step2 OK": int(s2r["ok"].sum()) if "ok" in s2r else len(s2r),
            "Step2 N": len(s2r),
            "Med. lat S1": round(s1r["latency_s"].median(), 2) if "latency_s" in s1r and not s1r.empty else None,
            "Med. lat S2": round(s2r["latency_s"].median(), 2) if "latency_s" in s2r and not s2r.empty else None,
            "Med. encji": int(s1r["entities_count"].median()) if "entities_count" in s1r and not s1r.empty else None,
            "Off-list encje": int(ents["off_list"].sum()) if not ents.empty else 0,
        }
        for f in SEO_FIELDS:
            col = f"{f}_len"
            if col in s2r.columns and not s2r.empty:
                row[f"{f} med."] = int(s2r[col].median())
        rows.append(row)
    st.subheader("KPI side-by-side")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Rozkład typów per run
    ents_all = explode_entities(s1)
    if not ents_all.empty:
        st.subheader("Top 25 typów encji per run")
        tc = (
            ents_all.groupby(["run", "type"]).size().reset_index(name="count")
        )
        top_types = (
            tc.groupby("type")["count"].sum().sort_values(ascending=False).head(25).index.tolist()
        )
        pivot = (
            tc[tc["type"].isin(top_types)]
            .pivot(index="type", columns="run", values="count")
            .fillna(0).astype(int)
            .loc[top_types]
        )
        st.bar_chart(pivot, use_container_width=True)
        st.dataframe(pivot, use_container_width=True)

    # Diff per URL — URLi obecne w >1 runie
    if not s1.empty:
        counts = s1.groupby("url_hash")["run"].nunique()
        common = counts[counts >= 2].index.tolist()
        st.subheader(f"Artykuły wspólne dla ≥2 runów ({len(common)})")
        if common:
            common_s1 = s1[s1["url_hash"].isin(common)][["url_hash", "url", "domain"]].drop_duplicates("url_hash")
            url_options = [f"{r.domain} · {r.url}" for r in common_s1.itertuples()]
            sel = st.selectbox("Artykuł do porównania", url_options)
            sel_idx = url_options.index(sel)
            url_hash = common_s1.iloc[sel_idx]["url_hash"]
            _render_diff(url_hash, s1, s2, runs)

    # Surowe metrics_delta.txt
    st.subheader("metrics_delta.txt")
    for run in runs:
        delta = data["metrics"].get(run, {}).get("metrics_delta.txt")
        if delta:
            with st.expander(f"{run}"):
                st.code(delta)


def _render_diff(url_hash: str, s1: pd.DataFrame, s2: pd.DataFrame, runs: list[str]):
    cols = st.columns(len(runs))
    for c, run in zip(cols, runs):
        with c:
            st.markdown(f"#### {run}")
            r1 = s1[(s1["url_hash"] == url_hash) & (s1["run"] == run)]
            if r1.empty:
                st.caption("brak Step 1")
                continue
            r1 = r1.iloc[0]
            st.caption(f"Kategoria: {r1.get('category', '—')} · {r1.get('language', '—')}")
            st.caption(f"Encji: {r1.get('entities_count', 0)} · lat: {round(float(r1.get('latency_s', 0) or 0), 2)}s")

            ents = r1.get("entities") or []
            if ents:
                st.dataframe(
                    pd.DataFrame(ents)[["name", "type", "strength"]] if ents else pd.DataFrame(),
                    use_container_width=True, hide_index=True, height=200,
                )

            r2 = s2[(s2["url_hash"] == url_hash) & (s2["run"] == run)]
            if not r2.empty:
                r2 = r2.iloc[0]
                for f in SEO_FIELDS:
                    v = r2.get(f, "")
                    st.markdown(f"**{f}**")
                    st.write(v)
