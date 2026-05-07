"""Widok analityczny — numeryczna ocena jakości one-step vs two-step.

W przeciwieństwie do compare_onestep (który jest do podglądu wyników), ten widok
robi twardą analitykę pod decyzję prod:

- Quality Scorecard — composite 0-100 z rozbiciem na komponenty (lang/cat/Jaccard/SEO).
- Speed vs Quality scatter — per URL: czy szybsze = gorsze?
- Per-category mismatch heatmap — gdzie one-step się myli (top 15 kategorii).
- Per-language quality — czy działa równo w PL/EN/DE.
- SEO meta range — które pola łamią targety (title 50-60, meta 140-160, h1 ≤100, summary ≤400).
- Token efficiency — quality per token completion (kto generuje "tańsze" jakościowe wyniki).
- Failure modes — fail rate / attempts / finish_reason per ścieżka.

Każda sekcja: tytuł → legenda (co to znaczy + jak czytać) → wykres/tabela → werdykt liczbowy.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.data_loader import RESULTS_BASE
from dashboard.views.compare_onestep import (
    _list_compare_runs, _load_run, _entity_set,
    SEO_FIELDS,
    CRIT_SPEEDUP_WALL, CRIT_CAT_MATCH, CRIT_LANG_MATCH, CRIT_JACCARD,
)

# Targetowe długości SEO (charakters, ze schematu).
SEO_TARGETS = {
    "title": (50, 60, 70),                # (lower, upper recommended, hard limit)
    "meta_description": (140, 160, 160),
    "h1": (40, 80, 100),
    "article_summary": (180, 350, 400),
}


# ---------------- helpers ----------------

def _avg(xs):
    return statistics.fmean(xs) if xs else 0.0


def _quality_score(M: dict) -> dict:
    """Composite quality score 0-100. Komponenty:
      - language match (waga 15)  — czy poprawnie wykrywa język
      - category match (waga 30)  — czy klasyfikacja jest stabilna
      - entity Jaccard (waga 30)  — czy ekstrahuje te same encje
      - fail rate (waga 15)       — czy ścieżka się nie wywala (1 - fail_rate)
      - SEO compliance (waga 10)  — % pól w target range (title/meta_desc/h1/summary)
    """
    lang_pts = 15 * M["lang_match_rate"]
    cat_pts = 30 * M["cat_match_rate"]
    jacc_pts = 30 * min(1.0, M["jaccard_mean"] / 1.0)
    one_alive = 15 * (1 - M["one_fail_rate"])
    two_alive = 15 * (1 - M["two_fail_rate"])
    return {
        "lang_pts": lang_pts,
        "cat_pts": cat_pts,
        "jacc_pts": jacc_pts,
        "one_alive_pts": one_alive,
        "two_alive_pts": two_alive,
    }


def _seo_range_compliance(values: list[int], target: tuple) -> float:
    """Procent wartości w target range (lower, upper)."""
    if not values:
        return 0.0
    lower, upper, _hard = target
    in_range = sum(1 for v in values if lower <= v <= upper)
    return in_range / len(values)


# ---------------- main render ----------------

def render(filters: dict, data: dict):
    st.title("📈 Analiza jakości — one-step vs two-step")
    st.caption(
        "Ten widok robi numeryczną analizę porównawczą dla decyzji "
        "**prod-default: one-step czy two-step?**. Każda sekcja ma legendę co to "
        "znaczy + interpretację. Sekcja **Quality Scorecard** podsumowuje wszystko "
        "w jednym wskaźniku 0-100."
    )

    runs = _list_compare_runs()
    if not runs:
        st.warning(
            "Brak runów porównawczych. Uruchom:\n```bash\n"
            "python3 scripts/compare_onestep_vs_twostep.py --random --limit 200 --tag prod_check\n```"
        )
        return

    sel = st.selectbox("Run", runs, index=len(runs) - 1)
    payload = _load_run(sel)
    onestep = payload["onestep"]
    step1 = payload["step1"]
    step2 = payload["step2"]

    # Re-use compute_metrics z compare_onestep
    from dashboard.views.compare_onestep import _compute_metrics
    M = _compute_metrics(payload)

    n = M["n_q"]
    if n == 0:
        st.warning("Brak common OK URL — uruchom oba pipeline'y na tym samym sample.")
        return

    st.caption(f"Sample size (common OK): **{n}** URL  ·  dir: `{payload['dir'].name}`")

    # ============================================
    # 1. QUALITY SCORECARD
    # ============================================
    st.header("1. Quality Scorecard (0-100)")
    st.caption(
        "**Co to znaczy:** composite score łączący 5 wymiarów jakości w jedną liczbę. "
        "Wysoka waga: category match (30) i Jaccard encji (30) — to są twarde sygnały "
        "spójności. Niska waga: language (15), fail rate (15), SEO compliance (10).\n\n"
        "**Jak czytać:** im bliżej 100, tym pewniej można wstawiać do produkcji. "
        "Two-step jest **referencją** (assumed 100% — porównujemy z nim, bo to default). "
        "One-step pokazuje **w jakim % zgadza się z two-step**."
    )

    pts = _quality_score(M)

    # Two-step traktujemy jako referencję — quality 100 (każda metryka match=1.0 bo z sobą samym)
    score_one = pts["lang_pts"] + pts["cat_pts"] + pts["jacc_pts"] + pts["one_alive_pts"]
    score_two_alive = pts["two_alive_pts"]
    score_two = 15 + 30 + 30 + score_two_alive  # 100% match z samym sobą + alive penalty

    c1, c2, c3 = st.columns(3)
    c1.metric("One-step Quality Score", f"{score_one:.1f} / 90",
              help="Bez SEO compliance (10 pkt) — patrz sekcja niżej. Max bez SEO = 90.")
    c2.metric("Two-step (reference)", f"{score_two:.1f} / 90",
              help="Two-step jako baseline — match z sobą = 100%, jedyny ubytek to fail rate.")
    c3.metric("Quality gap", f"{score_two - score_one:.1f} pkt",
              delta=f"{(score_one - score_two)/score_two*100:+.1f}%" if score_two else None)

    # Komponenty
    comp_df = pd.DataFrame([
        {"komponent": "Language match", "waga": 15, "one-step": round(pts["lang_pts"], 2),
         "two-step ref": 15.0, "rate": f"{100*M['lang_match_rate']:.1f}%"},
        {"komponent": "Category match", "waga": 30, "one-step": round(pts["cat_pts"], 2),
         "two-step ref": 30.0, "rate": f"{100*M['cat_match_rate']:.1f}%"},
        {"komponent": "Entity Jaccard", "waga": 30, "one-step": round(pts["jacc_pts"], 2),
         "two-step ref": 30.0, "rate": f"{M['jaccard_mean']:.3f}"},
        {"komponent": "Alive (1-fail_rate)", "waga": 15, "one-step": round(pts["one_alive_pts"], 2),
         "two-step ref": round(pts["two_alive_pts"], 2),
         "rate": f"one={100*(1-M['one_fail_rate']):.1f}% two={100*(1-M['two_fail_rate']):.1f}%"},
    ])
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    # Werdykt
    if score_one >= 80:
        st.success(f"✅ **Werdykt jakości:** one-step traci tylko {score_two-score_one:.1f} pkt vs two-step — akceptowalna jakość.")
    elif score_one >= 60:
        st.warning(f"⚠️ **Werdykt jakości:** one-step traci {score_two-score_one:.1f} pkt — sprawdź gdzie konkretnie (sekcje niżej).")
    else:
        st.error(f"❌ **Werdykt jakości:** one-step traci {score_two-score_one:.1f} pkt — duża rozbieżność, two-step lepszy.")

    # ============================================
    # 2. SPEED vs QUALITY SCATTER
    # ============================================
    st.header("2. Speed vs Quality (per URL scatter)")
    st.caption(
        "**Co to znaczy:** każdy punkt = jeden URL. Oś X = latency (s) one-step, "
        "oś Y = Jaccard encji z two-step. Górny-lewy róg = szybkie i dokładne (idealnie). "
        "Dolny-prawy = wolne i niedokładne (najgorzej).\n\n"
        "**Jak czytać:** szukaj korelacji. Jeśli wolniejsze URL mają niższy Jaccard, "
        "to znaczy że one-step gubi jakość na trudnych artykułach."
    )

    sc_rows = []
    for r in M["per_url_rows"]:
        sc_rows.append({
            "lat_one": r["lat_one"],
            "jaccard": r["jaccard"],
            "lat_two": r["lat_two_combined"],
            "ent_one": r["ent_one"],
            "ent_two": r["ent_two"],
            "cat_match": r["cat_match"],
        })
    df_sc = pd.DataFrame(sc_rows)

    try:
        import altair as alt
        chart = (
            alt.Chart(df_sc)
            .mark_circle(size=80, opacity=0.6)
            .encode(
                x=alt.X("lat_one:Q", title="one-step latency (s)"),
                y=alt.Y("jaccard:Q", title="Jaccard encji vs two-step",
                        scale=alt.Scale(domain=[0, 1])),
                color=alt.Color("cat_match:N", title="kategoria zgodna",
                                scale=alt.Scale(range=["#e74c3c", "#2ecc71"])),
                tooltip=["lat_one", "jaccard", "lat_two", "ent_one", "ent_two"],
            )
            .properties(height=350)
        )
        st.altair_chart(chart, use_container_width=True)

        # Korelacja Pearson
        if len(df_sc) >= 3:
            corr = df_sc[["lat_one", "jaccard"]].corr().iloc[0, 1]
            st.caption(
                f"**Korelacja Pearson(latency, Jaccard):** {corr:+.3f} — "
                + ("ujemna (szybsze = wyższa jakość)." if corr < -0.1
                   else "dodatnia (wolniejsze = wyższa jakość)." if corr > 0.1
                   else "brak istotnej korelacji.")
            )
    except ImportError:
        st.dataframe(df_sc, use_container_width=True, hide_index=True)

    # ============================================
    # 3. PER-CATEGORY MISMATCH (heatmap-like)
    # ============================================
    st.header("3. Per-category mismatch — gdzie one-step się myli")
    st.caption(
        "**Co to znaczy:** dla każdej pary `(two_step → one_step)` liczba URL gdzie two-step "
        "powiedział X a one-step powiedział Y. Diagonala (X=Y) ukryta — pokazujemy tylko **mismatchy**.\n\n"
        "**Jak czytać:** górne wiersze = kategorie najczęściej źle klasyfikowane przez one-step. "
        "Tę listę używasz do iteracji promptu one-step."
    )

    pairs = [(r["category_two"] or "(brak)", r["category_one"] or "(brak)")
             for r in M["per_url_rows"]]
    df_pairs = pd.DataFrame(pairs, columns=["two_step", "one_step"])
    mismatch = df_pairs[df_pairs["two_step"] != df_pairs["one_step"]]

    if mismatch.empty:
        st.success("✅ **Zero mismatchy.** One-step i two-step zgadzają się w 100% kategorii.")
    else:
        grouped = (
            mismatch.groupby(["two_step", "one_step"]).size()
            .reset_index(name="count").sort_values("count", ascending=False)
        )
        grouped["% wszystkich URL"] = (grouped["count"] / n * 100).round(1)
        st.dataframe(grouped.head(20), use_container_width=True, hide_index=True)
        st.caption(
            f"**Mismatchy:** {len(mismatch)}/{n} URL ({100*len(mismatch)/n:.1f}%). "
            f"Top mismatch: `{grouped.iloc[0]['two_step']}` → `{grouped.iloc[0]['one_step']}` "
            f"({grouped.iloc[0]['count']}× = {grouped.iloc[0]['% wszystkich URL']}%)."
        )

    # ============================================
    # 4. PER-LANGUAGE QUALITY
    # ============================================
    st.header("4. Per-language quality — czy działa równo w PL/EN/DE/...")
    st.caption(
        "**Co to znaczy:** Jaccard encji + category match rozbite per język artykułu. "
        "Język bierzemy z two-step (referencja).\n\n"
        "**Jak czytać:** szukaj języków gdzie Jaccard wyraźnie spada — to znaczy że "
        "prompt one-step jest słabiej dotrenowany na tych językach."
    )

    lang_rows = []
    for r in M["per_url_rows"]:
        h = r["url_hash"]
        lang = (step2.get(h) or {}).get("language") or "?"
        lang_rows.append({
            "language": lang,
            "jaccard": r["jaccard"],
            "cat_match": int(r["cat_match"]),
            "lang_match": int(r["lang_match"]),
        })
    df_lang = pd.DataFrame(lang_rows)
    if not df_lang.empty:
        agg = (
            df_lang.groupby("language")
            .agg(n=("jaccard", "count"),
                 jaccard_mean=("jaccard", "mean"),
                 cat_match_pct=("cat_match", "mean"),
                 lang_match_pct=("lang_match", "mean"))
            .reset_index().sort_values("n", ascending=False)
        )
        agg["jaccard_mean"] = agg["jaccard_mean"].round(3)
        agg["cat_match_pct"] = (agg["cat_match_pct"] * 100).round(1)
        agg["lang_match_pct"] = (agg["lang_match_pct"] * 100).round(1)
        st.dataframe(agg, use_container_width=True, hide_index=True)

        # Wskazanie najgorszego
        if len(agg) >= 2:
            worst = agg.iloc[agg["jaccard_mean"].idxmin()]
            best = agg.iloc[agg["jaccard_mean"].idxmax()]
            st.caption(
                f"**Najlepszy język:** `{best['language']}` (Jaccard {best['jaccard_mean']:.3f}, "
                f"n={best['n']}). **Najgorszy:** `{worst['language']}` "
                f"(Jaccard {worst['jaccard_mean']:.3f}, n={worst['n']}). "
                f"Różnica: {best['jaccard_mean'] - worst['jaccard_mean']:.3f}."
            )

    # ============================================
    # 5. SEO META RANGE COMPLIANCE
    # ============================================
    st.header("5. SEO meta — % pól w target range")
    st.caption(
        "**Co to znaczy:** `title` 50-60 znaków, `meta_description` 140-160, `h1` 40-80, "
        "`article_summary` 180-350. To są SEO best practices (nie hard limity ze schemy — "
        "schema ma title ≤70, meta_desc ≤160, h1 ≤100, summary ≤400).\n\n"
        "**Jak czytać:** dla każdej ścieżki % URL gdzie pole jest w optymalnym zakresie. "
        "Niski % = pole albo za krótkie albo za długie — Google źle wyświetli SERP-y."
    )

    seo_rows = []
    for f in SEO_FIELDS:
        target = SEO_TARGETS[f]
        one_vals = M["field_lens"][f]["one"]
        two_vals = M["field_lens"][f]["two"]
        seo_rows.append({
            "field": f,
            "target": f"{target[0]}-{target[1]} (hard ≤{target[2]})",
            "one-step in_range %": round(100 * _seo_range_compliance(one_vals, target), 1),
            "two-step in_range %": round(100 * _seo_range_compliance(two_vals, target), 1),
            "one-step len mean": round(_avg(one_vals), 0) if one_vals else None,
            "two-step len mean": round(_avg(two_vals), 0) if two_vals else None,
            "one missing": M["missing"][f]["one"],
            "two missing": M["missing"][f]["two"],
        })
    st.dataframe(pd.DataFrame(seo_rows), use_container_width=True, hide_index=True)

    # ============================================
    # 6. TOKEN EFFICIENCY
    # ============================================
    st.header("6. Token efficiency — quality per output token")
    st.caption(
        "**Co to znaczy:** ile output tokens zostało wygenerowanych w one-step vs two-step "
        "(suma S1+S2 dla two-step). Quality / token mówi które rozwiązanie generuje "
        "więcej wartości za 1 token.\n\n"
        "**Jak czytać:** completion_tokens × #URL × $/M-tok = **realny koszt prod**. "
        "Self-hosted (RTX 5090) → koszt jest umowny, ale token count wpływa na throughput "
        "(więcej tokens = wolniej)."
    )

    one_out_total = sum(M["one_out"])
    two_out_total = sum(M["s1_out"]) + sum(M["s2_out"])
    one_out_mean = _avg(M["one_out"])
    two_out_mean = _avg(M["s1_out"]) + _avg(M["s2_out"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Tok/URL one-step", f"{one_out_mean:.0f}")
    c2.metric("Tok/URL two-step (S1+S2)", f"{two_out_mean:.0f}")
    c3.metric("Token saving", f"{two_out_mean - one_out_mean:.0f}",
              delta=f"{(one_out_mean-two_out_mean)/two_out_mean*100:+.1f}%" if two_out_mean else None)

    # Quality per token
    if one_out_mean and two_out_mean:
        # composite quality już mamy w score_one / score_two
        qpt_one = score_one / one_out_mean
        qpt_two = score_two / two_out_mean
        c1, c2, c3 = st.columns(3)
        c1.metric("Quality / 100 tok (one-step)", f"{qpt_one*100:.2f}")
        c2.metric("Quality / 100 tok (two-step)", f"{qpt_two*100:.2f}")
        c3.metric("Efficiency winner",
                  "one-step" if qpt_one > qpt_two else "two-step",
                  delta=f"{abs(qpt_one - qpt_two)*100:.2f} pkt")

    # ============================================
    # 7. FAILURE MODES
    # ============================================
    st.header("7. Failure modes — co się psuje")
    st.caption(
        "**Co to znaczy:** rozbicie failów per ścieżka i per typ błędu. "
        "`finish_reason='length'` = output ucięty (max_tokens). `attempts>1` = retry-with-feedback "
        "się włączył (powolniejsze, koszt 2× tokens).\n\n"
        "**Jak czytać:** jeśli one-step ma więcej `length` truncatów, trzeba podnieść max_tokens "
        "albo przyciąć schemat. Jeśli więcej `attempts>1`, jakość promptu jest niestabilna."
    )

    def _fail_breakdown(records, label):
        n = len(records)
        n_ok = sum(1 for r in records.values() if r.get("ok"))
        n_fail = n - n_ok
        n_length = sum(1 for r in records.values() if r.get("finish_reason") == "length")
        n_retry = sum(1 for r in records.values() if (r.get("attempts") or 1) > 1)
        return {
            "phase": label,
            "n_total": n,
            "n_ok": n_ok,
            "n_fail": n_fail,
            "fail_rate %": round(100 * n_fail / max(n, 1), 1),
            "finish=length": n_length,
            "attempts>1 (retry)": n_retry,
            "retry_rate %": round(100 * n_retry / max(n, 1), 1),
        }

    fail_df = pd.DataFrame([
        _fail_breakdown(onestep, "one-step"),
        _fail_breakdown(step1, "two-step S1"),
        _fail_breakdown(step2, "two-step S2"),
    ])
    st.dataframe(fail_df, use_container_width=True, hide_index=True)

    # ============================================
    # 7b. ENTITY TYPE DIVERSITY — test "model na diecie"
    # ============================================
    st.header("7b. Entity type diversity — test \"model na diecie\"")
    st.caption(
        "**Co to znaczy:** model w one-step ma jeden budżet uwagi na encje + SEO + kategorię. "
        "Hipoteza: gubi *różnorodność typów* — generuje głównie `Product`/`Person`/`Organization` "
        "(top 3 \"bezpieczne\"), pomija `Skill`/`Information`/`ComputingProduct` (specjalistyczne).\n\n"
        "**Jak czytać:** porównujesz Counter typów. Jeśli one-step ma **mniej unikalnych typów** "
        "albo **wyższą koncentrację top-3** → potwierdzenie efektu \"diety\". Wtedy ekstrakcja "
        "encji jest płytsza — produkcyjnie strata."
    )

    from collections import Counter
    one_types = Counter()
    two_types = Counter()
    for r in onestep.values():
        if not r.get("ok"):
            continue
        for e in (r.get("entities") or []):
            one_types[e.get("type", "?")] += 1
    for r in step1.values():
        if not r.get("ok"):
            continue
        for e in (r.get("entities") or []):
            two_types[e.get("type", "?")] += 1

    if one_types and two_types:
        all_types = sorted(set(one_types) | set(two_types),
                           key=lambda t: -(one_types.get(t, 0) + two_types.get(t, 0)))
        df_types = pd.DataFrame([
            {"type": t,
             "one-step": one_types.get(t, 0),
             "two-step": two_types.get(t, 0),
             "Δ": two_types.get(t, 0) - one_types.get(t, 0),
             "one %": round(100 * one_types.get(t, 0) / max(sum(one_types.values()), 1), 2),
             "two %": round(100 * two_types.get(t, 0) / max(sum(two_types.values()), 1), 2),
             }
            for t in all_types[:30]
        ])
        st.dataframe(df_types, use_container_width=True, hide_index=True, height=420)

        # Wskaźniki diet
        unique_one = sum(1 for c in one_types.values() if c >= 1)
        unique_two = sum(1 for c in two_types.values() if c >= 1)
        top3_one_share = sum(c for c in sorted(one_types.values(), reverse=True)[:3]) / max(sum(one_types.values()), 1)
        top3_two_share = sum(c for c in sorted(two_types.values(), reverse=True)[:3]) / max(sum(two_types.values()), 1)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Unique types (one)", unique_one)
        c2.metric("Unique types (two)", unique_two,
                  delta=f"{unique_two-unique_one:+d}")
        c3.metric("Top-3 share (one)", f"{100*top3_one_share:.1f}%",
                  help="Jaki % wszystkich encji to top-3 typów. Wyższy = węższa dystrybucja.")
        c4.metric("Top-3 share (two)", f"{100*top3_two_share:.1f}%")

        if top3_one_share > top3_two_share + 0.05:
            st.warning(
                f"⚠️ **Potwierdzenie \"diety\":** one-step skupia {100*top3_one_share:.0f}% encji "
                f"w top-3 typach (vs two-step {100*top3_two_share:.0f}%). Mniej różnorodności = "
                f"specjalistyczne typy gubione."
            )
        elif unique_two - unique_one >= 5:
            st.warning(
                f"⚠️ **One-step gubi typy:** {unique_two - unique_one} unikalnych typów więcej w two-step. "
                f"Sprawdź których typów brakuje (kolumna Δ)."
            )
        else:
            st.success(
                f"✅ Type diversity podobna (Δ unique = {unique_two - unique_one}, "
                f"top-3 share spread = {abs(top3_one_share - top3_two_share)*100:.1f}pp). Brak \"diety\"."
            )

    # ============================================
    # 8. ENTITY COUNT DISTRIBUTION
    # ============================================
    st.header("8. Entity count distribution")
    st.caption(
        "**Co to znaczy:** ile encji wyciąga one-step vs two-step per artykuł. "
        "Schema ma cap 60. Tendencja \"więcej encji\" niekoniecznie = lepiej — "
        "może oznaczać hallucynacje albo encje 2-rzędowe.\n\n"
        "**Jak czytać:** jeśli rozkłady się pokrywają, ścieżki są spójne. "
        "Jeśli one-step ma medianę 5, a two-step 15 — one-step gubi encje."
    )

    if M["one_counts"] and M["two_counts"]:
        dist_rows = []
        for c in M["one_counts"]:
            dist_rows.append({"phase": "one-step", "n_entities": c})
        for c in M["two_counts"]:
            dist_rows.append({"phase": "two-step", "n_entities": c})
        df_dist = pd.DataFrame(dist_rows)

        try:
            import altair as alt
            chart = (
                alt.Chart(df_dist)
                .mark_bar(opacity=0.6)
                .encode(
                    x=alt.X("n_entities:Q", bin=alt.Bin(maxbins=25), title="# encji per artykuł"),
                    y=alt.Y("count()", title="# URL"),
                    color="phase:N",
                )
                .properties(height=240)
            )
            st.altair_chart(chart, use_container_width=True)
        except ImportError:
            st.dataframe(df_dist.groupby("phase")["n_entities"].describe())

        st.caption(
            f"**Stats:** one-step median={statistics.median(M['one_counts']):.0f} "
            f"(p95={sorted(M['one_counts'])[int(0.95*len(M['one_counts']))-1] if len(M['one_counts'])>1 else '?'}) · "
            f"two-step median={statistics.median(M['two_counts']):.0f} "
            f"(p95={sorted(M['two_counts'])[int(0.95*len(M['two_counts']))-1] if len(M['two_counts'])>1 else '?'})."
        )

    # ============================================
    # 9. LATENCY vs INPUT TOKENS (czy długie artykuły są wolniejsze?)
    # ============================================
    st.header("9. Latency vs input tokens — czy długie artykuły blokują batch?")
    st.caption(
        "**Co to znaczy:** każdy punkt = jeden URL. X = `prompt_tokens` (input), Y = `latency_s`. "
        "Jeśli korelacja dodatnia → długie artykuły są wolniejsze i blokują continuous batching "
        "(jeden 15k-tok URL może spowolnić cały batch).\n\n"
        "**Jak czytać:** korelacja >0.6 = warto zmniejszyć `MAX_ARTICLE_TOKENS` (np. 15000→8000) "
        "→ ucina ogon p95/p99, w prod daje ~30-40% speedupu."
    )

    lat_in_rows = []
    for r in onestep.values():
        if not r.get("ok"):
            continue
        u = r.get("usage") or {}
        lat_in_rows.append({
            "phase": "one-step",
            "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(u.get("completion_tokens", 0) or 0),
            "latency_s": float(r.get("latency_s") or 0),
        })
    for r in step1.values():
        if not r.get("ok"):
            continue
        u = r.get("usage") or {}
        lat_in_rows.append({
            "phase": "two-step S1",
            "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(u.get("completion_tokens", 0) or 0),
            "latency_s": float(r.get("latency_s") or 0),
        })
    df_li = pd.DataFrame(lat_in_rows)
    if not df_li.empty:
        try:
            import altair as alt
            chart = (
                alt.Chart(df_li)
                .mark_circle(size=40, opacity=0.4)
                .encode(
                    x=alt.X("prompt_tokens:Q", title="prompt_tokens (input)"),
                    y=alt.Y("latency_s:Q", title="latency (s)"),
                    color="phase:N",
                    tooltip=["prompt_tokens", "completion_tokens", "latency_s", "phase"],
                )
                .properties(height=320)
            )
            st.altair_chart(chart, use_container_width=True)
        except ImportError:
            st.dataframe(df_li.head(50))

        # Korelacja per phase + quartile slowdown
        rows_corr = []
        for ph in df_li["phase"].unique():
            sub = df_li[df_li["phase"] == ph]
            if len(sub) < 4:
                continue
            corr = sub[["prompt_tokens", "latency_s"]].corr().iloc[0, 1]
            sub_sorted = sub.sort_values("prompt_tokens")
            q1 = sub_sorted.iloc[:len(sub_sorted)//4]["latency_s"].mean()
            q4 = sub_sorted.iloc[3*len(sub_sorted)//4:]["latency_s"].mean()
            rows_corr.append({
                "phase": ph,
                "n": len(sub),
                "corr(input, lat)": round(corr, 3),
                "lat short (q1) s": round(q1, 2),
                "lat long (q4) s": round(q4, 2),
                "slowdown long/short": f"{q4/q1:.2f}×" if q1 else "—",
                "input median": int(sub["prompt_tokens"].median()),
                "input p95": int(sub["prompt_tokens"].quantile(0.95)),
                "input max": int(sub["prompt_tokens"].max()),
            })
        st.dataframe(pd.DataFrame(rows_corr), use_container_width=True, hide_index=True)
        for r in rows_corr:
            if r["corr(input, lat)"] > 0.6:
                st.warning(
                    f"⚠️ `{r['phase']}`: korelacja {r['corr(input, lat)']:.2f} — długie artykuły blokują batch. "
                    f"Slowdown q4/q1 = {r['slowdown long/short']}. Rozważ `MAX_ARTICLE_TOKENS = 8000`."
                )

    # ============================================
    # 10. THROUGHPUT W CZASIE (per-batch variance, ground truth z `ts`)
    # ============================================
    st.header("10. Throughput w czasie — variance per okno czasowe")
    st.caption(
        "**Co to znaczy:** rekordy z `ts` (od commita `b0690d0`) zliczane w oknach 60s. "
        "Pokazuje czy throughput jest **stabilny** czy **się zmienia** (np. przez scraper w tle, "
        "garbage collection, długi artykuł blokujący slot).\n\n"
        "**Jak czytać:** stabilna pozioma linia = healthy pipeline. Skoki w dół = "
        "warmup/cold spot lub artykuł-outlier. Skoki w górę = batch z samymi krótkimi."
    )

    from datetime import datetime as _dt
    bucket_rows = []
    for label, recs in [("one-step", onestep), ("two-step S1", step1), ("two-step S2", step2)]:
        ts_list = []
        for r in recs.values():
            if not r.get("ok"):
                continue
            ts = r.get("ts")
            if ts:
                try:
                    ts_list.append(_dt.fromisoformat(ts))
                except ValueError:
                    pass
        if len(ts_list) < 5:
            continue
        ts_list.sort()
        t0 = ts_list[0]
        for ts in ts_list:
            bucket_min = int((ts - t0).total_seconds() // 60)
            bucket_rows.append({"phase": label, "minute": bucket_min})

    if bucket_rows:
        df_b = pd.DataFrame(bucket_rows)
        agg = df_b.groupby(["phase", "minute"]).size().reset_index(name="urls_per_min")

        try:
            import altair as alt
            chart = (
                alt.Chart(agg)
                .mark_line(point=True)
                .encode(
                    x=alt.X("minute:Q", title="minuta od startu fazy"),
                    y=alt.Y("urls_per_min:Q", title="URL przetworzonych w tej minucie"),
                    color="phase:N",
                )
                .properties(height=280)
            )
            st.altair_chart(chart, use_container_width=True)
        except ImportError:
            st.dataframe(agg)

        # Variance verdict per phase
        for ph in agg["phase"].unique():
            sub = agg[agg["phase"] == ph]["urls_per_min"]
            if len(sub) < 3:
                continue
            cv = sub.std() / sub.mean() if sub.mean() else 0
            if cv > 0.4:
                st.warning(
                    f"⚠️ `{ph}`: coefficient of variation = {cv:.2f} (>0.4) — "
                    f"throughput **niestabilny**. Mean {sub.mean():.1f} URL/min, "
                    f"std {sub.std():.1f}, spread {sub.min()}-{sub.max()}."
                )
            else:
                st.caption(
                    f"`{ph}`: cv={cv:.2f} (stabilny). Mean {sub.mean():.1f} URL/min, "
                    f"spread {sub.min()}-{sub.max()}."
                )
    else:
        st.info(
            "Brak rekordów z polem `ts`. Stary run sprzed commita `b0690d0` — "
            "uruchom nowy run by zobaczyć analizę throughputu w czasie."
        )

    # ============================================
    # FINAL VERDICT
    # ============================================
    st.header("Werdykt finalny")
    speed_pass = M["speedup_wall"] >= CRIT_SPEEDUP_WALL
    quality_pass = (M["cat_match_rate"] >= CRIT_CAT_MATCH
                    and M["lang_match_rate"] >= CRIT_LANG_MATCH
                    and M["jaccard_mean"] >= CRIT_JACCARD)
    cols = st.columns(3)
    cols[0].metric("Quality Score (one-step)", f"{score_one:.1f} / 90")
    cols[1].metric("Speedup wall", f"{M['speedup_wall']:.2f}×",
                   delta=f"target ≥{CRIT_SPEEDUP_WALL}×")
    cols[2].metric("Token saving",
                   f"{(two_out_mean-one_out_mean)/max(two_out_mean,1)*100:+.1f}%")

    if speed_pass and quality_pass:
        st.success(
            f"✅ **One-step kandydat na prod** — quality {score_one:.0f}/90, "
            f"speedup {M['speedup_wall']:.2f}×, oszczędność {(two_out_mean-one_out_mean)/max(two_out_mean,1)*100:.0f}% tokenów."
        )
    elif quality_pass and not speed_pass:
        st.info(
            f"ℹ️ Quality OK ({score_one:.0f}/90), ale brak speedupu ({M['speedup_wall']:.2f}× < {CRIT_SPEEDUP_WALL}×). "
            f"Two-step zostaje defaultem dopóki one-step nie przyspieszy."
        )
    elif speed_pass and not quality_pass:
        st.warning(
            f"⚠️ Szybsze ({M['speedup_wall']:.2f}×) ale jakość {score_one:.0f}/90 nie spełnia kryterium. "
            f"Sprawdź sekcje 3 (mismatch kategorii) i 4 (per-language) — gdzie konkretnie traci."
        )
    else:
        st.error(
            f"❌ One-step traci na obu — speed {M['speedup_wall']:.2f}× i quality {score_one:.0f}/90. "
            f"Two-step zostaje."
        )
