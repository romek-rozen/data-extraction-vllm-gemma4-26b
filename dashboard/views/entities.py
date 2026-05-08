"""Statystyki encyjne — typy, kategorie, strength, off-list, top encje."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
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

    # Bar chart top 20 typów (sumarycznie po wszystkich runach)
    type_sum = (
        ents.groupby("type").size().reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(20)
    )
    type_sum["off_list"] = ~type_sum["type"].isin(KNOWN_TYPES)
    type_sum["pct"] = (type_sum["count"] / total * 100).round(2)
    fig_types = px.bar(
        type_sum, x="type", y="count",
        color="off_list", color_discrete_map={False: "#1f77b4", True: "#d62728"},
        text="pct", hover_data=["pct"],
        title="Top 20 typów encji (czerwony = off-list, poza 51 Azure)",
    )
    fig_types.update_traces(texttemplate="%{text}%", textposition="outside")
    fig_types.update_layout(xaxis_tickangle=-45, height=420, margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig_types, use_container_width=True)

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
    top_n = st.slider("Ile top encji pokazać na wykresie", 10, 50, 30, key="top_ents_n")
    top_ents = (
        ents.groupby(["name", "type"]).size().reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    top_ents_chart = top_ents.head(top_n).copy()
    top_ents_chart["label"] = top_ents_chart["name"] + "  (" + top_ents_chart["type"] + ")"
    fig_ents = px.bar(
        top_ents_chart, x="count", y="label", color="type",
        orientation="h",
        title=f"Top {top_n} encji — najczęściej wybierane (poziomy bar chart)",
    )
    fig_ents.update_layout(
        height=max(400, top_n * 22),
        yaxis=dict(autorange="reversed", title=""),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig_ents, use_container_width=True)
    st.dataframe(top_ents.head(200), use_container_width=True, hide_index=True, height=400)

    # Per kategoria high-level
    st.subheader("Per kategoria high-level (Azure)")
    cat_sum = (
        ents.groupby("category").size().reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    cat_sum["pct"] = (cat_sum["count"] / total * 100).round(2)
    fig_cats = px.bar(
        cat_sum, x="category", y="count", text="pct",
        title="Rozkład kategorii high-level (Azure — 11 grup)",
        color="category",
    )
    fig_cats.update_traces(texttemplate="%{text}%", textposition="outside")
    fig_cats.update_layout(
        height=400, xaxis_tickangle=-30, showlegend=False,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig_cats, use_container_width=True)
    cat_counts = (
        ents.groupby(["run", "category"]).size().reset_index(name="count")
        .sort_values(["run", "count"], ascending=[True, False])
    )
    st.dataframe(cat_counts, use_container_width=True, hide_index=True)

    # Strength per typ — stacked bar
    st.subheader("Strength per typ (top 20)")
    strength = (
        ents.groupby(["type", "strength"]).size().unstack(fill_value=0)
        .assign(total=lambda x: x.sum(axis=1))
        .sort_values("total", ascending=False)
        .head(20)
    )
    strength_long = (
        strength.drop(columns=["total"]).reset_index()
        .melt(id_vars="type", var_name="strength", value_name="count")
    )
    type_order = strength.index.tolist()
    fig_str = px.bar(
        strength_long, x="type", y="count", color="strength",
        category_orders={"type": type_order},
        color_discrete_map={"strong": "#2ca02c", "weak": "#ff7f0e"},
        title="Strong vs Weak per typ (top 20 typów, stacked)",
    )
    fig_str.update_layout(
        height=420, xaxis_tickangle=-45, barmode="stack",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig_str, use_container_width=True)
    st.dataframe(strength, use_container_width=True)

    # Liczba encji per artykuł — histogram
    st.subheader("Liczba encji per artykuł")
    ents_per_art = s1.copy()
    if "entities_count" in ents_per_art.columns:
        fig_per_art = px.histogram(
            ents_per_art, x="entities_count", color="run",
            nbins=40, opacity=0.6, barmode="overlay",
            title="Rozkład liczby encji per artykuł",
        )
        fig_per_art.update_layout(height=380, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig_per_art, use_container_width=True)
        st.caption(
            f"Median: {int(ents_per_art['entities_count'].median())} · "
            f"P95: {int(ents_per_art['entities_count'].quantile(0.95))} · "
            f"Max: {int(ents_per_art['entities_count'].max())}"
        )

    # ==========================================================================
    # POWTARZALNOŚĆ I UNIKALNOŚĆ
    # ==========================================================================
    st.header("🔁 Powtarzalność i unikalność encji")
    st.markdown(
        "**Po co to:** sprawdza czy model generuje encje *spójnie* (ta sama nazwa w wielu artykułach) "
        "czy *chaotycznie* (warianty pisowni, halucynacje 1-razowe). Wysoki % singletonów = sygnał ostrzegawczy."
    )

    # Liczba artykułów per encja (name+type)
    art_per_ent = (
        ents.groupby(["name", "type"])["url_hash"].nunique().reset_index(name="n_articles")
    )
    n_unique_ents = len(art_per_ent)
    n_singletons = int((art_per_ent["n_articles"] == 1).sum())
    n_articles = ents["url_hash"].nunique()
    diversity = n_unique_ents / total if total else 0
    avg_repeat = art_per_ent["n_articles"].mean() if not art_per_ent.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unikatowych encji (name+type)", n_unique_ents)
    c2.metric("Diversity score", f"{diversity:.3f}",
              help="unique / total. 1.0 = każda encja unikalna (chaos). <0.3 = dobre uogólnianie.")
    c3.metric("Singletony (1× w całym runie)", n_singletons,
              delta=f"{n_singletons/n_unique_ents*100:.1f}%" if n_unique_ents else None,
              delta_color="inverse",
              help=">50% singletonów = model nie konsoliduje wariantów (np. 'OpenAI' vs 'Open AI' vs 'OpenAI Inc').")
    c4.metric("Avg artykułów/encja", f"{avg_repeat:.2f}")

    # Histogram: ile encji występuje w 1, 2, 3, ... artykułach
    st.subheader("Histogram powtarzalności")
    st.caption("Oś X: w ilu artykułach pojawia się encja. Oś Y: ile encji ma taką częstość. Skala log-Y.")
    repeat_hist = art_per_ent["n_articles"].value_counts().sort_index().reset_index()
    repeat_hist.columns = ["n_articles", "n_entities"]
    # Bucketowanie ogona: 1, 2, 3, 4, 5, 6-10, 11-25, 26-100, 100+
    def _bucket(n):
        if n <= 5: return str(n)
        if n <= 10: return "6-10"
        if n <= 25: return "11-25"
        if n <= 100: return "26-100"
        return "100+"
    art_per_ent["bucket"] = art_per_ent["n_articles"].apply(_bucket)
    bucket_order = ["1", "2", "3", "4", "5", "6-10", "11-25", "26-100", "100+"]
    bucket_counts = (
        art_per_ent.groupby("bucket").size().reindex(bucket_order, fill_value=0)
        .reset_index(name="n_entities")
    )
    bucket_counts["pct"] = (bucket_counts["n_entities"] / n_unique_ents * 100).round(1)
    fig_rep = px.bar(
        bucket_counts, x="bucket", y="n_entities", text="pct",
        title="Ile encji występuje w X artykułach",
        category_orders={"bucket": bucket_order},
    )
    fig_rep.update_traces(texttemplate="%{text}%", textposition="outside")
    fig_rep.update_layout(
        height=380, margin=dict(l=10, r=10, t=50, b=10),
        yaxis_title="liczba unikatowych encji", xaxis_title="występuje w X artykułach",
    )
    st.plotly_chart(fig_rep, use_container_width=True)

    # Pareto: jak skoncentrowana jest dystrybucja
    st.subheader("Krzywa Pareto — koncentracja encji")
    st.caption(
        "Top X% najczęstszych encji generuje Y% wszystkich wystąpień. "
        "Stroma krzywa = mało encji dominuje (np. 'COVID' w wielu artykułach). "
        "Płaska = równomierny rozkład (każda encja ma podobną frekwencję)."
    )
    name_counts = (
        ents.groupby(["name", "type"]).size().reset_index(name="count")
        .sort_values("count", ascending=False).reset_index(drop=True)
    )
    name_counts["cumulative_pct"] = (name_counts["count"].cumsum() / total * 100).round(2)
    name_counts["rank_pct"] = ((name_counts.index + 1) / len(name_counts) * 100).round(2)

    # Sample do plotly (pełne 50k+ rekordów spowalnia)
    sample = name_counts.iloc[:: max(1, len(name_counts) // 1000)].copy()
    fig_pareto = px.line(
        sample, x="rank_pct", y="cumulative_pct",
        title="Pareto: top X% encji → Y% wystąpień",
    )
    fig_pareto.add_shape(type="line", x0=0, y0=0, x1=100, y1=100,
                        line=dict(dash="dot", color="gray"))
    fig_pareto.update_layout(
        height=380, margin=dict(l=10, r=10, t=50, b=10),
        xaxis_title="rank (% top encji)", yaxis_title="% wszystkich wystąpień",
    )
    st.plotly_chart(fig_pareto, use_container_width=True)

    # Konkretne progi Pareto
    def _pareto_at(target_pct):
        idx = (name_counts["cumulative_pct"] >= target_pct).idxmax() if (name_counts["cumulative_pct"] >= target_pct).any() else len(name_counts)
        return idx + 1, (idx + 1) / len(name_counts) * 100
    n50, pct50 = _pareto_at(50)
    n80, pct80 = _pareto_at(80)
    n90, pct90 = _pareto_at(90)
    st.markdown(
        f"- **50% wystąpień** generuje top **{n50}** encji ({pct50:.2f}% wszystkich unikatowych)\n"
        f"- **80% wystąpień** generuje top **{n80}** encji ({pct80:.2f}%)\n"
        f"- **90% wystąpień** generuje top **{n90}** encji ({pct90:.2f}%)"
    )

    # Strength ratio per kategoria
    st.subheader("Strong/Weak ratio per kategoria")
    st.caption(
        "% strong (Wikidata-linkable) vs weak (kontekstowy). "
        "Kategorie z wysokim % weak (np. Quantity, DateTime) to encje surowe — wymagają normalizacji w post-processing."
    )
    cat_str = (
        ents.groupby(["category", "strength"]).size().unstack(fill_value=0)
    )
    if "strong" not in cat_str.columns: cat_str["strong"] = 0
    if "weak" not in cat_str.columns: cat_str["weak"] = 0
    cat_str["total"] = cat_str["strong"] + cat_str["weak"]
    cat_str["pct_strong"] = (cat_str["strong"] / cat_str["total"] * 100).round(1)
    cat_str = cat_str.sort_values("total", ascending=False).reset_index()

    fig_cat_str = px.bar(
        cat_str.melt(id_vars=["category", "total", "pct_strong"],
                     value_vars=["strong", "weak"],
                     var_name="strength", value_name="count"),
        x="category", y="count", color="strength",
        color_discrete_map={"strong": "#2ca02c", "weak": "#ff7f0e"},
        title="Strong vs Weak per kategoria (stacked, sortowane po total)",
        category_orders={"category": cat_str["category"].tolist()},
    )
    fig_cat_str.update_layout(
        height=400, xaxis_tickangle=-30, barmode="stack",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    st.plotly_chart(fig_cat_str, use_container_width=True)
    st.dataframe(
        cat_str[["category", "strong", "weak", "total", "pct_strong"]],
        use_container_width=True, hide_index=True,
    )

    # Singletony — warning list (top 50 best candidates dla halucynacji / literówek)
    if n_singletons > 0:
        with st.expander(f"⚠️ Singletony — encje 1-razowe ({n_singletons}) — kandydaci na halucynacje/literówki"):
            singletons = art_per_ent[art_per_ent["n_articles"] == 1].sort_values("name").head(200)
            st.dataframe(singletons[["name", "type"]], use_container_width=True, hide_index=True)
            st.caption(
                "Pokazuję 200 pierwszych. Wysokie % singletonów (>50% encji) = problem konsolidacji "
                "wariantów pisowni / halucynacje. <30% to zdrowy sygnał."
            )
