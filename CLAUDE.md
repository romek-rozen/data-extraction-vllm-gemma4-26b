# CLAUDE.md

Wskazówki dla Claude Code do pracy w tym repo.

## Projekt

Two-step pipeline ekstrakcji metadanych SEO z 21M URL na DGX Spark (dev) → RTX 5090 (prod). Model: **`nvidia/Gemma-4-26B-A4B-NVFP4`**. Stack: vLLM + xgrammar (`response_format: json_schema`) + trafilatura (markdown).

- **Step 1** — ekstrakcja encji + wykrycie języka + kategoria (output → pipe note). Schema: Microsoft Azure NER (51 typów + 11 kategorii high-level + strong/weak + metadata).
- **Step 2** — generacja meta SEO (title, meta_description, h1, article_summary) w języku artykułu.

## Schema encji (Azure NER)

```json
{
  "name": "190°C",
  "type": "Temperature",      // 1 of 51 Azure types
  "category": "Quantity",      // high-level group (deterministic from type)
  "strength": "weak",          // strong (Wikidata-linkable) / weak (kontekstowo-zależna)
  "metadata": {"unit": "Celsius", "value": 190}  // optional, for 18 Quantity/DateTime types
}
```

`category` i `strength` mapowane deterministycznie po typie w `lib/pipeline.py:TYPE_TO_CATEGORY`. `metadata` cleanup (whitelist per typ) w `_clean_metadata()`.

## Spec — czytaj zawsze przed implementacją

`INSTRUCTIONS_FROM_CLAUDE.md` to **źródło prawdy**: prompty (full English), JSON Schemas, sampling defaults, decyzje architektoniczne, lista pułapek. Wszystkie zmiany pipeline'u powinny być spójne z tym dokumentem.

`PLAN.md` — plan techniczny per faza. `TODO.md` — actionable checklist. `DECISIONS.md` — log decyzji z uzasadnieniem (co, dlaczego, oparte na czym).

**Reguła:** każda nietrywialna decyzja techniczna (zmiana parametru samplingu, flagi vLLM, schematu JSON, biblioteki, layoutu) → wpis w `DECISIONS.md` zgodnie z formatem na końcu tego pliku. Bez wpisu decyzja nie istnieje — kolejne osoby (i sesje Claude) nie znają kontekstu, dlaczego coś jest właśnie tak.

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

- **Markdown w ekstrakcji** — `output_format="markdown"`, `include_links=True`, `include_formatting=True`, `include_comments=False`, `include_tables=True`. Powód: model ekstrahuje encje + SEO meta — nagłówki/linki/bold/tabele to bezpośrednie sygnały. Koszt vs plain text ~+2,86% mediana (Phase 1 pomiar).
- **Two-step jako default** — one-step tylko jako baseline porównawczy w Phase 2.
- **Sampling: Google defaults** — `temperature=1.0, top_p=0.95, top_k=64, repetition_penalty=1.0` (NIE 1.2 — łamie powtarzające się klucze JSON). Niższe temperatury tylko z empirycznym dowodem (Phase 3 A/B).
- **Idempotencja** — klucz `url_hash = sha256(url)`; rerun nie duplikuje.
- **Prefix caching ON** — system prompty w cache, `enable_prefix_caching` w vLLM.
- **English prompts** w systemie — opisy enum w system prompt (xgrammar przekazuje tylko enum, nie `description`).
- **Thinking OFF** — `--default-chat-template-kwargs '{"enable_thinking": false}'` w starcie vLLM, plus per-request `chat_template_kwargs: {enable_thinking: false}` w body dla pewności. **Nie używać `--reasoning-parser gemma4`** — łączenie z `enable_thinking=false` wyłącza xgrammar (vLLM issue #39130), a my potrzebujemy guided_json.

## Polecenia dev

```bash
# vLLM startup (gotowy skrypt z patchem sm_121, port 8001, thinking OFF)
bash scripts/start_vllm.sh
docker logs -f vllm-gemma4   # czekaj na "Application startup complete"
bash scripts/smoke_test.sh    # math + JSON mode sanity

# Smoke test loadera
python3 -c "from lib.data_loader import load_articles; \
  arts = load_articles('websites', limit=3); \
  print(arts[0]['text'][:500])"

# Pełen E2E (mkdir + snapshot + Step 1 + Step 2 + analiza)
python3 -u scripts/run_full.py --limit 0 --concurrency 8                     # auto: final_results/<timestamp>/
python3 -u scripts/run_full.py --limit 0 --concurrency 8 --tag v6_baseline   # final_results/<ts>__v6_baseline/
python3 -u scripts/run_full.py --resume                                      # wznów najnowszy run (idempotencja po url_hash, fails są ponawiane)

# Dashboard Streamlit (analiza wyników z final_results/)
streamlit run dashboard/main.py --server.address 0.0.0.0 --server.port 8501

# Pojedyncze fazy
python3 -u scripts/run_step1.py --limit 100 --concurrency 8
python3 -u scripts/run_step2.py --limit 100 --concurrency 8

# A/B sampling
python3 scripts/ab_sampling.py --step 1 --limit 100 --concurrency 8

# Analiza wyników
python3 scripts/analyze_phase2.py --samples 10
python3 scripts/analyze_entity_quality.py --top 30
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
- Markdown z `include_tables=True` (decyzja Phase 1, D5 w DECISIONS.md) — koszt p95 +2,5%, zysk: tabele to sygnał porównań/specyfikacji.
- Surowy HTML w prompcie zżera tokeny i pogarsza jakość → zawsze trafilatura.
- Niska temperatura bez dowodu = degradacja Gemma 4 (kalibracja Google na 1.0).
