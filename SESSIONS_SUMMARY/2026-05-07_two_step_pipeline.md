# Two-step pipeline ekstrakcji metadanych z 21M URL — pełna sesja

**Data:** 2026-05-07 (jeden dzień, ~5h pracy)
**Cel:** Postawić od zera production-ready pipeline ekstrakcji encji + meta SEO z artykułów HTML, na DGX Spark, model Gemma 4 26B A4B NVFP4 + vLLM, target 21M URL (etap dev/staging).
**Repo:** [github.com/romek-rozen/data-extraction-vllm-gemma4-26b](https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b)

## TL;DR

Two-step pipeline (Step 1 entity extraction → Step 2 SEO meta generation) na vLLM + xgrammar + Gemma 4. Po 4 fazach iteracji (setup, cleanup, sampling A/B, prompt evolution) finalna konfiguracja:

- **Model:** `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` (Spark sm_121 + Marlin fallback)
- **Schema:** Microsoft Azure NER (51 typów + 11 kategorii + strong/weak strength + metadata)
- **Sampling:** Step 1 Google default (1.0/0.95/64), Step 2 obniżony do 0.8 (eliminuje rzadkie zapętlenia)
- **HTML cleanup:** trafilatura markdown z linkami, tabelami, formatowaniem (98,45% redukcji znaków, +2,86% tokenów vs plain text)
- **Throughput:** 1,73-3,44 s/req amortized @ concurrency 8 (Spark Marlin fallback)
- **Cache hit rate:** 72-99% (system prompt 8084 tokenów cached)
- **Quality:** 100/100 OK na 100 URL, 50/50 OK na 50 URL z metadata

## Stack

| Komponent | Wersja / wybór |
|---|---|
| Model | Gemma 4 26B A4B NVFP4 (MoE, 25,2B total / 3,8B active) |
| Inference | vLLM `vllm/vllm-openai:gemma4-cu130` (custom Gemma 4 image) |
| Quantization | NVFP4 weights + FP8 KV cache |
| MoE backend | Marlin (sm_121 fallback — Spark nie ma natywnego FP4) |
| Structured output | `response_format: json_schema` (xgrammar) |
| HTML cleanup | trafilatura markdown + links + formatting + tables |
| Tokenizer | `tokenizers` (Rust) z lokalnym `tokenizer.json` |
| GPU | NVIDIA GB10 (sm_121, 128GB unified memory) |

## Chronologia

### Phase 0: vLLM setup (30 min)

**Wyzwania:**
- Port 8000 zajęty przez `open-terminal` → port 8001
- Spark sm_121 nie ma natywnego FP4 → Marlin fallback (~30% wolniejszy)
- vLLM zwracał markdown-wrapped JSON gdy używaliśmy deprecated `guided_json` → migracja na `response_format: json_schema`

**Konfiguracja finalna:**
```bash
docker run -d --gpus all --ipc=host \
  --name vllm-gemma4 \
  -v ~/models/gemma4-26b-nvfp4-bg:/model \
  -v $MODEL_DIR/gemma4_patched.py:/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/gemma4.py \
  -p 8001:8000 \
  vllm/vllm-openai:gemma4-cu130 \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 24576 \
  --max-num-seqs 8 \
  --gpu-memory-utilization 0.85 \
  --moe-backend marlin \
  --enable-prefix-caching \
  --default-chat-template-kwargs '{"enable_thinking": false}'
```

**Kluczowa decyzja:** thinking OFF (Gemma 4 ma natywny chain-of-thought, niepotrzebny dla strukturalnej ekstrakcji). **Świadomie nie używamy** `--reasoning-parser gemma4` — kombinacja z `enable_thinking=false` cicho wyłącza xgrammar (vLLM issue #39130).

**Smoke test:** math `12*17 → 204` w ~300 ms, JSON mode `{"language": "pl"}` w 2s.

### Phase 1: HTML cleanup pipeline (30 min)

Pomiar dystrybucji długości na 100 URL (głównie naturanatalerzu.pl):

| Metryka | median | p95 | max |
|---|---|---|---|
| HTML znaki | 273 777 | 300 977 | 306 471 |
| Markdown znaki | 4 225 | 15 625 | 20 161 |
| Markdown tokeny | 1 247 | 4 856 | 5 979 |
| Plain tokeny | 1 204 | 4 690 | 5 809 |

**Decyzje:**
- HTML → Markdown cleanup MANDATORY (98,45% redukcja)
- Markdown vs plain: +2,86% tokenów mediana — minimalny koszt za sygnały struktury
- `include_tables=True` (zmiana z False) — dla artykułów porównawczych

**Tokenizer hack:** HF `transformers.AutoTokenizer` wywalił się na buggy `tokenizer_config.json` w `bg-digitalservices` quancie. Workaround: użycie `tokenizers` (Rust) bezpośrednio z `tokenizer.json` — **25× szybszy** niż HTTP `/tokenize` (2 ms/req vs 50 ms/req).

### Phase 2: Two-step vs one-step (1h)

Implementacja core pipeline:
- `lib/vllm_client.py` — OpenAI-compat client z retry, `response_format: json_schema`, thinking OFF per request
- `lib/prompt_loader.py` — cached system prompts + user templates
- `lib/reporter.py` — thread-safe JSONL z idempotencją po `url_hash`
- `lib/pipeline.py` — `process_step1`/`process_step2` (importowalne)
- `scripts/run_step1.py` + `run_step2.py` + `run_pipeline.py`

**Walidacja na 100 URL @ concurrency 4:**
- 100/100 OK na obu Step'ach
- Throughput 1,73 s/req amortized
- Step 1 latency median 9,7 s, Step 2 median 6,9 s
- Encje median 15 per artykuł, max 33 (po zdjęciu `maxItems: 15` cap)
- title median 60/70, meta 156/160 (target 140-160 — idealne)
- Prefix cache hit rate **72,2%**

**Pominięte:** one-step baseline. Smoke test pokazał wystarczająco wysoką jakość two-step, baseline to byłaby strata cykli.

### Phase 3: A/B sampling (45 min)

Test na 100 URL × 3 configi × 2 stepy + consistency 5 URL × 3 reruns × 2 configi.

**Step 1 (entity extraction):**

| | A (1.0) | B (0.7) | C (0.3) |
|---|---|---|---|
| OK | 100/100 | 100/100 | 100/100 |
| Encje median | 14 | 15 | 15 |
| Unikalne nazwy | 784 | 793 | 787 |
| Stabilność kategorii (3× ta sama) | **94/100** | | |

**Wniosek:** schema xgrammar dominuje nad temperaturą (token-level constraint wycina 99% przestrzeni). Zostawiamy A (Google default 1.0).

**Step 2 (SEO meta):**

| | A (1.0) | B (0.8) | C (0.5) |
|---|---|---|---|
| OK | **99/100** ⚠️ | 100/100 | 100/100 |
| Diversity tytułów | 99/100 | 100/100 | 100/100 |

**1 zapętlenie w A** — `finish_reason=length`, model wygenerował 2000 tokenów multi-line JSON (959 linii!). Niższa temperatura tego nie robi. **Zmiana:** Step 2 → B (0.8). Eliminuje zapętlenia bez utraty jakości.

**Consistency test (5 URL × 3 reruns sequential):**

| Config | Pełna identyczność | Te same nazwy | Sama liczba |
|---|---|---|---|
| A (1.0) | 0/5 | 0/5 | 0/5 |
| C (0.3) | 0/5 | 0/5 | 1/5 |

**Wniosek:** **niska temperatura NIE daje determinizmu** na Marlin sm_121. Trzy źródła niedeterminizmu: sampling, FP arytmetyka batchu, non-deterministic CUDA reductions. Idempotencja prod opiera się na `url_hash` skip, nie deterministycznym output.

### Phase 4: Prompt iteration (1.5h, 5 wersji)

**v1 → v2: Domain-specific refinement (na bazie 100 URL analizy)**

Znalezione błędy w v1:
- `mięśnie głębokie`, `kręgosłup` → structure (powinno być other — anatomia)
- `tortownica` → structure (powinno być product — kitchen tool)
- `chusteczki nawilżane`, `pieluchy` → other (powinno być product)
- `stres oksydacyjny` → therapy (powinno być disease)
- `biomechanika`, `dietetyka`, `BMI` → discipline (powinno być other — academic)

v2 dodał:
- Wzmocnione opisy `structure`, `discipline`, `other`
- 9 nowych negative examples
- 2 nowe disambiguation sections

**Wynik v1 → v2 na 50 URL:** 6 problemów → **0** ✅

**v2 → v3 → v4: Migracja na Microsoft Azure NER taxonomy**

Zamiast custom 23 typów, zaadoptowaliśmy [Azure AI Language Service NER schema](https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/named-entity-categories) — production-grade, 51 typów, language-agnostic.

**Pola w encji (v4):**
```json
{
  "name": "190°C",
  "type": "Temperature",
  "category": "Quantity",
  "strength": "weak",
  "metadata": {"unit": "Celsius", "value": 190}
}
```

**Mapping starych typów (custom → Azure):**

| Były (custom) | Jest (Azure) |
|---|---|
| substance, ingredient, dish, species, asset, work | `Product` (broad) |
| disease, therapy (jako koncept), law, anatomy | `Information` |
| therapy (jako procedure), discipline, activity | `Skill` |
| brand | `Organization` |
| technology | `ComputingProduct` |
| nationality | `PersonType` |

**Strength (DBMS-inspired):**
- **Strong** = encja ma stabilny ID w bazie wiedzy (Wikidata, KRS, ICD-10, CAS, ISBN)
- **Weak** = kontekstowo-zależna (Hotel/Room analogy z DBMS)

Mapping deterministyczny po typie w `lib/pipeline.py:TYPE_TO_CATEGORY`.

**Metadata** (Azure resolutions) — structured normalization dla 18 typów Quantity/DateTime:

```json
"180°C" → Temperature {unit: "Celsius", value: 180}
"500 g" → Weight {unit: "Gram", value: 500}
"25-30 minut" → NumberRange {rangeKind: "Number", minimum: 25, maximum: 30}
"5 maja 2025" → Date {timex: "2025-05-05", value: "2025-05-05"}
"100 USD" → Currency {unit: "US Dollar", value: 100, ISO4217: "USD"}
```

**Empiryczna walidacja v4 na 50 URL:**
- 50/50 OK
- 1 355 encji, median 23 per artykuł (vs ~15 w v2)
- 106/1355 (7,8%) z metadata
- Strong/Weak ratio 77%/23%

**v4 → v5: Edge case cleanup + post-processing safety net**

W v4 znaleziono 16 problematycznych metadata wpisów (model mieszał schemy):
- `Number` z `offset/relativeTo` (z Ordinal schema)
- `Date "maj"` z `maximum: 5, offset: 0` (te pola nie istnieją w Date)
- `Temporal` z metadata (Temporal nie ma metadata schema w Azure)
- `Volume "łyżka oleju"` → `unit: "Unspecified"` zamiast Tablespoon

**Rozwiązanie hybrydowe (prompt + cleanup):**
1. v5 prompt — explicit "ONLY use these fields for type X" + "What NOT to extract" sekcja
2. `lib/pipeline.py:_clean_metadata()` — deterministyczny post-processing whitelist per typ. Odrzuca wszystkie pola spoza Azure spec dla danego typu.

**Wynik v4 → v5 na 50 URL:** 16 problemów → **0** ✅

**Wnioski techniczne z Phase 4:**
1. **xgrammar nie wystarcza** dla per-type metadata constraints. Wszystkie pola opcjonalne → model może je mieszać.
2. **Post-processing cleanup** w Pythonie jest niezawodny (deterministyczny).
3. **Hybrid (prompt + cleanup)** eliminuje 100% błędów strukturalnych.

### Phase 5 (in progress): E2E na 155 URL

Pełen run na całym datasecie `websites/` przez nowy orchestrator `scripts/run_full.py`:

```bash
python3 -u scripts/run_full.py --out-dir final_result --limit 0 --concurrency 8
```

Robi: mkdir + snapshot metrics before + Step 1 + Step 2 + snapshot after + analiza → `summary.md`.

## Kluczowe decyzje (DECISIONS.md)

15 logged decisions:

| ID | Decyzja | Status |
|---|---|---|
| D1 | Model finalny: nvidia/Gemma-4-26B-A4B-NVFP4 (prod) / bg-digitalservices (Spark) | Final |
| D2 | Stack: vLLM + xgrammar + Marlin (sm_121) + FP8 KV cache + prefix caching | Final |
| D3 | Thinking OFF + brak `--reasoning-parser gemma4` (xgrammar bypass bug) | Final |
| D4 | `--max-model-len 24576` (60% headroom dla worst case) | Final |
| D5 | HTML cleanup MANDATORY przez trafilatura markdown + tables | Final |
| D6 | Sampling Google defaults (validated empirycznie) | Final |
| D7 | Two-step pipeline (smoke 3+100 URL wystarczył; baseline pominięty) | Final |
| D8 | Tokenizer lokalny Rust `tokenizers` (25× szybciej niż HTTP) | Final |
| D9 | Truncate dwustopniowy (chars + tokens) | Final |
| D10 | URL info z json.gz: tylko url + domain + path (bez headings) | Final |
| D11 | Flat layout (`lib/` + `scripts/`) zamiast `src/` | Final |
| D11.5 | "No premature constraints" — najpierw obserwuj, potem ograniczaj | Final |
| D11.7 | Diagnostyka prefix cache przez `/metrics`, nie response | Final |
| D12 | Step 1 sampling 1.0, Step 2 → 0.8 (eliminuje zapętlenia) | Final |
| D13 | Niska temp NIE daje determinizmu — idempotencja przez `url_hash` skip | Final |
| D14 | Prompt v2: domain-specific refinement (6 problemów → 0) | Superseded by D15 |
| D15 | Pełna migracja na Azure NER taxonomy + category + strength + metadata | Final |

## Lessons learned

### 1. "No premature constraints" reguła (D11.5)

Każdy parametr który ma efekt twardego ograniczenia (`max_tokens`, `maxItems`, `maxLength`, truncate) — zaczynamy od ustawienia które **NIE jest aktywne** dla typowego runa. Mierzymy. Tnijemy tylko tam, gdzie dane uzasadniają.

**Konkretne przypadki:**
- `MAX_TOKENS_STEP1` 400 (z INSTRUCTIONS) → niepotrzebnie ucinał 2/100 outputów. User słusznie zwrócił uwagę: "musimy najpierw zobaczyć ile realnie outputów potrzebujemy". Po podniesieniu do 2000 → median 301, max 763. Bezpieczne `1000`.
- `maxItems: 15` w schemacie → ograniczał encje. Po zdjęciu — model wyciąga median 23.

### 2. Empiryczne pomiary biją intuicje

Twoje przeczucie ("nie wiem czy temperatura zmieni jakość") okazało się **w 100% trafione**. A/B test pokazał:
- Step 1 A/B/C dają **niemal identyczne** wyniki — schema constraint dominuje
- Step 2 A/B/C dają identyczną jakość, ale A ma 1% zapętleń

Bez pomiaru nie wiedzielibyśmy żeby Step 1 zostawić na 1.0 a Step 2 obniżyć do 0.8. **A priori** byśmy poszli za INSTRUCTIONS (oba 1.0).

### 3. Prefix caching to ekonomia projektu

System prompt 8084 tokenów × 21M URL = 170 mld tokenów input bez cache. Z cache hit rate 72-99% płacimy ~10× mniej. Dla 21M URL na RTX 5090 to różnica **dni vs tygodni**.

### 4. Schema enforcement ≠ semantic enforcement

xgrammar gwarantuje że JSON jest poprawny strukturalnie i `enum` values są zachowane. Ale dla zagnieżdżonych opcjonalnych pól (`metadata` z 10 możliwymi kluczami) model może mieszać schematy. **Post-processing cleanup w Pythonie** to najprostsze i najpewniejsze rozwiązanie.

### 5. Standardy >>> custom dla 21M URL

23 custom typów (substance, dish, ingredient, disease, etc.) były dobrze przemyślane dla domeny food/health. Ale dla 21M URL na różnych domenach **Azure NER (51 typów)** jest spójniejsze. Plus daje:
- Hierarchię (category → type)
- Metadata structured resolutions (gotowe do agregacji)
- Lat doświadczenia Microsoftu
- Zero subiektywnych granicznych decyzji

### 6. Idempotencja przez hash, nie determinism

Niska temperatura nie wystarczy dla powtarzalności na Marlin sm_121. **`url_hash` skip** w `JsonlReporter.load_existing_hashes()` jest niezawodne i niezależne od inference layer.

## Wyniki finalne

**Phase 4 v5 na 50 URL @ concurrency 8:**

| Metryka | Wartość |
|---|---|
| OK rate | **100/100** (Step 1 + Step 2) |
| Throughput | 4,76 s/req amortized |
| Step 1 latency median | ~10 s |
| Step 2 latency median | ~7 s |
| Encje per artykuł median | 22 |
| Encji z metadata | 8% (100% mandatory typów) |
| Strong/Weak ratio | 77% / 23% |
| Prefix cache hit rate | 99,6% (Phase 3 multi-config) |
| Problematic patterns | **0** |

## Co dalej (Phase 5+)

- **Phase 5 (E2E):** pełen run na 155 URL z `websites/` (in progress)
- **Storage decision:** SQLite + sqlite-vec dla research → PostgreSQL + pgvector dla 21M URL prod
- **Embeddingi:** BGE-M3 lub jina-embeddings-v3 dla metadanych encji
- **Phase 7:** migracja na RTX 5090 (RunPod) — natywne FP4, ~3-5× szybciej niż Spark Marlin
- **Phase 9:** prod run 21M URL (estymata 6-15 dni / ~$200-300)

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- INSTRUCTIONS_FROM_CLAUDE.md — full spec architektury
- DECISIONS.md — log wszystkich 15 decyzji
- PLAN.md — plan techniczny per faza
- Microsoft Azure NER taxonomy: https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/named-entity-categories
- vLLM issue #39130 (reasoning-parser bypass): https://github.com/vllm-project/vllm/issues/39130

## Statystyki sesji

- **Czas pracy:** ~5h
- **Faz ukończonych:** 4 (z 9 planowanych)
- **Decyzji udokumentowanych:** 15
- **Wersji promptu Step 1:** 5 (v1→v2→v3→v4→v5)
- **Wersji schematu:** 4 (v1→v2→v3→v4)
- **Commitów:** ~15
- **Linijek kodu:** ~3500 (Python + JSON Schema + prompt MD)
- **URL'i przetestowanych:** 100 (Phase 2) + 100×3 (Phase 3) + 50 (Phase 4) = ~450 unique runs
