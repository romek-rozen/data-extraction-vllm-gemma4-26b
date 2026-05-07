# TODO.md — Two-step vLLM pipeline

Actionable checklist per faza. Plan: `PLAN.md`. Spec: `INSTRUCTIONS_FROM_CLAUDE.md`.

## Phase 0 — vLLM setup ✅

- [x] Pobrać `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` do `~/models/gemma4-26b-nvfp4-bg`
- [x] Patch `gemma4_patched.py` dostępny w katalogu modelu (mountowany w skrypcie)
- [x] `scripts/start_vllm.sh`: `vllm/vllm-openai:gemma4-cu130`, port 8001, `--moe-backend marlin`, `--max-model-len 24576`, `--gpu-memory-utilization 0.85`, `--enable-prefix-caching`, `--kv-cache-dtype fp8`, `--default-chat-template-kwargs '{"enable_thinking": false}'`
- [x] Healthcheck: `curl http://localhost:8001/v1/models` → `max_model_len: 24576`
- [x] Smoke: math (`12*17 → 204`, ~300ms) + JSON mode (`{"language": "pl"}`, 2s, `reasoning: null`)

## Phase 1 — HTML cleanup pipeline ✅

- [x] `lib/config.py`: `WEBSITES_DIR`, `RESULT_DIR`, `TEXT_TRUNCATE_LIMIT=80000`
- [x] `lib/data_loader.py`: trafilatura markdown + url/domain/path z json.gz
- [x] Smoke: `load_articles` zwraca markdown z `## nagłówkami`, `**bold**`
- [x] `scripts/measure_lengths.py` na 100 URL: tokeny przez vLLM `/tokenize` (dokładny tokenizer Gemma 4)
- [x] **Decyzja**: cleanup MANDATORY (98,45% redukcja), markdown ON (+2,86% overhead), `include_tables=False` (outlier 109%)
- [x] Wyniki: `result/phase1_lengths.md` + `result/phase1_lengths.json`
- [ ] A/B `include_tables` True vs False — opcjonalne, Phase 2 jeśli będą problemy z brakiem kontekstu z tabel

## Phase 2 — Two-step vs one-step ✅

- [x] `prompts/step1_system.md`, `step2_system.md`, schematy JSON
- [x] `lib/vllm_client.py` (response_format json_schema, thinking OFF)
- [x] `lib/prompt_loader.py` (cache + placeholder substitution)
- [x] `lib/reporter.py` (thread-safe JSONL z idempotencją)
- [x] `scripts/run_step1.py`, `run_step2.py`, `run_pipeline.py`
- [x] `scripts/analyze_phase2.py` (statystyki + sample do eyeballa)
- [x] `scripts/snapshot_metrics.py` (workaround dla `prompt_tokens_details: null`)
- [x] **100 URL run @ concurrency 4: 100/100 OK** (Step 1 + Step 2)
- [x] Throughput: 1,73 s/req amortized | Prefix cache hit rate: 72,2%
- [x] Wnioski: `result/phase2_twostep.md` (encje median 15/max 33, output max 763 tok)
- [-] One-step baseline POMINIĘTY — D7 zaktualizowany (smoke 3+100 URL pokazuje wystarczającą jakość)

## Phase 3 — A/B sampling ✅

- [x] `scripts/ab_sampling.py` + `analyze_phase3.py` + `lib/pipeline.py` (refaktor)
- [x] Step 1 A/B/C × 100 URL: 100/100 OK we wszystkich, encje median 14-15, 94% URL→ta sama kategoria
- [x] Step 2 A/B/C × 100 URL: A 99/100 (1 zapętlenie), B,C 100/100, diversity ~100/100
- [x] Consistency 5 URL × 3 reruns × A,C: 0/5 pełna identyczność nawet przy temp 0.3 — **niska temp NIE daje determinizmu na Marlin sm_121**
- [x] **Decyzja D12:** Step 1 zostaje 1.0, Step 2 → 0.8 (eliminuje zapętlenia)
- [x] **Decyzja D13:** idempotencja przez `url_hash` skip, nie deterministic rerun
- [x] Wyniki: `result/phase3_compare.md`

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
