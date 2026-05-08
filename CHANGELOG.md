# Changelog

Format: data + krótkie streszczenie zmian. Pełne podsumowania per sesja w [`SESSIONS_SUMMARY/`](SESSIONS_SUMMARY/).

## 2026-05-09 (rano) — SPO v3 full parallel A/B + maxItems removed

**Zmiana planu po cząstkowym benchu v1 (354/1000):**
- 1000-art bench v1+v2 ANULOWANY (cząstkowe wyniki potwierdziły pipeline działa,
  parse_errors=0, triples sensowne — wystarczy by przejść do full).
- Idziemy bezpośrednio na full sample (25667 art) z OBU pipeline'ami w PARALLEL,
  każdy `--concurrency 4` (suma = 8 inflight, sweet spot vLLM Sparka).

**Schema fix (`prompts/spo_schema_v3.json` + `prompts/spo_pipe_v3_schema.json`):**
- `maxItems` usunięte z `entities` (było 60), `central_entities` (było 5),
  `triples` (było 40). Capy były arbitralne, model i tak ma `max_tokens` jako natural
  cap. Decyzja D26.

**Nowy orchestrator (`scripts/run_spo_v1_v2_test.py`):**
- Stage 1: clear `websites_cache/`.
- Stage 2: pre-warm cache (parallel ThreadPool, 8 workers, 25667 art) z osobnym pomiarem
  czasu w `cache_warmup_meta.json`. Trafilatura HTML→markdown to CPU-bound koszt
  (niezależny od GPU), mierzymy go osobno dla ekstrapolacji ETA na RTX 6000 Pro.
- Stage 3: launch v1 (cram) + v2 (split) jako subprocess'y, oba `--concurrency 4`,
  oba `--limit 0 --random --seed 42`. Cache współdzielony (read-only po warmupie).
- Stage 4: czeka na oba, generuje comparison report.

**Pliki:**
- `scripts/run_spo_v1_v2_test.py` (nowy)
- `PLANS/spo_v3_full_parallel_plan.md` (nowy)
- `DECISIONS.md`: D26 (maxItems removed) + D27 (parallel run + cache gen separate).
- Aktualizacja CHANGELOG.

**Cele runa:**
- Junk % na realnej PL/multi-language próbce.
- Sponsored % (paid_placement + brand_mentions + advertorial dist).
- Predykaty — harvest 250-500k triples × `relation_type` + `predicate_phrase` distribution
  do wyboru closed enum w v4 (`PLANS/spo_predicate_refinement_plan.md`).
- ETA scaling na RTX 6000 Pro (GPU ~3-5× szybciej, CPU cache gen stały).

---

## 2026-05-08 (noc) — SPO v3 rich-JSON (replace pipe format)

**Motywacja:** v2 pipe format (`s|p|o\n`) miał 2-7% parse errors mimo dwóch iteracji wzmacniania
promptu (extra-pipe qualifiers `lody waniliowe|stored in|lodówka|at least|4 godziny`,
missing-pipe glue `Badanie UFL|is non-invasive`). Decyzja: switch na JSON wymuszany przez
xgrammar (`response_format: json_schema`) → strukturalna poprawność = 100%.

**Rich JSON schema** (D24, D25):
- `primary_topic` (string) — syntetyczny hyperonim, może być spoza entities list.
- `central_entities[]` — 1-5 encji z gradacją `primary`/`secondary` (silniejszy sygnał niż
  boolean `is_central`).
- `triples[]` — 9 wymaganych pól per triple:
  `subject`, `subject_type` (Azure NER 51-enum),
  `relation_type` (snake_case English, **freeform w v3 — bootstrap, enum dopiero w v4**),
  `predicate_phrase` (article-language natural verb phrase, backup gdy relation_type coarse),
  `object`, `object_type`, `object_kind ∈ {entity, literal}`,
  `evidence_span` (verbatim fragment z artykułu — audit trail), `confidence` (0-1).

**Pliki nowe:**
- `prompts/spo_pipe_v3_schema.json` + `prompts/spo_pipe_v3_system.md` — split-call dla spo_v2.
- `prompts/spo_schema_v3.json` + `prompts/spo_entities_v3_system.md` — single-call cram dla spo_v1.
- `lib/spo_pipeline_v3.py` — `process_entities_spo_v3`, `process_spo_pipe_v3`, `join_final_v3`,
  helpers (`_normalize_rich_triple`, `_dedup_rich_triples`, `_validate_triples_against_entities`).
- `PLANS/spo_rich_json_v3_plan.md` — plan implementacji + benchmark plan.
- `PLANS/spo_predicate_refinement_plan.md` — TODO dla v4 (closed enum po harvescie).
- `SESSIONS_SUMMARY/2026-05-08_spo_rich_json.md` — pełne podsumowanie sesji.

**Pliki edytowane (orkiestratory):**
- `scripts/run_spo_v1.py` — load v3 prompts/schemas, max_tokens 4500, spo_raw.txt jako JSON-per-line.
- `scripts/run_spo_v2.py` — load v3 prompts/schemas, max_tokens 4200 (`MAX_TOKENS_SPO_PIPE_V3`).

**Pliki zachowane (deprecated, dla A/B reference):**
- `prompts/spo_pipe_v2_system.md` (pipe format).
- `prompts/spo_schema_v1.json` (basic s/p/o).
- `prompts/spo_entities_v1_system.md`.
- `lib/spo_pipeline_v1.py`, `lib/spo_pipeline_v2.py` (re-eksportowane przez v3 dla helperów).

**Pre-bench smoke (10 art, seed=42, conc=8, cold cache):**
| Pipeline | wall | triples/art | s_unm% | parse_err |
|---|---|---|---|---|
| v1 cram | 93.7s | 8.75 | 4.29% | 0 |
| v2 split | 133.4s (+42%) | 11.88 (+35%) | 2.11% | 0 |

Zero parse errors w obu (xgrammar). v2 więcej triples + lepszy s match, ale wolniejszy.

**Commits:** `8140c60` (v3 implementation), `b98fcc5` (lib helpers prereq), `8a8c7c2` (v1 join args fix).

**Następnie (this session, autonomous):** bench v1 1000 art seed=42, bench v2 1000 art seed=42,
porównanie, pełen run zwycięzcy na 25667 art. Cache czyszczone przed każdym (mierzymy też
trafilatura cold time).

---

## 2026-05-08 (późny wieczór) — SPO pipeline fix: drain-first scheduling + rich entity context

**Worker starvation fix (v1 + v2):**
- Diagnoza run 19:47: `spo_pipe.log = 0 B` po 17 min, classified.jsonl 6.1 MB rosło, entities.jsonl zamarł na 54 KB. Worker priority `classify > entities > spo` + producer zalewający unbounded `q_classify` → late stages głodzone.
- **Fix:** odwrócony priorytet — drain-first (`spo > entities > classify` w v2; `entities > classify` w v1). Classify z `timeout=0.1` jako fallback gdy późniejsze etapy puste. `q_classify = queue.Queue(maxsize=concurrency*8)` bounded — producer throttluje się na put().
- Efekt: GPU saturation utrzymywana (4 inflight cały czas), peak RAM `state[]` mniejszy (artykuły szybciej finalizowane), spo_pipe.log nabija linie od pierwszych sekund.
- Pliki: `scripts/run_spo_v1.py`, `scripts/run_spo_v2.py`. Decyzja: D21.

**Rich entity context dla spo_pipe_v2:**
- Po enrich każda encja ma `{name, type, category, strength, is_central}` ale do `process_spo_pipe_v2` szła tylko `name`. Strata sygnału: `is_central` (max 5 głównych bohaterów), `type` (51 Azure NER → role + predicate priors).
- **Format:** `* name [type, central]` (bullet list, central first) zamiast `name1, name2, ...`. System prompt rozszerzony o sekcję `## ENTITY METADATA — how to use the tags` z role priors (Org/Person → subject, Number/Currency/Temperature → object) i predicate priors (Temperature → `cooked at`, Currency → `costs`).
- Pominięte (duplikat sygnału): `category` (deterministyczna agregacja typów), `strength` (skorelowany z typem). Koszt: ~+150 tok/article.
- Pliki: `lib/spo_pipeline_v2.py:process_spo_pipe_v2`, `prompts/spo_pipe_v2_system.md`. Decyzja: D22.

**A/B run:** v1 + v2 równolegle, `--limit 0` (cała próbka), tag `full_drainfix`, w tmux sessions `spo_v1`/`spo_v2`.

---

## 2026-05-08 (wieczór) — SPO pipelines (v1 + v2) + streaming loader + v3 classifier

**Pipeline'y SPO (Subject-Predicate-Object) — fundament knowledge graph:**
- **`scripts/run_spo_v1.py`** + **`lib/spo_pipeline_v1.py`** — two-step (classify + entities_spo). Single-call JSON: w jednym wywołaniu LLM zwraca `entities` (kanoniczne nazwy + `is_central` boolean, max 5 per artykuł) + `triples` (s, p, o). Free-form predicates dla bootstrap discovery (closed vocab v2 dopiero z danych).
- **`scripts/run_spo_v2.py`** + **`lib/spo_pipeline_v2.py`** — three-step (classify + entities_only + spo_pipe). Pipe format dla SPO (`subject|predicate|object\n` per linia, raw text, parser tolerantny). **Hard rule predicate MUST be ENGLISH** dla wszystkich języków artykułu. Smoke: ~60% mniej output tokenów vs JSON, 31% szybszy wall vs v1.
- **Prompty:** `prompts/spo_entities_v1_system.md` (v1, canonical+central+SPO+3 examples), `prompts/spo_entities_only_v2_system.md` + `prompts/spo_pipe_v2_system.md` (v2, split). Schemy: `prompts/spo_schema_v1.json`, `prompts/spo_entities_only_v2_schema.json`.
- **Auto-summary** `scripts/spo_summary_v1.py` (top-100 predicates, top-50 central entities, type×is_central, domain stats, 30 sample triples, predicate clustering hint).
- **Dashboard view** `dashboard/views/spo.py` (karta `🕸️ SPO / Knowledge Graph`).

**Streaming loader z disk cache (`websites_cache/`):**
- **`lib/streaming_loader.py`** — `stream_articles_async()`, generator yielding articles po jednym, producer ThreadPool (default n=2 workerów, override `--loader-workers`), bounded queue maxsize=200. Cache `websites_cache/<hash>.json` w formacie `{"domain":"...","url":"...","content":"<markdown>"}`. Versioning w `_version.txt` (obecna v4). Smoke: 5.6× speedup z cache (1.91s cold → 0.34s hot na 20 URL).
- Integracja w `run_spo_v1.py` + `run_spo_v2.py` z flagami `--no-streaming`, `--loader-workers`, `--cache-dir`. Default streaming ON. **Pierwsze artykuły do GPU w <1s** vs 5-15min idle z sekwencyjnym `load_articles()`.
- Plan: `PLANS/streaming_loader_plan.md`.

**v3 classifier (recall na listing pages):**
- **`lib/junk_pre_filter.py`** — deterministyczny regex pre-classifier. Match `/tag/`, `/author/`, `/archive/`, `/search/`, `?s=...`, `?paged=N`, `?start=N` → skip LLM, zapisz stub z `ml_skipped=True, junk_reason=<label>`. Oszczędza tokeny + 0.2-0.6s/URL na każdym matchu.
- **`prompts/step_junkclassify_v3_system.md`** — sekcja "OVERRIDE URL signals" zastępuje "Strong URL signals". Reguła: tag/category/author URL → JUNK regardless of content. Krytyczny: `/tag/` z 1-2 wpisami pozostaje JUNK (override "3+ snippets" rule). Dwa nowe przykłady: K (single-entry tag), L (single-entry author archive).
- v2 classifier miał 73% recall na tag pages (22/81 false negatives na pomocedlaseniora.pl) — v3 fix u źródła (pre-filter) i w prompcie.

**Output schema (kompatybilność z dashboard + analizą):**
- Każdy run produkuje 4+ pliki JSONL: **`classified.jsonl`** (junk/non-junk + URL signals), **`entities.jsonl`** (encje canonical z `is_central`), **`spo.jsonl`** (triplety s/p/o), **`final.jsonl`** (joined record). Plus legacy `entities_spo.jsonl` w v1 dla resume.
- **`spo_raw.txt`** — surowy pipeline output (autentyczny w v2, reconstructed z JSON w v1) — same triplety bez headerów, do wglądu w format wyjścia LLM.
- `run_meta.json` z metadanymi runa: pipeline, classifier_prompt, use_streaming, loader_stats, n_pre_filter_junk, counters.
- `SUMMARY.md` (auto-generated po runie).

**Decyzje (DECISIONS.md):**
- D16: SPO + canonical + central + free-form predicate bootstrap
- D17: streaming loader z disk cache markdown
- D18: v2 SPO pipe pipeline (alternatywna architektura)
- D19: v3 classifier — pre-filter URL regex + OVERRIDE prompt signals

**Plany (PLANS/):**
- `PLANS/spo_v1_bootstrap_plan.md` — design+motywacja v1
- `PLANS/spo_v1_todo.md` — phased checklist
- `PLANS/spo_v2_pipe_plan.md` — design v2
- `PLANS/streaming_loader_plan.md` — streaming + cache design
- `SESSIONS_SUMMARY/2026-05-08_spo_v1_design.md` — log sesji projektowej

**A/B running** — `final_results/2026-05-08_19-47-43__spo_v{1,2}_AB_v3/` na pełnych 25667 URL, conc=4 each (total 8 = max vLLM `--max-num-seqs`). Decyzja v1 vs v2 vs hybrid (D20) po wynikach z metrykami: wall, throughput, parse error rate, predicate distribution, central entity precision.

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
