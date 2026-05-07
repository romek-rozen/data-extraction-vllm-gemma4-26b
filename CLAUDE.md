# CLAUDE.md

Wskazówki dla Claude Code do pracy w tym repo.

## Projekt

Two-step pipeline ekstrakcji metadanych SEO z 21M URL na DGX Spark (dev) → RTX 5090 (prod). Model: **`nvidia/Gemma-4-26B-A4B-NVFP4`**. Stack: vLLM + `guided_json` (xgrammar) + trafilatura (markdown).

- **Step 1** — uniwersalna ekstrakcja encji + wykrycie języka + kategoria (output → pipe note).
- **Step 2** — generacja meta SEO (title, meta_description, h1, article_summary) w języku artykułu.

## Spec — czytaj zawsze przed implementacją

`INSTRUCTIONS_FROM_CLAUDE.md` to **źródło prawdy**: prompty (full English), JSON Schemas, sampling defaults, decyzje architektoniczne, lista pułapek. Wszystkie zmiany pipeline'u powinny być spójne z tym dokumentem.

`PLAN.md` — plan techniczny per faza. `TODO.md` — actionable checklist.

## Język

Komunikacja z użytkownikiem: **polski**. Prompty do modelu: **English** (~30% mniej tokenów; uniwersalność dla 140+ języków artykułów).

## Stack

- Model: `nvidia/Gemma-4-26B-A4B-NVFP4` (MoE 25.2B total / 3.8B active, NVFP4 weights, FP8 KV cache).
- Inference: vLLM (image `vllm/vllm-openai:gemma4-cu130` na Spark sm_121 z `--moe-backend marlin`).
- Structured output: `guided_json` (xgrammar) — enum constraints na poziomie tokenów.
- Cleanup: `trafilatura` z `output_format="markdown"`, `include_links=True`, `include_formatting=True`.

## Źródło danych

`websites/<sha-hash>/{html.gz, json.gz}` — 1 katalog = 1 URL. `html.gz` = surowy HTML, `json.gz` = metadane (m.in. `url`).

## Struktura katalogów

```
INSTRUCTIONS_FROM_CLAUDE.md   ← spec (źródło prawdy)
PLAN.md, TODO.md, CHANGELOG.md
CLAUDE.md, README.md, LICENCE.md
lib/                          ← data_loader, config, vllm_client, reporter
scripts/                      ← run_step1.py, run_step2.py, run_pipeline.py
prompts/                      ← step1_system.md, step2_system.md, schema_step{1,2}.json
result/                       ← output: entity_layer.jsonl, final.jsonl
dashboard/                    ← (opcjonalny Streamlit, po Phase 5)
websites/<hash>/              ← input
```

## Konwencje

- **Markdown w ekstrakcji** — `output_format="markdown"`, `include_links=True`, `include_formatting=True`, `include_comments=False`, `include_tables=False`. Powód: model ekstrahuje encje + SEO meta — nagłówki/linki/bold to bezpośrednie sygnały. Koszt ~+2,3% długości (~75 tokenów/artykuł).
- **Two-step jako default** — one-step tylko jako baseline porównawczy w Phase 2.
- **Sampling: Google defaults** — `temperature=1.0, top_p=0.95, top_k=64, repetition_penalty=1.0` (NIE 1.2 — łamie powtarzające się klucze JSON). Niższe temperatury tylko z empirycznym dowodem (Phase 3 A/B).
- **Idempotencja** — klucz `url_hash = sha256(url)`; rerun nie duplikuje.
- **Prefix caching ON** — system prompty w cache, `enable_prefix_caching` w vLLM.
- **English prompts** w systemie — opisy enum w system prompt (xgrammar przekazuje tylko enum, nie `description`).
- **Thinking OFF** — `--default-chat-template-kwargs '{"enable_thinking": false}'` w starcie vLLM, plus per-request `chat_template_kwargs: {enable_thinking: false}` w body dla pewności. **Nie używać `--reasoning-parser gemma4`** — łączenie z `enable_thinking=false` wyłącza xgrammar (vLLM issue #39130), a my potrzebujemy guided_json.

## Polecenia dev

```bash
# vLLM (Spark, NVFP4 + Marlin fallback)
docker run -d --gpus all --ipc=host \
  -v ~/models/gemma4-26b-nvfp4:/model \
  -p 8000:8000 vllm/vllm-openai:gemma4-cu130 \
  --model /model --quantization modelopt --kv-cache-dtype fp8 \
  --max-model-len 16384 --gpu-memory-utilization 0.85 \
  --moe-backend marlin --enable-prefix-caching

# Smoke test loadera
python -c "from lib.data_loader import load_articles; \
  arts = load_articles('websites', limit=3); \
  print(arts[0]['text'][:500])"

# Step 1 (po zaimplementowaniu)
python -u scripts/run_step1.py --limit 100

# Pełen pipeline
python -u scripts/run_pipeline.py --limit 500
```

Brak formalnych testów — weryfikacja przez eyeball outputów + sanity check schematów.

## Zależności

```bash
pip install -r requirements.txt
# vllm, trafilatura, xgrammar, requests
```

Wymaga: Python 3.11+, dostęp do GPU (Spark GB10 sm_121 lub RTX 5090 sm_120).

## Pułapki (skrót — pełna lista w INSTRUCTIONS)

- `repeat_penalty=1.2` łamie JSON (powtarzające się klucze) → trzymaj 1.0.
- `description` w JSON Schema nie trafia do modelu (xgrammar) → opisy enum w system prompt.
- Markdown z `include_tables=True` zwiększa koszt znacząco; default OFF, decyzja po Phase 1 A/B.
- Surowy HTML w prompcie zżera tokeny i pogarsza jakość → zawsze trafilatura.
- Niska temperatura bez dowodu = degradacja Gemma 4 (kalibracja Google na 1.0).
