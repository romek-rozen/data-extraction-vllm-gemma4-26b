"""Run Summary — KPI + rozkłady (mirror summary.md)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data_loader import explode_entities

SEO_LIMITS = {"title": 70, "meta_description": 160, "h1": 100, "article_summary": 400}


def _kpi_row(s1: pd.DataFrame, s2: pd.DataFrame, total_s1_all: int, total_s2_all: int) -> dict:
    ok1 = int(s1["ok"].sum()) if "ok" in s1.columns else len(s1)
    ok2 = int(s2["ok"].sum()) if "ok" in s2.columns else len(s2)
    out = {
        "Step1 OK": f"{ok1} / {total_s1_all}" if total_s1_all else f"{ok1}",
        "Step2 OK": f"{ok2} / {total_s2_all}" if total_s2_all else f"{ok2}",
        "Med. lat. Step1 (s)": round(s1["latency_s"].median(), 2) if "latency_s" in s1 and not s1.empty else None,
        "Med. lat. Step2 (s)": round(s2["latency_s"].median(), 2) if "latency_s" in s2 and not s2.empty else None,
        "Med. encji/art": int(s1["entities_count"].median()) if "entities_count" in s1 and not s1.empty else None,
        "Med. prompt tok S1": int(s1["prompt_tokens"].median()) if "prompt_tokens" in s1 and not s1.empty else None,
        "Med. compl. tok S1": int(s1["completion_tokens"].median()) if "completion_tokens" in s1 and not s1.empty else None,
    }
    return out


def render(filters: dict, data: dict):
    st.title("Run Summary")

    s1 = filters["step1"]
    s2 = filters["step2"]
    runs = filters["runs"]

    if s1.empty and s2.empty:
        st.info("Brak danych po filtrach.")
        return

    # KPI per run
    rows = []
    for run in runs:
        s1r = s1[s1["run"] == run] if not s1.empty else s1
        s2r = s2[s2["run"] == run] if not s2.empty else s2
        total_s1 = len(data["step1"][data["step1"]["run"] == run]) if not data["step1"].empty else 0
        total_s2 = len(data["step2"][data["step2"]["run"] == run]) if not data["step2"].empty else 0
        kpi = {"run": run, **_kpi_row(s1r, s2r, total_s1, total_s2)}
        rows.append(kpi)
    st.subheader("KPI per run")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Latencja Step 1 — histogram
    if not s1.empty and "latency_s" in s1.columns:
        st.subheader("Latencja Step 1 (s)")
        st.bar_chart(
            s1.groupby("run")["latency_s"].apply(list).to_dict(),
            use_container_width=True,
        ) if False else st.plotly_chart(_hist(s1, "latency_s", "run"), use_container_width=True)

    # Top kategorie artykułów
    if not s1.empty and "category" in s1.columns:
        st.subheader("Top kategorie artykułów")
        cat_counts = (
            s1.groupby(["run", "category"]).size().reset_index(name="count")
            .sort_values("count", ascending=False)
        )
        top = cat_counts.head(50)
        st.dataframe(top, use_container_width=True, hide_index=True)

    # Rozkład typów encji
    ents = explode_entities(s1)
    if not ents.empty:
        st.subheader("Rozkład typów encji")
        type_counts = (
            ents.groupby(["run", "type"]).size().reset_index(name="count")
        )
        total_per_run = type_counts.groupby("run")["count"].transform("sum")
        type_counts["pct"] = (type_counts["count"] / total_per_run * 100).round(2)
        type_counts = type_counts.sort_values(["run", "count"], ascending=[True, False])
        st.dataframe(type_counts, use_container_width=True, hide_index=True, height=400)

    # Długości pól SEO
    if not s2.empty:
        st.subheader("Długości pól SEO")
        rows = []
        for run in runs:
            s2r = s2[s2["run"] == run]
            if s2r.empty:
                continue
            for f, lim in SEO_LIMITS.items():
                col = f"{f}_len"
                if col in s2r.columns and not s2r[col].empty:
                    rows.append({
                        "run": run,
                        "pole": f,
                        "limit": lim,
                        "median": int(s2r[col].median()),
                        "p95": int(s2r[col].quantile(0.95)),
                        "max": int(s2r[col].max()),
                    })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Surowe summary.md
    st.subheader("Raw summary.md")
    for run in runs:
        if run in data["summaries"]:
            with st.expander(f"summary.md — {run}"):
                st.markdown(data["summaries"][run])


def _hist(df, col, color):
    import plotly.express as px
    return px.histogram(df, x=col, color=color, nbins=40, barmode="overlay", opacity=0.6)
