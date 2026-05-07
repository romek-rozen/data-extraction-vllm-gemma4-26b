"""Run Summary — KPI + rozkłady (mirror summary.md)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.data_loader import explode_entities

SEO_LIMITS = {"title": 70, "meta_description": 160, "h1": 100, "article_summary": 400}


def _kpi_row(
    s1: pd.DataFrame,
    s2: pd.DataFrame,
    total_s1_all: int,
    total_s2_all: int,
    os1: pd.DataFrame | None = None,
    total_os1_all: int = 0,
) -> dict:
    ok1 = int(s1["ok"].sum()) if "ok" in s1.columns else len(s1)
    ok2 = int(s2["ok"].sum()) if "ok" in s2.columns else len(s2)
    out = {
        "Step1 OK": f"{ok1} / {total_s1_all}" if total_s1_all else f"{ok1}",
        "Step2 OK": f"{ok2} / {total_s2_all}" if total_s2_all else f"{ok2}",
        "Med. lat. Step1 (s)": round(s1["latency_s"].median(), 2) if "latency_s" in s1 and not s1.empty else None,
        "Med. lat. Step2 (s)": round(s2["latency_s"].median(), 2) if "latency_s" in s2 and not s2.empty else None,
        "Med. encji/art (S1)": int(s1["entities_count"].median()) if "entities_count" in s1 and not s1.empty else None,
        "Med. prompt tok S1": int(s1["prompt_tokens"].median()) if "prompt_tokens" in s1 and not s1.empty else None,
        "Med. compl. tok S1": int(s1["completion_tokens"].median()) if "completion_tokens" in s1 and not s1.empty else None,
    }
    if os1 is not None and not os1.empty:
        ok_o = int(os1["ok"].sum()) if "ok" in os1.columns else len(os1)
        out.update({
            "OneStep OK": f"{ok_o} / {total_os1_all}" if total_os1_all else f"{ok_o}",
            "Med. lat. OneStep (s)": round(os1["latency_s"].median(), 2) if "latency_s" in os1 and not os1.empty else None,
            "Med. encji/art (OneStep)": int(os1["entities_count"].median()) if "entities_count" in os1 and not os1.empty else None,
            "Med. prompt tok OneStep": int(os1["prompt_tokens"].median()) if "prompt_tokens" in os1 and not os1.empty else None,
            "Med. compl. tok OneStep": int(os1["completion_tokens"].median()) if "completion_tokens" in os1 and not os1.empty else None,
        })
    else:
        out.update({
            "OneStep OK": "—",
            "Med. lat. OneStep (s)": None,
            "Med. encji/art (OneStep)": None,
            "Med. prompt tok OneStep": None,
            "Med. compl. tok OneStep": None,
        })
    return out


def render(filters: dict, data: dict):
    st.title("Run Summary")

    s1 = filters["step1"]
    s2 = filters["step2"]
    os1 = filters.get("onestep") if filters.get("onestep") is not None else pd.DataFrame()
    runs = filters["runs"]

    if s1.empty and s2.empty and (os1 is None or os1.empty):
        st.info("Brak danych po filtrach.")
        return

    data_os = data.get("onestep", pd.DataFrame())

    # KPI per run
    rows = []
    for run in runs:
        s1r = s1[s1["run"] == run] if not s1.empty else s1
        s2r = s2[s2["run"] == run] if not s2.empty else s2
        os1r = os1[os1["run"] == run] if not os1.empty else os1
        total_s1 = len(data["step1"][data["step1"]["run"] == run]) if not data["step1"].empty else 0
        total_s2 = len(data["step2"][data["step2"]["run"] == run]) if not data["step2"].empty else 0
        total_os = len(data_os[data_os["run"] == run]) if not data_os.empty else 0
        kpi = {"run": run, **_kpi_row(s1r, s2r, total_s1, total_s2, os1r, total_os)}
        rows.append(kpi)
    st.subheader("KPI per run")
    st.caption("Kolumny `OneStep ...` wypełnione tylko dla runów ze ścieżki `compare_onestep_vs_twostep.py` (mają `onestep.jsonl`).")
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

    # Długości pól SEO — Step 2 + One-step
    if not s2.empty or (not os1.empty):
        st.subheader("Długości pól SEO")
        rows = []
        for run in runs:
            for source_label, src_df in [("two-step S2", s2), ("one-step", os1)]:
                if src_df.empty:
                    continue
                src_r = src_df[src_df["run"] == run]
                if src_r.empty:
                    continue
                for f, lim in SEO_LIMITS.items():
                    col = f"{f}_len"
                    if col in src_r.columns and not src_r[col].empty:
                        rows.append({
                            "run": run,
                            "source": source_label,
                            "pole": f,
                            "limit": lim,
                            "median": int(src_r[col].median()),
                            "p95": int(src_r[col].quantile(0.95)),
                            "max": int(src_r[col].max()),
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
