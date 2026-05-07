# PLAN.md — Two-step vLLM pipeline

Plan techniczny eksperymentu. Pełna spec: `INSTRUCTIONS_FROM_CLAUDE.md` (źródło prawdy). Bieżąca lista zadań: `TODO.md`.

## Cel

Walidacja two-step pipeline (entity extraction + SEO meta) na Gemma 4 26B A4B NVFP4 + vLLM, na 100–1000 URL z `websites/`, przed migracją na RTX 5090 i prod runem 21M URL.

## Stage A — DGX Spark (development)

### Phase 0: vLLM setup ✅
**Cel:** vLLM + Gemma 4 NVFP4 odpowiada na 1 request.

- Pobrać `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` (testowany na Spark) do `~/models/gemma4-26b-nvfp4`.
- Postawić docker `vllm/vllm-openai:gemma4-cu130` z `--moe-backend marlin`, `--max-model-len 16384`, `--enable-prefix-caching`.
- Smoke: `curl /v1/chat/completions` na 1 prompt.

**Decision:** vLLM stabilnie odpowiada → przechodzimy do Phase 1.

### Phase 1: HTML cleanup pipeline ✅
**Cel:** zmierzyć skrócenie inputu po markdown ekstrakcji + zdecydować o `include_tables`.

- `lib/data_loader.py`: trafilatura w trybie `output_format="markdown"`, `include_links=True`, `include_formatting=True`, `include_comments=False`, `include_tables=False`.
- Mierzyć dystrybucję długości (znaki + tokeny) na 100 URL: BEFORE (raw HTML), AFTER markdown, AFTER plain text.
- A/B: markdown z `include_tables=True` vs `False` na 30 URL — czy tabele dorzucają wartość czy szum?

**Decision:**
- Mediana skrócenia >40% bez utraty treści → markdown MANDATORY.
- Tabele istotnie zmieniają output Step 1/2 → włączyć; w przeciwnym razie OFF.

### Phase 2: Two-step vs one-step ✅ (one-step pominięty, walidacja przez 100 URL run)
**Cel:** udowodnić, że two-step daje lepsze wyniki niż one-step.

- Implementować Step 1 (entity extraction + language) i Step 2 (SEO meta) — schematy + prompty z `INSTRUCTIONS_FROM_CLAUDE.md`.
- Baseline one-step: jeden prompt zwracający wszystko (refaktoryzacja z `mateusz-g-json-vs-flat/prompts/`).
- 200 URL przez oba pipeline'y. Eyeball: jakość encji, idiomatyczność meta, trudne typy (substance vs therapy, discipline vs activity).

**Decision:**
- Two-step ≥15% lepszy → kontynuujemy two-step.
- <10% różnicy → one-step (taniej, prościej).

### Phase 3: A/B sampling ✅
**Cel:** empirycznie dobrać temp/top_p/top_k.

- Step 1: A=`(1.0, 0.95, 64)` Google default, B=`(0.7, 0.9, 50)`, C=`(0.3, 0.9, 40)` na 100 URL.
- Step 2: A=`(1.0, 0.95, 64)`, B=`(0.8, 0.9, 50)`, C=`(0.5, 0.9, 40)`.
- Metryki: # encji, różnorodność, consistency (3× rerun), eyeball.

**Decision:** Domyślnie A. Niżej tylko jeśli B/C wyraźnie lepsze.

### Phase 4: Prompt iteration ✅
**Cel:** szlifować prompty na podstawie obserwacji z Phase 2–3.

- Add/remove few-shot.
- Refine entity type descriptions (najczęściej mylone: substance↔therapy, discipline↔activity, brand↔organization).
- Tuning `max_tokens`, długości opisów kategorii.
- 50 URL po każdej zmianie.

### Phase 5: End-to-end na 500–1000 URL ⏳
**Cel:** pełna walidacja przed migracją.

- Pełny pipeline: HTML cleanup → Step 1 → entity layer → Step 2 → final.
- Idempotencja (rerun = identyczny output dla deterministic configu).
- Error handling (które URL failują, dlaczego).
- Performance baseline (URL/h na Spark — sanity, nie target).

### Phase 6: Decision gate ⏳
Readiness check:
- ✅ Two-step proven
- ✅ HTML cleanup validated
- ✅ Sampling chosen empirically
- ✅ Prompty stabilne (>3 wersje)
- ✅ E2E na 500–1000 bez crashy
- ✅ Jakość "good enough"

→ Migracja na RunPod RTX 5090.

## Stage B — RunPod RTX 5090 (production, placeholder)

### Phase 7: RunPod setup ⏳
Network Volume 100GB, pobrać `nvidia/Gemma-4-26B-A4B-NVFP4`, skopiować kod, smoke 100 URL.

### Phase 8: Performance test ⏳
5000 URL na 1× 5090. Targets: throughput >2000 t/s/step, prefix cache >70%, e2e <2s/URL.

### Phase 9: Production run ⏳
1× lub 2× 5090, sequential strategy (Step 1 cały → Step 2 cały). Idempotent writes, checkpoints/1000 URL, failed queue, backup S3/GCS, idle timeout. Estymata: 12–15 dni / ~$200–280.

## Decision status (skrót — pełna w INSTRUCTIONS_FROM_CLAUDE.md)

| Element | Decyzja | Status |
|---|---|---|
| Architektura | Two-step | Final, walidacja Phase 2 |
| Model | Gemma 4 26B A4B NVFP4 | Final |
| Sampling baseline | Google defaults (1.0/0.95/64) | Final, A/B Phase 3 |
| Quantization | NVFP4 weights + FP8 KV cache | Final |
| Decoding | guided_json (xgrammar) | Final |
| HTML cleanup | trafilatura markdown | Final, A/B `include_tables` Phase 1 |
| Execution | Sequential (Option A) | Tentative |
| GPU prod | 1× lub 2× 5090 | Decyzja po Phase 8 |
| Fine-tuning | NIE — guided_json + few-shot wystarczą | Final |

## Status faz

Phase 0 ✅ • Phase 1 ✅ • Phase 2 ✅ • Phase 3 ✅ • Phase 4 ✅ • Phase 2 ⏳ • Phase 3 ⏳ • Phase 4 ⏳ • Phase 5 ⏳ • Phase 6 ⏳ • Phase 7–9 ⏳
