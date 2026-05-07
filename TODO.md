# TODO.md — Two-step vLLM pipeline

Actionable checklist per faza. Plan: `PLAN.md`. Spec: `INSTRUCTIONS_FROM_CLAUDE.md`.

## Phase 0 — vLLM setup ✅

- [x] Pobrać `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` do `~/models/gemma4-26b-nvfp4-bg`
- [x] Patch `gemma4_patched.py` dostępny w katalogu modelu (mountowany w skrypcie)
- [x] `scripts/start_vllm.sh`: `vllm/vllm-openai:gemma4-cu130`, port 8001, `--moe-backend marlin`, `--max-model-len 24576`, `--gpu-memory-utilization 0.85`, `--enable-prefix-caching`, `--kv-cache-dtype fp8`, `--default-chat-template-kwargs '{"enable_thinking": false}'`
- [x] Healthcheck: `curl http://localhost:8001/v1/models` → `max_model_len: 24576`
- [x] Smoke: math (`12*17 → 204`, ~300ms) + JSON mode (`{"language": "pl"}`, 2s, `reasoning: null`)

## Phase 1 — HTML cleanup pipeline

- [ ] `lib/config.py`: `WEBSITES_DIR="websites"`, `RESULT_DIR="result"`, `TEXT_TRUNCATE_LIMIT` (do ustalenia po pomiarze)
- [x] `lib/data_loader.py`: trafilatura markdown z `include_links=True, include_formatting=True, include_comments=False, include_tables=False`
- [ ] Smoke: `load_articles('websites', limit=3)` zawiera `# `, `## `, `[anchor](url)`
- [ ] `scripts/measure_lengths.py` na 100 URL: dystrybucja znaków + tokenów (median, p95, max) BEFORE / AFTER markdown / AFTER plain
- [ ] A/B `include_tables` True vs False (30 URL): czy tabele dorzucają sygnał?
- [ ] **Decyzja** w `PLAN.md` + tabela wyników w `result/phase1_lengths.md`

## Phase 2 — Two-step vs one-step

- [ ] `prompts/step1_system.md` (full English z INSTRUCTIONS sekcja "STEP 1")
- [ ] `prompts/step2_system.md` (full English z INSTRUCTIONS sekcja "STEP 2")
- [ ] `prompts/schema_step1.json` + `prompts/schema_step2.json`
- [ ] `lib/vllm_client.py` (OpenAI-compat klient, `guided_json`, retry na timeout)
- [ ] `lib/prompt_loader.py` (placeholder substitution dla Step 2: `{detected_language}`, `{category}`, `{entities_summary}`)
- [ ] `lib/reporter.py` (thread-safe JSONL append, klucz `url_hash`)
- [ ] `scripts/run_step1.py --limit N` → `result/entity_layer.jsonl`
- [ ] `scripts/run_step2.py --limit N` (czyta entity_layer) → `result/final.jsonl`
- [ ] `scripts/run_pipeline.py` (Step 1 + Step 2 sekwencyjnie)
- [ ] One-step baseline w `scripts/run_one_step.py` (refaktoryzacja z `mateusz-g-json-vs-flat`)
- [ ] 200 URL w obu trybach, eyeball jakości
- [ ] **Decyzja**: two-step vs one-step

## Phase 3 — A/B sampling

- [ ] `scripts/ab_sampling.py --step 1 --configs A,B,C`
- [ ] Step 1: A=(1.0, 0.95, 64), B=(0.7, 0.9, 50), C=(0.3, 0.9, 40) × 100 URL
- [ ] Step 2: A=(1.0, 0.95, 64), B=(0.8, 0.9, 50), C=(0.5, 0.9, 40) × 100 URL
- [ ] Metryki: # encji, różnorodność, consistency (3× rerun)
- [ ] Eyeball "stupidity check"
- [ ] **Decyzja** zapisana w `PLAN.md` + `result/phase3_sampling.md`

## Phase 4 — Prompt iteration

- [ ] Lista najczęściej mylonych typów (substance↔therapy, discipline↔activity, brand↔organization)
- [ ] Add/remove few-shot per znalezione problemy
- [ ] Po każdej zmianie: 50 URL + eyeball
- [ ] Wersjonowanie promptów (`prompts/step1_system_v2.md`, ...)

## Phase 5 — End-to-end 500–1000 URL

- [ ] Pełny pipeline na 500 URL → eyeball 50
- [ ] Test idempotencji (rerun → identyczny output dla deterministic configu)
- [ ] Failed queue (URL crashujące + powód)
- [ ] Performance baseline (URL/h, t/s, prefix cache hit rate)
- [ ] Skala do 1000 URL bez crashu

## Phase 6 — Decision gate

- [ ] Wszystkie ✅ z PLAN.md sekcja Phase 6
- [ ] Decyzja: ready dla RunPod 5090 lub iteracja

## Phase 7–9 — Migration RunPod (placeholder)

- [ ] Network Volume 100GB
- [ ] Pobrać `nvidia/Gemma-4-26B-A4B-NVFP4`
- [ ] Performance test 5000 URL
- [ ] Production run 21M URL

## Otwarte decyzje

- [ ] `include_tables` w trafilatura (Phase 1 A/B)
- [ ] `max_model_len` (16384 wystarczy czy mniej dla większego batcha?)
- [ ] `max_num_seqs` na Sparku (start 8, do testu 16)
- [ ] Streamlit dashboard (Phase 5+, opcjonalny)
