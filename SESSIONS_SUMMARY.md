# SESSIONS_SUMMARY

> **Note:** ten plik jest indeksem. Szczegóły per sesja siedzą w katalogu
> [`SESSIONS_SUMMARY/`](./SESSIONS_SUMMARY/) z plikami nazwanymi `<data>_<topic>.md`.

## Indeks chronologiczny

| Data | Plik | Główne tematy |
|---|---|---|
| 2026-05-07 | [`SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md`](./SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md) | Phase 0–4 baseline two-step pipeline |
| 2026-05-07 | [`SESSIONS_SUMMARY/2026-05-07_dashboard_streamlit.md`](./SESSIONS_SUMMARY/2026-05-07_dashboard_streamlit.md) | Streamlit dashboard do analizy wyników |
| 2026-05-07 | [`SESSIONS_SUMMARY/2026-05-07_v6_100pct_and_scraper.md`](./SESSIONS_SUMMARY/2026-05-07_v6_100pct_and_scraper.md) | Phase 5 v6 — 100% reliability + crawl4ai scraper |
| 2026-05-07 | [`SESSIONS_SUMMARY/2026-05-07_resume_context_overflow_junkey.md`](./SESSIONS_SUMMARY/2026-05-07_resume_context_overflow_junkey.md) | Resume-after-context-overflow + junkey kategoria |
| 2026-05-08 | [`SESSIONS_SUMMARY/2026-05-08_threestep_fourstep_sponsored_scrapers.md`](./SESSIONS_SUMMARY/2026-05-08_threestep_fourstep_sponsored_scrapers.md) | Three-step v1/v2/v3 + Four-step v4 sponsored detection + scrapery (intymnehistorie, exposilesia) + robots.txt + Content-Type filter |
| 2026-05-11 | [`SESSIONS_SUMMARY/2026-05-11_v1_vs_v2_comparison_and_embeddings_setup.md`](./SESSIONS_SUMMARY/2026-05-11_v1_vs_v2_comparison_and_embeddings_setup.md) | SPO v1 vs v2 comparison (15 730 wspólnych URL, v1 +15% szybsze, triples Jaccard 0.10) + rozkład 51 typów Azure NER + setup Qwen3-Embedding-4B na Spark (orchestrator dual-container) |

## Konwencje

- **Naming**: `YYYY-MM-DD_<topic-slug>.md` w katalogu `SESSIONS_SUMMARY/`. Topic slug bez spacji, snake_case.
- **Format**: TL;DR + Punkt startowy + Co zrobiono per iteracja + Tabele liczb + Otwarte pytania + Lista nowych plików.
- **Cross-references**: linki do `PLANS/`, `DECISIONS.md`, konkretnych skryptów i runów (`final_results/<ts>_<tag>/`).
- **Liczby**: zawsze z kontekstem (sample size, seed, concurrency). Bez tych metadanych wartości są niesporównywalne.
