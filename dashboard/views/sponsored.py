"""Widok Sponsored — analiza wykrycia artykułów sponsorowanych z four-step v1.

Główne sekcje:
1. Per-domain sponsored ratio — kluczowy KPI (mapa rynku publishing).
2. Distribution sponsored vs editorial (binary).
3. Distribution po subtype (paid_placement / brand_mentions / advertorial).
4. Sponsored vs category (czy niektóre kategorie mają więcej sponsored?).
5. Eksplorator artykułów z filtrem po sponsored=true/false + subtype + justification.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _filter_by_run(df: pd.DataFrame, run_filter: list[str]) -> pd.DataFrame:
    if df.empty or not run_filter:
        return df
    return df[df["run"].isin(run_filter)]


def render(filters: dict | None = None, data: dict | None = None) -> None:
    st.title("🎯 Sponsored Detection (four-step v1)")
    if data is None:
        st.error("Brak danych — uruchom najpierw pipeline.")
        return
    fs = data.get("fourstep", pd.DataFrame())

    if fs.empty:
        st.warning(
            "Brak danych z four-step pipeline. Uruchom pipeline:\n\n"
            "```bash\n"
            "python3 -u scripts/run_fourstep_v1.py --limit 1000 --random --tag v4_1000 --concurrency 6\n"
            "```\n\n"
            "Wynik trafia do `final_results/<ts>__fourstep_v1_<tag>/final.jsonl` "
            "(z polami `sponsored`, `sponsored_subtype`, `sponsored_justification`)."
        )
        return

    # Filtr runów
    runs_avail = sorted(fs["run"].unique().tolist())
    selected = st.sidebar.multiselect("Runy four-step", runs_avail, default=runs_avail)
    df = _filter_by_run(fs, selected)
    if df.empty:
        st.info("Wybierz co najmniej jeden run.")
        return

    # Filtr junk (zwykle chcemy non-junk)
    show_junk = st.sidebar.checkbox("Pokaż junk", value=False)
    if not show_junk:
        df = df[~df["is_junk"]]
    df_ok = df[df["ok"]] if "ok" in df.columns else df

    # ====== KPI cards ======
    st.markdown("### Liczby (na non-junk OK records)")
    n_total = len(df_ok)
    n_sponsored = int(df_ok["sponsored"].sum())
    n_editorial = n_total - n_sponsored
    pct = (n_sponsored / n_total * 100) if n_total else 0.0
    n_junk = int(df["is_junk"].sum()) if show_junk else int((fs[fs["run"].isin(selected)])["is_junk"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total artykułów", f"{n_total:,}")
    c2.metric("Sponsored (true)", f"{n_sponsored:,}", f"{pct:.1f}%")
    c3.metric("Editorial (false)", f"{n_editorial:,}", f"{100-pct:.1f}%")
    c4.metric("Junk (skip)", f"{n_junk:,}")

    st.divider()

    # ====== Per-domain ratio (główny KPI) ======
    st.markdown("### 📊 Per-domain sponsored ratio")
    st.caption(
        "Kluczowy biznesowy output: które domeny są content-farms / link-farmami "
        "(>50% sponsored), a które prawdziwymi redakcjami (<10%). "
        "Sortowanie po `sponsored%` pokazuje publisher quality."
    )
    if "domain" in df_ok.columns:
        agg = (
            df_ok.groupby("domain")
            .agg(
                n_articles=("url_hash", "count"),
                n_sponsored=("sponsored", "sum"),
            )
            .reset_index()
        )
        agg["sponsored_pct"] = (agg["n_sponsored"] / agg["n_articles"] * 100).round(1)
        agg["n_editorial"] = agg["n_articles"] - agg["n_sponsored"]
        agg = agg[["domain", "n_articles", "n_sponsored", "n_editorial", "sponsored_pct"]]
        agg = agg.sort_values(["sponsored_pct", "n_articles"], ascending=[False, False])

        # Filtr min liczba artykułów (drobne domeny zaszumiają)
        min_n = st.slider(
            "Min liczba artykułów per domena (filtr szumu)",
            min_value=1, max_value=max(int(agg["n_articles"].max()), 1),
            value=min(3, int(agg["n_articles"].max())),
        )
        agg_filtered = agg[agg["n_articles"] >= min_n]

        st.dataframe(
            agg_filtered,
            hide_index=True,
            use_container_width=True,
            column_config={
                "sponsored_pct": st.column_config.ProgressColumn(
                    "Sponsored %", min_value=0, max_value=100, format="%.1f%%"
                ),
                "n_articles": st.column_config.NumberColumn("Total", format="%d"),
                "n_sponsored": st.column_config.NumberColumn("Sponsored", format="%d"),
                "n_editorial": st.column_config.NumberColumn("Editorial", format="%d"),
            },
        )

    st.divider()

    # ====== Subtype breakdown ======
    st.markdown("### 🏷️ Sponsored subtypes")
    if n_sponsored > 0:
        sub = df_ok[df_ok["sponsored"]].copy()
        sub["sponsored_subtype"] = sub["sponsored_subtype"].fillna("(null)").replace({"": "(null)"})
        cnt = sub["sponsored_subtype"].value_counts().reset_index()
        cnt.columns = ["subtype", "count"]
        cnt["pct"] = (cnt["count"] / cnt["count"].sum() * 100).round(1)

        col_a, col_b = st.columns([1, 2])
        with col_a:
            st.dataframe(cnt, hide_index=True, use_container_width=True)
        with col_b:
            st.bar_chart(cnt.set_index("subtype")["count"])
    else:
        st.info("Brak sponsored w wybranym sample'u.")

    st.divider()

    # ====== Sponsored per category ======
    st.markdown("### 📂 Sponsored per category")
    st.caption(
        "Rozkład sponsored % po kategoriach artykułów. Niektóre branże (finanse, "
        "ubezpieczenia, medycyna) tradycyjnie mają więcej paid content."
    )
    if "category" in df_ok.columns and not df_ok["category"].isna().all():
        cat = (
            df_ok.groupby("category")
            .agg(n=("url_hash", "count"), n_sponsored=("sponsored", "sum"))
            .reset_index()
        )
        cat["sponsored_pct"] = (cat["n_sponsored"] / cat["n"] * 100).round(1)
        cat = cat.sort_values(["sponsored_pct", "n"], ascending=[False, False])
        st.dataframe(
            cat,
            hide_index=True,
            use_container_width=True,
            column_config={
                "sponsored_pct": st.column_config.ProgressColumn(
                    "Sponsored %", min_value=0, max_value=100, format="%.1f%%"
                ),
                "n": st.column_config.NumberColumn("Total", format="%d"),
                "n_sponsored": st.column_config.NumberColumn("Sponsored", format="%d"),
            },
        )

    st.divider()

    # ====== Eksplorator artykułów ======
    st.markdown("### 🔎 Eksplorator artykułów")
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        flt_sponsored = st.selectbox("Sponsored?", ["wszystkie", "tylko sponsored", "tylko editorial"])
    with col2:
        subtypes_avail = ["(wszystkie)"] + sorted(
            [s for s in df_ok["sponsored_subtype"].dropna().unique().tolist() if s and s != "(null)"]
        )
        flt_subtype = st.selectbox("Subtype", subtypes_avail)
    with col3:
        flt_search = st.text_input("Szukaj w justification / title / domain", "")

    explorer = df_ok.copy()
    if flt_sponsored == "tylko sponsored":
        explorer = explorer[explorer["sponsored"]]
    elif flt_sponsored == "tylko editorial":
        explorer = explorer[~explorer["sponsored"]]
    if flt_subtype != "(wszystkie)":
        explorer = explorer[explorer["sponsored_subtype"] == flt_subtype]
    if flt_search:
        s = flt_search.lower()
        explorer = explorer[
            explorer["sponsored_justification"].fillna("").str.lower().str.contains(s)
            | explorer.get("title", pd.Series([""] * len(explorer))).fillna("").str.lower().str.contains(s)
            | explorer.get("domain", pd.Series([""] * len(explorer))).fillna("").str.lower().str.contains(s)
        ]

    cols_show = [
        c for c in [
            "domain", "category", "sponsored", "sponsored_subtype",
            "sponsored_justification", "title", "url",
        ] if c in explorer.columns
    ]
    st.caption(f"Wyświetlono {len(explorer)} artykułów (z {len(df_ok)})")
    st.dataframe(
        explorer[cols_show].reset_index(drop=True),
        hide_index=True,
        use_container_width=True,
        column_config={
            "url": st.column_config.LinkColumn("URL", display_text=r"https?://([^/]+)"),
            "sponsored_justification": st.column_config.TextColumn("Justification", width="large"),
        },
    )

    st.divider()

    # ====== Latency stats ======
    st.markdown("### ⏱️ Latency sponsored phase")
    if "latency_s" in fs.columns:
        # Tu bierzemy z fourstep df, bo final.jsonl ma latency wszystkich faz zmieszane.
        # Lepiej z timing.csv jeśli dostępne — to TODO.
        st.caption(
            "Uwaga: `latency_s` w final.jsonl to czas join'a, nie samej fazy sponsored. "
            "Per-stage latencje dostępne w `timing.csv` w katalogu runu."
        )
