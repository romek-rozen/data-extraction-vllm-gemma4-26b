"""Widok Junk Analysis — które domeny mają najwięcej śmieciowych URL.

Łączy dwa źródła sygnału junkowego:
1. Four-step pipeline: `is_junk=True` w final.jsonl (binary classifier z guided_choice)
2. Two-step pipeline: `category="junkey"` w entity_layer.jsonl (Step 1 v6 z 41-enum)

Per-domain junk ratio jest kluczowym KPI:
- domeny z >50% junk = content farms / linkfarmy / WordPress instalacje pełne taxonomy pages
- domeny z <5% junk = porządne portale z dobrą strukturą URL
- pomiędzy = mix editorial + paginowane kategorie
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _collect_junk_records(data: dict) -> pd.DataFrame:
    """Zbuduj DataFrame z (run, url_hash, url, domain, is_junk, source).

    `source` = 'fourstep' (is_junk z classifier binary) lub 'twostep' (category=junkey w Step1).
    """
    frames = []
    fs = data.get("fourstep", pd.DataFrame())
    if not fs.empty and "domain" in fs.columns and "is_junk" in fs.columns:
        df = fs[["run", "url_hash", "url", "domain", "is_junk"]].copy()
        df["source"] = "fourstep"
        frames.append(df)

    s1 = data.get("step1", pd.DataFrame())
    if not s1.empty and "domain" in s1.columns and "category" in s1.columns:
        df = s1[["run", "url_hash", "url", "domain", "category"]].copy()
        df["is_junk"] = (df["category"] == "junkey")
        df = df.drop(columns=["category"])
        df["source"] = "twostep"
        # Jeśli ten sam (run, url_hash) wpadł też z fourstep (bo data_loader robi alias),
        # zostawiamy tylko jeden
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["run", "url_hash", "url", "domain", "is_junk", "source"])
    out = pd.concat(frames, ignore_index=True)
    # Dedup po (source, run, url_hash) — bo step1 zawiera fourstep alias z data_loader
    out = out.drop_duplicates(subset=["source", "run", "url_hash"])
    return out


def render(filters: dict | None = None, data: dict | None = None):
    st.title("🗑️ Junk Analysis — domeny ze śmieciem")
    if data is None:
        st.error("Brak danych — uruchom najpierw pipeline.")
        return

    df_all = _collect_junk_records(data)
    if df_all.empty:
        st.warning(
            "Brak danych. Uruchom either:\n"
            "- four-step `python3 scripts/run_fourstep_v1.py` → `final.jsonl` z polem `is_junk`\n"
            "- two-step (każda wersja) → `entity_layer.jsonl` z `category=junkey`"
        )
        return

    # Filtry
    runs_avail = sorted(df_all["run"].unique().tolist())
    selected_runs = st.sidebar.multiselect("Runy", runs_avail, default=runs_avail)
    sources_avail = sorted(df_all["source"].unique().tolist())
    selected_sources = st.sidebar.multiselect(
        "Źródło sygnału junku", sources_avail, default=sources_avail,
        help="fourstep = binary classifier z guided_choice (v4); twostep = category=junkey w Step1 v6"
    )
    df = df_all[df_all["run"].isin(selected_runs) & df_all["source"].isin(selected_sources)]
    if df.empty:
        st.info("Filtry usunęły wszystkie wiersze.")
        return

    # ====== KPI cards ======
    st.markdown("### Liczby")
    n_total = len(df)
    n_junk = int(df["is_junk"].sum())
    n_clean = n_total - n_junk
    n_domains = df["domain"].nunique() if "domain" in df.columns else 0
    pct = (n_junk / n_total * 100) if n_total else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total URL", f"{n_total:,}")
    c2.metric("Junk", f"{n_junk:,}", f"{pct:.1f}%")
    c3.metric("Non-junk", f"{n_clean:,}", f"{100-pct:.1f}%")
    c4.metric("Unikalnych domen", f"{n_domains:,}")

    st.divider()

    # ====== Per-domain junk ratio (główny KPI) ======
    st.markdown("### 📊 Per-domain junk ratio")
    st.caption(
        "Sortowanie po `junk%` desc pokazuje content farms / WordPressy z dużą ilością taxonomy pages. "
        "Sortowanie po `junk%` asc pokazuje porządne portale z dobrą strukturą URL."
    )
    if "domain" in df.columns:
        agg = (
            df.groupby("domain")
            .agg(n_articles=("url_hash", "count"), n_junk=("is_junk", "sum"))
            .reset_index()
        )
        agg["junk_pct"] = (agg["n_junk"] / agg["n_articles"] * 100).round(1)
        agg["n_clean"] = agg["n_articles"] - agg["n_junk"]
        agg = agg[["domain", "n_articles", "n_junk", "n_clean", "junk_pct"]]
        agg = agg.sort_values(["junk_pct", "n_articles"], ascending=[False, False])

        # Filtr min
        min_n = st.slider(
            "Min liczba URL per domena (filtr szumu)",
            min_value=1, max_value=max(int(agg["n_articles"].max()), 1),
            value=min(3, int(agg["n_articles"].max())),
        )
        agg_f = agg[agg["n_articles"] >= min_n]

        st.dataframe(
            agg_f,
            hide_index=True,
            use_container_width=True,
            column_config={
                "junk_pct": st.column_config.ProgressColumn(
                    "Junk %", min_value=0, max_value=100, format="%.1f%%"
                ),
                "n_articles": st.column_config.NumberColumn("Total", format="%d"),
                "n_junk": st.column_config.NumberColumn("Junk", format="%d"),
                "n_clean": st.column_config.NumberColumn("Non-junk", format="%d"),
            },
        )

    st.divider()

    # ====== Cross-source comparison ======
    if len(df["source"].unique()) >= 2:
        st.markdown("### 🔀 Cross-source comparison (fourstep vs twostep)")
        st.caption(
            "Czy obie metody zgadzają się co do śmiecia? Cele: precyzja klasyfikacji + walidacja."
        )
        # Pivot per (run, url_hash) — czy fourstep i twostep zgadzają się
        wide = df.pivot_table(
            index=["url_hash", "domain"],
            columns="source",
            values="is_junk",
            aggfunc="first",
        ).reset_index()

        if "fourstep" in wide.columns and "twostep" in wide.columns:
            both_present = wide.dropna(subset=["fourstep", "twostep"])
            if not both_present.empty:
                both_present["fourstep"] = both_present["fourstep"].astype(bool)
                both_present["twostep"] = both_present["twostep"].astype(bool)

                tt = (both_present["fourstep"] & both_present["twostep"]).sum()
                tf = (both_present["fourstep"] & ~both_present["twostep"]).sum()
                ft = (~both_present["fourstep"] & both_present["twostep"]).sum()
                ff = (~both_present["fourstep"] & ~both_present["twostep"]).sum()

                cm = pd.DataFrame(
                    [[tt, tf], [ft, ff]],
                    index=["fourstep=junk", "fourstep=clean"],
                    columns=["twostep=junk", "twostep=clean"],
                )
                st.write("**Confusion matrix (URL z oboma sygnałami):**")
                st.dataframe(cm, use_container_width=True)
                st.caption(
                    f"Zgodność: {(tt+ff)}/{len(both_present)} "
                    f"({(tt+ff)/max(len(both_present),1)*100:.1f}%). "
                    f"fourstep łapie więcej (bo URL signals): {tf} URL flag'owanych tylko przez fourstep. "
                    f"twostep łapie więcej (bo widzi cały tekst v6): {ft} URL flag'owanych tylko przez twostep."
                )
        st.divider()

    # ====== Eksplorator junk URL ======
    st.markdown("### 🔎 Eksplorator junk URL")
    junk_only = df[df["is_junk"]].copy()
    if junk_only.empty:
        st.info("Brak junku w wybranym filtrze.")
    else:
        domains_avail = ["(wszystkie)"] + sorted(junk_only["domain"].unique().tolist())
        flt_dom = st.selectbox("Filtr domeny", domains_avail)
        flt_search = st.text_input("Szukaj w URL", "")

        explorer = junk_only.copy()
        if flt_dom != "(wszystkie)":
            explorer = explorer[explorer["domain"] == flt_dom]
        if flt_search:
            s = flt_search.lower()
            explorer = explorer[explorer["url"].fillna("").str.lower().str.contains(s)]

        st.caption(f"Wyświetlono {len(explorer)} junk URL (z {len(junk_only)} total)")
        st.dataframe(
            explorer[["domain", "url", "source", "run"]].reset_index(drop=True),
            hide_index=True,
            use_container_width=True,
            column_config={
                "url": st.column_config.LinkColumn("URL", display_text=r"https?://([^?]+)"),
                "source": st.column_config.TextColumn("Źródło sygnału"),
            },
        )
