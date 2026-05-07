# Architektura two-step pipeline

## Diagram przepływu danych

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INPUT (websites/)                             │
│  websites/<sha-hash>/                                                │
│    ├── html.gz       (raw HTML)                                      │
│    └── json.gz       (metadata: url, url_finish, http_code, headers) │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    HTML CLEANUP (lib/data_loader.py)                 │
│  trafilatura.extract(                                                │
│    output_format="markdown",                                         │
│    include_links=True, include_formatting=True,                      │
│    include_comments=False, include_tables=True                       │
│  )                                                                   │
│  → 273k znaków HTML → 4.2k znaków markdown (98.45% redukcja)         │
│                                                                      │
│  + url_info from json.gz: {url, domain, path}                        │
│  + url_hash = sha256(url) for idempotency                            │
│  + truncate to MAX_ARTICLE_TOKENS=20000 (safety net)                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│           STEP 1: ENTITY EXTRACTION (lib/pipeline.py)                │
│                                                                      │
│  vLLM (Gemma 4 26B A4B NVFP4 + Marlin) +                             │
│  response_format: json_schema (xgrammar) +                           │
│  Sampling: temp 1.0, top_p 0.95, top_k 64 (Google default)           │
│  Schema: 51 Azure NER types + optional metadata                      │
│                                                                      │
│  Per article:                                                        │
│    - language (ISO 639-1)                                            │
│    - category (1 of 41)                                              │
│    - entities[]: {name, type, [metadata]}                            │
│                                                                      │
│  Post-processing (lib/pipeline.py:enrich_entity):                    │
│    - dedup_entities (per-article, by name+type case-insens)          │
│    - enrich: add category (Azure high-level) + strength (strong/weak)│
│    - clean metadata: per-type field whitelist                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│       ENTITY LAYER (result/entity_layer.jsonl) — pipe note           │
│  {                                                                   │
│    url_hash, id, url, domain, path,                                  │
│    category, language,                                               │
│    entities: [                                                       │
│      {name, type, category, strength, metadata?}                     │
│    ],                                                                │
│    text_tokens, latency_s, usage                                     │
│  }                                                                   │
│                                                                      │
│  REUSABLE: long-term value for KG, search, multilingual              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│        STEP 2: SEO META GENERATION (lib/pipeline.py)                 │
│                                                                      │
│  vLLM + response_format json_schema +                                │
│  Sampling: temp 0.8, top_p 0.9, top_k 50 (D12)                       │
│  Context from entity layer: language, category, top entities         │
│                                                                      │
│  Per article (in detected language):                                 │
│    - title (max 70 chars)                                            │
│    - meta_description (target 140-160 chars)                         │
│    - h1 (max 100 chars)                                              │
│    - article_summary (max 400 chars)                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              FINAL OUTPUT (result/final.jsonl)                       │
│  {                                                                   │
│    url_hash, id, url, domain,                                        │
│    category, language, entities,                                     │
│    title, meta_description, h1, article_summary,                     │
│    latency_s, usage                                                  │
│  }                                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Komponenty kodu

### lib/

| Plik | Odpowiedzialność |
|---|---|
| `data_loader.py` | Skanuje `websites/`, ekstrahuje markdown przez trafilatura, dodaje url/domain/path z json.gz, truncate dwustopniowy (chars + tokens) |
| `tokenizer.py` | Singleton `tokenizers.Tokenizer.from_file(tokenizer.json)` — Rust, ~2 ms/req |
| `vllm_client.py` | OpenAI-compat klient: `/v1/chat/completions` z `response_format: json_schema`, retry na timeout, thinking OFF per request |
| `prompt_loader.py` | LRU-cache loader system promptów + JSON schemas + user template builders |
| `pipeline.py` | `process_step1`, `process_step2`, `dedup_entities`, `enrich_entity` (deterministic type → category, strength + metadata cleanup) |
| `reporter.py` | Thread-safe JSONL append + idempotent skip po `url_hash` |
| `config.py` | Stałe: ścieżki, sampling, max_tokens, vLLM URL |

### scripts/

| Plik | Cel |
|---|---|
| `start_vllm.sh` | Docker run vLLM + Gemma 4 + flagi |
| `smoke_test.sh` | Healthcheck + math + JSON mode |
| `measure_lengths.py` | Phase 1: dystrybucja długości HTML/markdown/plain (znaki + tokeny) |
| `measure_prompt_tokens.py` | Pomiar system promptów + total budżetu input |
| `run_step1.py` | Step 1 batch z entity_layer.jsonl |
| `run_step2.py` | Step 2 batch z final.jsonl |
| `run_pipeline.py` | Step 1 → Step 2 sequential |
| `run_full.py` | E2E orchestrator (mkdir + snapshot + pipeline + analiza) |
| `ab_sampling.py` | Phase 3: A/B/C samplingu × 100 URL × consistency |
| `analyze_phase2.py` | Statystyki + sample do eyeballa |
| `analyze_phase3.py` | A/B porównanie configów |
| `analyze_phase4.py` | Diagnoza problemów typowania |
| `analyze_entity_quality.py` | Top N nazw per typ (Phase 4 source of truth) |
| `compare_prompt_versions.py` | v1 vs v2 vs vN — rule-based problem detector |
| `snapshot_metrics.py` | vLLM /metrics before/after diff (cache hit rate per run) |

### prompts/

| Plik | Status |
|---|---|
| `step1_system.md` | **Aktywny** (= v5: Azure 51 + metadata + cleanup-aware) |
| `step1_system_v{1,2,3,3_no_meta,4,4_backup,5}.md` | Backupy historyczne |
| `step2_system.md` | Aktywny |
| `schema_step1.json` | **Aktywny** (51 types + optional metadata schema) |
| `schema_step1_v{2,3,4}.json` | Backupy |
| `schema_step2.json` | Aktywny |

## Schema entity (Azure NER + extensions)

```json
{
  "name": "190°C",
  "type": "Temperature",          // 1 of 51 Azure types
  "category": "Quantity",          // high-level group (deterministic from type)
  "strength": "weak",              // strong/weak (DBMS-style)
  "metadata": {                    // optional — only for 18 Quantity/DateTime types
    "unit": "Celsius",
    "value": 190
  }
}
```

### 11 Azure categories

```
Person, PersonType, Organization, Location, Address, Event,
Product, Quantity, DateTime, Skill, Information,
Email, PhoneNumber, URL, IpAddress
```

### Strength mapping (heuristic)

- **Strong** (linkable do Wikidata/KB):
  Person, Organization*, Location*, City, Continent, CountryRegion, State, GPE, Geographical, Airport, Structural, Event*, CulturalEvent, NaturalEvent, SportsEvent, Product, ComputingProduct
- **Weak** (kontekstowo-zależne):
  PersonType, Address, all Quantity sub-types, all DateTime sub-types, Email, PhoneNumber, URL, IpAddress, Skill, Information

### Metadata schemas (per-type whitelist w `lib/pipeline.py`)

| Type | Allowed metadata keys |
|---|---|
| Currency | `unit, value, ISO4217` |
| Age, Area, Length, Height, Volume, Weight, Speed, Temperature, Percentage, Duration | `unit, value` |
| Number | `numberKind, value` |
| NumberRange | `rangeKind, minimum, maximum` |
| Ordinal | `offset, relativeTo, value` |
| Date, DateTime, Time, TimeRange, DateTimeRange, SetTemporal | `timex, value` |
| DateRange | `timex, value, rangeKind, minimum, maximum` |
| Information (data size only) | `unit, value` |

Wszystkie inne typy: metadata jest stripped w `enrich_entity()`.

## Flow uruchomieniowy

```bash
# 1. vLLM kontener
bash scripts/start_vllm.sh

# 2. Healthcheck
bash scripts/smoke_test.sh

# 3. Pomiar długości artykułów (raz, do PLAN.md)
python3 scripts/measure_lengths.py --limit 100

# 4. E2E orchestrator (one-shot)
python3 -u scripts/run_full.py --out-dir final_result --limit 0 --concurrency 8

# Wyniki:
#   final_result/entity_layer.jsonl     (Step 1)
#   final_result/final.jsonl             (Step 2)
#   final_result/summary.md              (raport)
#   final_result/metrics_delta.txt       (cache stats)
```

## Idempotencja

`JsonlReporter.load_existing_hashes()` czyta plik output i zwraca set `url_hash`. Pipeline pomija URL już przetworzone. Rerun bez utraty postępu.

`--no-skip` flag wymusza rerun (output będzie semantycznie ekwiwalentny ale nie bit-identyczny — patrz D13).

## Performance (Spark sm_121 + Marlin)

| Faza | Throughput | Latency median |
|---|---|---|
| Phase 2 (custom 23 typów) | 1,73 s/req @ c=4 | Step 1 ~9,7s |
| Phase 4 v3 (51 Azure typów) | 3,09 s/req @ c=8 | ~10s |
| Phase 4 v4 (+ metadata) | 3,44 s/req @ c=8 | ~10s |
| Phase 4 v5 (+ cleanup prompt) | 4,76 s/req @ c=8 | ~10s |

Migracja na RTX 5090 (natywne FP4) → szacowane **3-5× szybciej**, zgodnie z INSTRUCTIONS Phase 8.

## Storage (planowane)

Patrz `docs/storage_21m_urls.md` dla szczegółowej analizy SQLite vs PostgreSQL vs Hybrid (Parquet + DuckDB + Qdrant).
