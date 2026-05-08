# Changelog

Format: data + krótkie streszczenie zmian. Pełne podsumowania per sesja w [`SESSIONS_SUMMARY/`](SESSIONS_SUMMARY/).

## 2026-05-08 — Three-step → Four-step + sponsored detection + scrapery

**Pipeline:**
- **`scripts/run_threestep.py`** (v1) — pierwsza próba three-step (classify → meta || entities). Classifier z pełnym 41-enum okazał się za drogi (2.63 s/URL). FAIL D7c v1.
- **`scripts/run_threestep_v2.py`** (v2) — binary classifier `0/1` przez vLLM `guided_choice` + truncated input 1000 chars. Classify mean 0.21 s/URL (12× szybciej). 3 osobne ThreadPoolExecutor (1+3+4=8). Wynik: 3.26 s/URL = +6.3% szybsze niż baseline5000 (3.48 s/URL). Per-stage logi (`classify.log`, `meta.log`, `entities.log`, `run.log`).
- **`scripts/run_threestep_v3.py`** (v3) — single ThreadPoolExecutor + 3 priority queues (wzorzec A). Concurrency=6 (Spark dławi się na 8). Naprawiony bug: priority pull `meta > entities` powodował sekwencję; fix: load-balance między meta i entities.
- **`scripts/run_fourstep_v1.py`** (v4) — dorzucony 4-ty etap **sponsored detection** jako równoległa faza po classify. Decyzja: nie merge'ować sponsored do meta — różne tryby cognitive (klasyfikacja vs generacja).

**Sponsored detection:**
- Schema binary: `{sponsored: bool, sponsored_subtype: enum, sponsored_justification: string}`. Subtype enum: `[null, full_sponsored, link_insertion, brand_mentions, advertorial]`. `affiliate_review` przeniesiony do editorial.
- Prompt: 10 examples (full_sponsored, link_insertion, brand_mentions, owner-commercial, single-product news, press release, single-product review, affiliate review).
- **Kluczowy fix domain context**: user-prompt zawiera `PUBLISHER DOMAIN: <domain>` linię + reguła "links to {domain} są INTERNAL = not third-party sponsored". Bez tego model błędnie flagował owner-commercial (publisher promuje swój sklep) jako link_insertion.

**Scraper:**
- `scraper/scrape_domain.py` UPDATE: klasa `RobotsChecker` — per-domena cache `robots.txt` przez `requests` z explicit Chrome UA (omija Cloudflare 403 problem stdlib `RobotFileParser.read()`). Filtruje URL na 3 etapach (discovery, BFS, crawl). Nowe flagi: `--ignore-robots`, `--user-agent`.
- `scraper/scrape_domain.py` UPDATE: filtr `Content-Type: text/html` w `crawl_urls` po Playwright fetchu (case-insensitive lookup w response_headers).
- Zescrapowane domeny: `websites_intymnehistorie/` (139 URL), `websites_exposilesia/` (141 URL), `websites_cmomega/` (211 URL). Wszystkie zmergowane do `websites/`.

**Dokumentacja:**
- `SESSIONS_SUMMARY/2026-05-08_threestep_fourstep_sponsored_scrapers.md` — pełen szczegółowy log sesji.
- `SESSIONS_SUMMARY.md` — przerobiony na indeks chronologiczny.
- `PLANS/threestep_pipeline_plan.md` — sekcje v2, v3, v4 + tabela porównawcza.
- `PLANS/threestep_pipeline_todo.md` — checkboxy v4 + scrapery.
- `PLANS/sponsored_detection_plan.md` — rev 1→5, owner-commercial discovery.
- `JACCARD.md` — edukacyjny doc o Jaccard Index z mermaid diagramami.
- `scraper/README.md` — UPDATE: sekcje robots.txt + Content-Type filter.

**Wyniki pomiarów (500 URL random seed=42):**

| Run | Wall s/URL | URL/h | Junk% | Junk recall | Jaccard | Fail rate |
|---|---|---|---|---|---|---|
| baseline5000 (conc=6, sequential) | 3.48 | 1035 | 11.44% | — | — | 0% |
| three-step v1 | 4.58 (-32%) | 787 | 1.20% | 8.9% | 0.552 | 0.6% |
| three-step v2 b2 (1+3+4=8) | **3.26** (+6.3%) | **1104** | 3.00% | 23.2% | 0.495 | 0% |
| three-step v3 c6_fix (single pool 6) | 3.75 (-7.8%) | 960 | 3.20% | 25.0% | 0.489 | 0% |
| four-step v1 smoke n=5 | 5.28 | — | 0% | — | — | 0% |

**Otwarte:** fair-baseline run two-step (concurrency=6 na 500 URL z seed=42) — uczciwy punkt odniesienia, którego brakuje. Pełen run v4 na 1000 URL.

## 2026-05-07 — Phase 0–4 + dashboard + scraper + junkey + resume

Wcześniejsze sesje (przed założeniem CHANGELOG.md). Szczegóły w:

- [`SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md`](SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md) — Phase 0–4 baseline two-step pipeline
- [`SESSIONS_SUMMARY/2026-05-07_dashboard_streamlit.md`](SESSIONS_SUMMARY/2026-05-07_dashboard_streamlit.md) — Streamlit dashboard
- [`SESSIONS_SUMMARY/2026-05-07_v6_100pct_and_scraper.md`](SESSIONS_SUMMARY/2026-05-07_v6_100pct_and_scraper.md) — v6 100% reliability + crawl4ai scraper
- [`SESSIONS_SUMMARY/2026-05-07_resume_context_overflow_junkey.md`](SESSIONS_SUMMARY/2026-05-07_resume_context_overflow_junkey.md) — resume after context overflow + junkey kategoria
