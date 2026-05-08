# FILES.md — repo file index

Last refreshed: 2026-05-08 22:50 CEST (sesja SPO v3 rich-JSON full parallel A/B).

Comprehensive map of every file in the repo with one-line purpose. Source of truth for "where is X?" questions. When you add/move/delete a file, update this file.

## Top-level docs

| File | Purpose |
|---|---|
| `CLAUDE.md` | Wskazówki dla Claude Code — projekt overview, język komunikacji, konwencje, polecenia dev. **Czytaj jako pierwszy.** |
| `README.md` | High-level overview projektu (model, stack, struktura, quick start). |
| `PLAN.md` | Plan techniczny per faza/milestone. Aktualnie: SPO v3 rich-JSON + plan harvest predykatów. |
| `TODO.md` | Actionable checklist. Bieżąca lista zadań. |
| `CHANGELOG.md` | Krótkie streszczenia zmian per data. Pełne summary w `SESSIONS_SUMMARY/`. |
| `DECISIONS.md` | Log decyzji technicznych z uzasadnieniem (D1–D27 obecnie). Każda nietrywialna decyzja → wpis. |
| `INSTRUCTIONS_FROM_CLAUDE.md` | Pełna spec architektury two-step (legacy, źródło prawdy dla Step 1/2 entity extraction + meta). |
| `LICENCE.md` | License. |
| `FILES.md` | Ten plik — inventory. |

## Konfiguracja i scripts setup

| File | Purpose |
|---|---|
| `requirements.txt` | Python deps (vllm, trafilatura, xgrammar, requests, streamlit). |
| `pyproject.toml` | Python project metadata. |
| `.gitignore` | Ignore: `websites/`, `websites_*/`, `websites_cache/`, `result/`, `final_results/`. |
| `.env.example` | Przykładowy template env. |

## Source of truth per pipeline (pipelines)

### Two-step (legacy, Phase 1-5) — entity extraction + meta SEO

| File | Purpose |
|---|---|
| `lib/pipeline.py` | Two-step + threestep enrichment helpers: `enrich_entity` (Azure NER → category+strength), `dedup_entities`, `_clean_metadata`, `TYPE_TO_CATEGORY` (51-type → 11-category mapping). **Reused by SPO pipelines.** |
| `prompts/step1_system_v6.md` | Step 1 system prompt (entity extraction, current active = v6). |
| `prompts/schema_step1_v6.json` | Step 1 JSON schema (entities + language + category). |
| `prompts/step2_system.md` | Step 2 system prompt (SEO meta: title, meta_description, h1, article_summary). |
| `prompts/schema_step2.json` | Step 2 JSON schema. |
| `scripts/run_step1.py` | Run Step 1 standalone. |
| `scripts/run_step2.py` | Run Step 2 standalone. |
| `scripts/run_pipeline.py` | Two-step orchestrator (Step 1 → Step 2). |
| `scripts/run_full.py` | Full E2E orchestrator with auto-snapshot, summary, dashboard view. |

### One-step (Phase 5b A/B comparator)

| File | Purpose |
|---|---|
| `lib/pipeline_onestep.py` | One-step impl: entity + meta in single LLM call. |
| `prompts/step_onestep_system.md` + `schema_onestep.json` | One-step prompt + schema. |
| `scripts/run_onestep.py` | Run one-step standalone. |
| `scripts/compare_onestep_vs_twostep.py` | A/B comparator with quality + speed metrics. |
| `dashboard/views/compare_onestep.py` | Streamlit view for A/B results. |

### Three-step v2 (Phase 6+ orchestration unit)

| File | Purpose |
|---|---|
| `lib/pipeline_threestep_v2.py` | Three-step variant (classify → meta || entities), splits junk filter from extraction. **`process_classify_v2` reused by SPO pipelines as the junk classifier.** |
| `lib/pipeline_threestep.py` | Threestep v1 (deprecated). |
| `scripts/run_threestep.py`, `run_threestep_v2.py`, `run_threestep_v3.py` | Threestep orchestrators (v3 used for sponsored detection iteration). |

### Four-step v1/v2 (current production candidate for non-SPO)

| File | Purpose |
|---|---|
| `lib/pipeline_fourstep_v1.py` | Four-step orchestration: classify → (meta ‖ entities ‖ sponsored). Provides `process_meta_v2`, `process_entities_v2`, `process_sponsored_v1`, `make_junk_stub_final_v4`, `join_final_v4`. **`process_meta_v2` and `process_sponsored_v1` reused by SPO pipelines for parallel meta + sponsored steps.** |
| `prompts/step_classify_system.md`, `step_junkclassify_v2_system.md`, `step_junkclassify_v3_system.md` | Junk classifier prompts (v3 active, with URL pre-filter override rules). |
| `prompts/schema_classify.json` | Classifier schema (binary junk/non-junk). |
| `prompts/step_meta_v2_system.md` + `schema_meta_v2.json` | Meta SEO prompt + schema (language + category + title + meta_description + h1 + article_summary). |
| `prompts/step_entities_v2_system.md` + `schema_entities_v2.json` | Entity-only prompt + schema (Azure NER 51 types). |
| `prompts/step_sponsored_v1_system.md`, `step_sponsored_v2_system.md` | Sponsored classifier (v2 active: paid_placement / brand_mentions / advertorial). |
| `prompts/schema_sponsored_v1.json`, `schema_sponsored_v2.json` | Sponsored schemas. |
| `scripts/run_fourstep_v1.py` | Four-step orchestrator (current active). |
| `scripts/run_full_fourstep.py` | Full E2E with snapshot + summary. |

### SPO v1 / v2 / v3 (current focus — knowledge graph extraction)

#### v1 cram (single-call entities + SPO)

| File | Purpose |
|---|---|
| `lib/spo_pipeline_v1.py` | SPO v1 cram impl: `process_entities_spo` (legacy basic JSON), `make_junk_stub_final_spo`, `join_final_spo`, `_META_FIELDS`, `_merge_meta_into`, `_merge_sponsored_into` (helpers reused by v3). |
| `prompts/spo_entities_v1_system.md` | v1 cram prompt (legacy, simple s/p/o triples). |
| `prompts/spo_schema_v1.json` | v1 cram schema (legacy, basic SPO). |
| `prompts/spo_entities_v3_system.md` | **v3 cram prompt — RICH JSON (active).** primary_topic + central_entities + 9-field triples. |
| `prompts/spo_schema_v3.json` | **v3 cram schema — RICH JSON (active).** xgrammar-enforced. |

#### v2 split (entities_only + spo_pipe)

| File | Purpose |
|---|---|
| `lib/spo_pipeline_v2.py` | SPO v2 split impl: `process_entities_only_v2` (entity-only short call), `process_spo_pipe_v2` (legacy pipe format), `join_final_spo_v2`. |
| `prompts/spo_entities_only_v2_system.md` + `spo_entities_only_v2_schema.json` | Entities-only step (active in v2 split, both legacy pipe and v3 rich). |
| `prompts/spo_pipe_v2_system.md` | **v2 spo_pipe legacy prompt (PIPE format)** — deprecated; preserved for A/B reference. |
| `prompts/spo_pipe_v3_system.md` | **v3 spo_pipe prompt — RICH JSON (active).** |
| `prompts/spo_pipe_v3_schema.json` | **v3 spo_pipe schema — RICH JSON (active).** |

#### v3 unified module (rich JSON impl for both v1 cram and v2 split)

| File | Purpose |
|---|---|
| `lib/spo_pipeline_v3.py` | `process_entities_spo_v3` (cram), `process_spo_pipe_v3` (split), `join_final_v3`, `_normalize_rich_triple`, `_dedup_rich_triples`, `_validate_triples_against_entities`. Re-exports `make_junk_stub_final_spo` and `process_classify_v2`. |

#### Orchestrators

| File | Purpose |
|---|---|
| `scripts/run_spo_v1.py` | spo_v1 orchestrator (cram). Loads v3 prompts/schema. Includes meta + sponsored parallel steps + drain-first worker. |
| `scripts/run_spo_v2.py` | spo_v2 orchestrator (split: entities_only + spo_pipe + meta + sponsored). Loads v3 prompts/schema. |
| `scripts/run_spo_v1_v2_test.py` | **Master orchestrator: parallel A/B v1+v2 on full sample with separate cache pre-warm timing.** D27. |
| `scripts/spo_v3_overnight.sh` | Earlier sequential overnight script (deprecated by run_spo_v1_v2_test.py for full parallel). |
| `scripts/spo_summary_v1.py` | Auto-summary: top predicates, central entities, type×is_central, sample triples. Called by run_spo_v{1,2}.py at end. |
| `scripts/spo_compare_benches.py` | v1 vs v2 markdown comparison report (wall, triples, predicates, confidence). |

## Library code (`lib/`)

| File | Purpose |
|---|---|
| `lib/__init__.py` | Empty package init. |
| `lib/config.py` | Constants: `VLLM_BASE_URL`, `VLLM_MODEL`, `WEBSITES_DIR`, `FINAL_RESULT_DIR`, `MAX_TOKENS_STEP1/2`, `SAMPLING_STEP1/2`. |
| `lib/data_loader.py` | `load_articles` (sync, bulk), `extract_markdown_from_html_gz` (trafilatura wrapper), `load_url_info_from_json_gz`, `_list_article_dirs`. |
| `lib/streaming_loader.py` | `stream_articles_async` — generator yielding articles via parallel ThreadPool, with disk cache (`websites_cache/<hash>.json` JSON envelope storing markdown body). Used by SPO pipelines + warmup stage. |
| `lib/junk_pre_filter.py` | `is_definite_url_junk` (regex: tag/author/paginated URLs) + `build_junk_stub`. Saves LLM calls on definite junk. |
| `lib/prompt_loader.py` | `load_system_prompt(name)`, `load_schema(name)` — read + cache. |
| `lib/reporter.py` | `JsonlReporter` thread-safe append + `load_existing_hashes` (idempotency). |
| `lib/tokenizer.py` | Token counting helpers (vLLM `/tokenize` endpoint or local). |
| `lib/vllm_client.py` | `VLLMClient.chat_json` — main entry point for guided_json calls. |
| `lib/pipeline.py` | Shared enrichment helpers: `enrich_entity`, `dedup_entities`, `_clean_metadata`, `TYPE_TO_CATEGORY`. |
| `lib/pipeline_onestep.py` | One-step impl (Phase 5b). |
| `lib/pipeline_threestep.py`, `lib/pipeline_threestep_v2.py` | Threestep orchestration impl. |
| `lib/pipeline_fourstep_v1.py` | Fourstep orchestration impl. |
| `lib/spo_pipeline_v1.py`, `lib/spo_pipeline_v2.py`, `lib/spo_pipeline_v3.py` | SPO pipelines (v1 cram, v2 split, v3 unified rich-JSON). |

## Scripts (`scripts/`)

### Orchestrators (run pipelines end-to-end)

| Script | Purpose |
|---|---|
| `run_pipeline.py` | Two-step E2E (Step 1 → Step 2). |
| `run_full.py` | Two-step E2E + snapshot + summary. |
| `run_step1.py`, `run_step2.py` | Per-step runners (debug). |
| `run_onestep.py` | One-step. |
| `run_threestep.py`, `run_threestep_v2.py`, `run_threestep_v3.py` | Three-step variants. |
| `run_fourstep_v1.py` | Four-step (classify → meta‖entities‖sponsored). |
| `run_full_fourstep.py` | Four-step E2E + snapshot. |
| `run_spo_v1.py` | SPO v1 cram orchestrator (now using v3 rich-JSON prompts). |
| `run_spo_v2.py` | SPO v2 split orchestrator (now using v3 rich-JSON prompts). |
| `run_spo_v1_v2_test.py` | SPO v1+v2 parallel A/B with separate cache warmup timing. |

### Analysis + comparators

| Script | Purpose |
|---|---|
| `analyze_phase2.py`, `analyze_phase3.py` | Phase 2/3 statistics + sample dump. |
| `analyze_entity_quality.py` | Top-N entity names per type from entity_layer.jsonl. |
| `compare_onestep_vs_twostep.py` | One-step vs two-step quality + speed report. |
| `compare_prompt_versions.py` | Prompt version A/B (entity stability). |
| `spo_compare_benches.py` | v1 vs v2 SPO bench comparator (markdown report). |
| `spo_summary_v1.py` | SPO post-run summary (predicates, central entities, sample triples). |

### Sampling + measurement

| Script | Purpose |
|---|---|
| `ab_sampling.py` | Step 1/2 sampling A/B/C runner. |
| `measure_lengths.py`, `measure_prompt_tokens.py` | Token measurement (Phase 1). |
| `snapshot_metrics.py` | Workaround for vLLM `prompt_tokens_details: null` bug. |
| `wall_time.py` | Wall-time measurement helpers. |
| `finalize_compare.py` | Finalize per-run comparison report. |

### Test + smoke

| Script | Purpose |
|---|---|
| `smoke_test.sh` | Bash smoke for vLLM (math + JSON mode). |
| `test_streaming_loader.py` | Streaming loader smoke. |

### Operational

| Script | Purpose |
|---|---|
| `start_vllm.sh` | Start vLLM Gemma 4 docker container with sm_121 patch. |
| `spo_v3_overnight.sh` | Sequential overnight bench v1 → v2 → full (deprecated by run_spo_v1_v2_test.py). |

## Prompts + schemas (`prompts/`)

### Two-step (legacy)

| File | Purpose |
|---|---|
| `step1_system.md` | Active link to current Step 1 system. |
| `step1_system_v1.md` … `step1_system_v6.md` | Step 1 prompt versions (v1=initial, v6=current with 9 disambiguation rules). |
| `step1_system_v3_no_meta.md` | Variant without metadata for narrow Quantity types. |
| `step1_system_v4_backup.md`, `step1_system_v5_backup.md` | Backups before sweeping changes. |
| `step2_system.md` | Step 2 SEO meta. |
| `schema_step1.json` … `schema_step1_v6.json` | Step 1 schemas (v6 active). |
| `schema_step1_v5_backup.json` | Backup. |
| `schema_step2.json` | Step 2 schema. |

### Onestep, threestep, fourstep

| File | Purpose |
|---|---|
| `step_onestep_system.md` + `schema_onestep.json` | One-step. |
| `step_classify_system.md` | Classifier (binary). |
| `step_junkclassify_v2_system.md`, `step_junkclassify_v3_system.md` | Junk classifier (v3 active, URL signals override). |
| `schema_classify.json` | Classifier schema. |
| `step_meta_v2_system.md` + `schema_meta_v2.json` | Meta SEO (active). |
| `step_entities_v2_system.md` + `schema_entities_v2.json` | Entities only (active). |
| `step_sponsored_v1_system.md`, `step_sponsored_v2_system.md` | Sponsored detection (v2 active). |
| `schema_sponsored_v1.json`, `schema_sponsored_v2.json` | Sponsored schemas. |

### SPO

| File | Purpose |
|---|---|
| `spo_entities_v1_system.md` + `spo_schema_v1.json` | v1 cram prompt+schema (legacy basic JSON triples). |
| `spo_entities_only_v2_system.md` + `spo_entities_only_v2_schema.json` | v2 entities-only step (used by both legacy and v3 rich). |
| `spo_pipe_v2_system.md` | v2 spo_pipe **PIPE FORMAT** prompt (legacy, deprecated, kept for A/B reference). |
| `spo_pipe_v3_system.md` + `spo_pipe_v3_schema.json` | **v3 spo_pipe RICH JSON (active)** — primary_topic + central_entities + 9-field triples. |
| `spo_entities_v3_system.md` + `spo_schema_v3.json` | **v3 cram RICH JSON (active)** — entities + primary_topic + central_entities + 9-field triples. |

## Plans (`PLANS/`)

| File | Purpose |
|---|---|
| `next_session_plan.md` | Generic placeholder. |
| `output_schema.md` | JSONL output schema reference. |
| `rtx_pro_6000_optimization.md` | Notes for production migration. |
| `sponsored_detection_plan.md` | Sponsored detection plan. |
| `streaming_loader_plan.md` | Streaming loader design. |
| `step3_spo_triplets.md` | Original SPO step3 design (pre-v1). |
| `threestep_pipeline_plan.md`, `threestep_pipeline_todo.md` | Three-step pipeline plan + TODO. |
| `spo_v1_bootstrap_plan.md`, `spo_v1_todo.md` | SPO v1 bootstrap plan. |
| `spo_v2_pipe_plan.md` | SPO v2 pipe-format design. |
| `spo_rich_json_v3_plan.md` | **v3 rich-JSON design (this session).** |
| `spo_v3_full_parallel_plan.md` | **v3 full parallel A/B run plan (current session).** |
| `spo_predicate_refinement_plan.md` | TODO for v4 closed predicate enum after harvest. |
| `junk_examples_v2.json`, `junk_examples_v2_curated.json` | Junk classifier training examples. |

## Sessions summary (`SESSIONS_SUMMARY/`)

Per-session writeups (chronological).

| File | Purpose |
|---|---|
| `2026-05-08_spo_v1_design.md` | SPO v1 (cram) design session. |
| `2026-05-08_spo_rich_json.md` | SPO v3 rich-JSON design + smoke results. |
| `2026-05-08_overnight_master.log` | Master overnight orchestrator log. |

## Dashboard (`dashboard/`)

| File | Purpose |
|---|---|
| `main.py` | Streamlit entry. |
| `views/__init__.py` | View registry. |
| `views/run_summary.py` | Per-run summary view. |
| `views/articles.py` | Article browser. |
| `views/entities.py` | Entity statistics view. |
| `views/categories.py` | Category distribution. |
| `views/analytics.py` | Cross-run analytics. |
| `views/drift.py` | Domain drift detection. |
| `views/logs.py` | Log viewer. |
| `views/compare_runs.py` | Compare two runs. |
| `views/compare_onestep.py` | One-step vs two-step view. |
| `views/sponsored.py` | Sponsored articles view. |
| `views/junk_analysis.py` | Per-domain junk ratio. |
| `views/spo.py` | SPO knowledge graph view. |

## Data dirs (gitignored)

| Dir | Purpose |
|---|---|
| `websites/<hash>/{html.gz, json.gz}` | Input articles (1 dir = 1 URL). 25667 dirs total. |
| `websites_cache/<hash>.json` | Cached trafilatura markdown extracts. JSON envelope `{domain, url, content}` where `content` is markdown. Cleared between cold-cache benchmarks. |
| `final_results/<ts>__<pipeline>_<tag>/` | Per-run outputs: `final.jsonl`, `classified.jsonl`, `entities.jsonl`, `spo.jsonl`, `meta.jsonl`, `sponsored.jsonl`, `*.log`, `run_meta.json`, `summary.txt`, `timing.csv`, `stdout.log`. |
| `final_results/<ts>__spo_v1_v2_test_<tag>/` | Master dir for parallel A/B: `cache_warmup_meta.json`, `run_log.txt`, `v1_dir.txt`, `v2_dir.txt`, `comparison_report.md`, plus per-pipeline subproc logs. |
| `result/` | Older Phase 1-4 outputs (legacy). |

## Models dir (outside repo)

| Dir | Purpose |
|---|---|
| `~/models/gemma4-26b-nvfp4-bg/` | Gemma 4 26B A4B NVFP4 weights (BG variant for sm_121 / Spark). |
| `~/models/gemma4-26b-nvfp4/` | Gemma 4 26B A4B NVFP4 (NVIDIA original, for prod RTX 5090/6000). |

## What you generally edit when

- **New pipeline variant** → new `lib/<pipe>.py` + `prompts/<pipe>_*.{json,md}` + `scripts/run_<pipe>.py`.
- **New prompt iteration** → bump version in filename (`*_v3_system.md`), keep old as backup.
- **New decision** → append D{N+1} to `DECISIONS.md`.
- **New session work** → new file in `SESSIONS_SUMMARY/<date>_<topic>.md`.
- **Major refactor** → `PLANS/<topic>_plan.md` first.
- **Add a file/move/delete** → update THIS file (`FILES.md`).
