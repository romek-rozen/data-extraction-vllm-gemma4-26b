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

## Phase 4 — Prompt iteration ✅

- [x] `scripts/analyze_entity_quality.py` — top N nazw per typ z entity_layer.jsonl
- [x] Identified problems: anatomia w `structure`, academic w `discipline`, products w `other`, condition w `therapy`, NGO w `brand`
- [x] Prompt v2 z wzmocnionymi disambiguation rules + 9 nowych negative examples
- [x] Versioning: `prompts/step1_system_v1.md` (backup), `step1_system.md` (= v2 aktywne)
- [x] Run 50 URL z v2: 50/50 OK
- [x] `scripts/compare_prompt_versions.py`: **6 problemów → 0**, 570/597 encji stabilnych
- [x] **Decyzja D14:** v2 aktywne; koszt +24% tokenów systemu (cached → amortyzowany)
- [x] Wyniki: `result/phase4_compare.md` + `result/phase4_entity_quality.md`

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

## Phase 5b — One-step vs two-step revisit

Cel: zmierzyć speedup vs koszt jakości one-step na obecnym promptcie/schemie v6. Bez zmian w produkcyjnej ścieżce two-step.

- [x] `prompts/schema_onestep.json` — schema łącząca Step1 + Step2 (entities, language, category, title, meta_description, h1, article_summary)
- [x] `prompts/step_onestep_system.md` — system prompt (English; junkey → puste/placeholder meta)
- [x] `lib/pipeline_onestep.py` — `process_onestep()` z dedup_entities + enrich_entity (reuse z lib/pipeline.py)
- [x] `scripts/run_onestep.py` — runner idempotentny po url_hash, sampling=SAMPLING_STEP1, max_tokens=NUM_PREDICT (4096)
- [x] `scripts/compare_onestep_vs_twostep.py` — uruchamia oba pipeline'y na tym samym sample, generuje `report.md` z metrykami speed/quality
- [ ] Smoke 5 URL: `python3 scripts/compare_onestep_vs_twostep.py --limit 5 --concurrency 4 --tag smoke` — sanity (czy schema xgrammar nie wybucha, czy SEO meta jest w języku artykułu)
- [ ] Run 20 URL: `python3 scripts/compare_onestep_vs_twostep.py --limit 20 --concurrency 4 --tag mini` — pierwszy realny pomiar
- [ ] Run 100 URL (po pozytywnym mini): `--limit 100 --concurrency 8 --tag baseline100`
- [ ] Sanity check na losowej próbce: `--limit 30 --random --seed 7 --tag rand7` — sprawdza czy first-N nie jest niereprezentatywny
- [ ] Eyeball 10 sample diff (one vs two: title/meta_desc/encje) — czy jakość jednego jest tańsza niż drugiego
- [ ] Wpis do `DECISIONS.md` (uzupełnienie D7) z liczbami: speedup wall, per-URL latency, category/lang match %, Jaccard, fail rate
- [ ] Decyzja: one-step prod-kandydat / odrzucony / dalsza iteracja promptu one-step

## Phase 6 — Embeddings + HDBSCAN clustering

Cel: przyspieszyć kategoryzację artykułów. Embedding → HDBSCAN → klastry → meta-kategorie.

### Setup vLLM dual-container
- [x] `scripts/start_vllm_llm_plus_embedding.py` — orchestrator dual-container (Gemma + Qwen embed) z health-wait, GPU_MEM=0.60/0.20 split na Sparku
- [x] `scripts/embed_articles.py` — klient `/v1/embeddings` z idempotencją po url_hash, doc_text = `{h1}\n{summary}\n{strong ∪ central entities deduped}`
- [x] Pobranie modelu: `hf download Qwen/Qwen3-Embedding-4B --local-dir ~/models/qwen3-embedding-4b` (~8 GB bf16)
- [x] Decyzja modelu i dtype: 4B + bf16 (D30 — patrz `DECISIONS.md`)
- [ ] Smoke `/v1/embeddings` na 5 doc po starcie kontenera
- [ ] Pełen embedding 22 582 non-junk artykułów (`scripts/embed_articles.py --batch-size 16 --concurrency 8`)
- [ ] Pomiar wall_s + docs/s; ETA dla 1M / 21M URL

### Filtr typów encji do embedding (opcjonalnie)
- [ ] Decyzja: czy odsiać 14 typów Azure NER <0.1% (`Height`, `Ordinal`, `OrganizationStockExchange`, `SetTemporal`, `DateTimeRange`, `IpAddress`, `DateTime`, `Airport`, `SportsEvent`, `TimeRange`, `Speed`, `Email`, `PhoneNumber`, `Area`)? Obecny default to strong ∪ central — weak pomijane.

### HDBSCAN
- [ ] `scripts/cluster_articles.py` — load `embeddings.npy` + `manifest.jsonl`, L2-norm + cosine→euclidean, HDBSCAN `min_cluster_size=20-50`
- [ ] Output: `manifest_clustered.jsonl` + raport `clusters_summary.md` (top topics, top entities per cluster, n_noise)
- [ ] Sanity check: ręczny eyeball top 10 klastrów (czy są semantyczne, czy zlepki nieskorelowanych)
- [ ] Decyzja: czy klastry zastąpią/uzupełnią `category` z Gemma meta (`DECISIONS.md`)

### LLM-judge eval SPO triples (po embeddings, ortogonalne)
- [ ] `scripts/eval_triples_llm_judge.py` — 100-500 próbka, każda triple z v1 i v2 → judge prompt → score
- [ ] Wyniki: precision/recall per pipeline, decyzja v1 vs v2 final (uzupełnienie D29)

## Otwarte decyzje

- [x] `include_tables` w trafilatura (Phase 1 A/B)
- [x] `max_model_len` (16384 wystarczy czy mniej dla większego batcha?)
- [x] `max_num_seqs` na Sparku (start 8, do testu 16)
- [ ] Streamlit dashboard (Phase 5+, opcjonalny)
- [ ] SPO v1 vs v2 final (po LLM-judge eval; preliminary v1 — D29)
- [ ] Filtrowanie weak/rare encji przed embedding (po pierwszym HDBSCAN runie)
