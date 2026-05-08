# Output Schema — pliki w `final_results/<ts>__<pipeline>_<tag>/`

Każdy run SPO produkuje uniformowy zestaw plików niezależnie od wariantu pipeline'u (v1 single-call JSON vs v2 three-step pipe). To pozwala downstream'owi (dashboard, analizy, A/B comparison) traktować runy identycznie.

## Pliki

### `classified.jsonl`
Klasyfikacja junk vs non-junk. Jeden record per URL.
```json
{
  "url_hash": "sha256-of-url",
  "id": "subdir-name",
  "url": "https://...",
  "domain": "example.com",
  "path": "/some/path",
  "text_tokens": 1234,
  "ok": true,
  "is_junk": false,
  "raw": "0",
  "latency_s": 0.234,
  "usage": {"prompt_tokens": ..., "completion_tokens": ...},
  "finish_reason": "stop",
  "attempts": 1,
  "ts": "2026-05-08T19:47:48",
  "ml_skipped": false,
  "junk_reason": null
}
```
Pola dodatkowe gdy pre-filter URL match (deterministyczne junk):
- `ml_skipped: true` — nie wywołano LLM
- `junk_reason: "tag_listing" | "author_archive" | "date_archive" | "search_results" | "label_listing" | "wp_search_query" | "paginated_query"`
- `latency_s: 0.0`, `finish_reason: "url_pre_filter"`, `attempts: 0`

### `entities.jsonl`
Encje canonical z flagą centralności. Jeden record per non-junk URL.
```json
{
  "url_hash": "...",
  "id": "...",
  "ok": true,
  "error": null,
  "latency_s": 12.3,
  "ts": "...",
  "entities": [
    {"name": "OpenAI", "type": "Organization", "category": "Organization", "strength": "strong", "is_central": true},
    {"name": "Sam Altman", "type": "Person", "category": "Person", "strength": "strong", "is_central": false}
  ],
  "n_central": 2,
  "entities_raw_count": 24
}
```

### `spo.jsonl`
Triplety SPO. Jeden record per non-junk URL.
```json
{
  "url_hash": "...",
  "id": "...",
  "ok": true,
  "error": null,
  "latency_s": 8.5,
  "ts": "...",
  "triples": [
    {"s": "OpenAI", "p": "founded by", "o": "Sam Altman"},
    {"s": "OpenAI", "p": "headquartered in", "o": "San Francisco"}
  ],
  "triples_raw_count": 8,
  "triples_s_unmatched": 0,
  "triples_o_unmatched": 1
}
```
v2 dodaje: `parse_errors` (linie pipe nie-3-segmentowe), `n_lines_total`, `sample_bad_lines`.

### `final.jsonl`
Joined record per URL — entities + triples + metadane. Punkt wejścia dla downstream.
```json
{
  "url_hash": "...",
  "url": "https://...",
  "domain": "...",
  "is_junk": false,
  "ok": true,
  "ts": "...",
  "entities": [...],
  "triples": [...],
  "n_central": 2,
  "entities_raw_count": 24,
  "triples_raw_count": 8,
  "triples_s_unmatched": 0,
  "triples_o_unmatched": 1
}
```
Junk: pusty `entities`/`triples`, `is_junk: true`, `skipped_reason: "junk_classified"`.

### `spo_raw.txt`
Surowy pipeline output — same triplety, jeden per linia, BEZ headerów, separatorów ani metadanych. Format `subject|predicate|object`.

```
OpenAI|founded by|Sam Altman
OpenAI|headquartered in|San Francisco
trufla|grows in|Polska
trufla|grows in|Puszcza Białowieska
...
```

- **v2**: autentyczny output LLM (przed parsowaniem) — pozwala zobaczyć jak model literalnie generuje pipe text + edge cases (extra `|`, malformed lines).
- **v1**: reconstructed z parsed JSON triples — ten sam format dla wizualnego porównania z v2.

### `entities_spo.jsonl` (v1 only, legacy)
Combined record (entities + triples) z v1 — używany dla resume idempotency. Ten sam content co osobne `entities.jsonl` + `spo.jsonl`.

### `run_meta.json`
Metadane runa (jeden plik, override przy resume).
```json
{
  "pipeline": "spo_v1" | "spo_v2",
  "limit": 0,
  "concurrency": 4,
  "pattern": "two_step_classify_then_entities_spo",
  "random_sample": false,
  "seed": 42,
  "skip_junk": true,
  "websites": "/path/to/websites",
  "wall_s": 12345.6,
  "started_at": "...",
  "ended_at": "...",
  "counters": {...},
  "n_articles_seen": 25667,
  "n_skipped_already_done": 0,
  "n_pre_filter_junk": 1843,
  "classifier_prompt": "step_junkclassify_v3_system",
  "use_streaming": true,
  "loader_workers": 2,
  "cache_dir": null,
  "loader_stats": {"cache_hits": 25600, "cache_misses": 67, "parse_errors": 0, "yielded": 25667, "elapsed_s": 5.2}
}
```

### `SUMMARY.md`
Auto-generated po runie przez `scripts/spo_summary_v1.py`. Zawiera:
- Counters (junk%, non-junk OK, fails)
- Top-100 predicates z procentowym pokryciem
- Predicate word-length distribution
- Top-50 central entities (cross-article)
- Entity type × is_central tabela
- Top-30 domains by junk rate (min 5 articles)
- 30 sample triples (random, seed=42) do eyeball
- Decision feed dla closed vocab v2 (cumulative coverage top-50/top-100)

### Logi
- `classify.log` — per-event classify outcomes
- `entities_spo.log` (v1) / `entities_only.log` + `spo_pipe.log` (v2)
- `run.log` — high-level orchestrator events
- `full_stdout.log` — pełen tee z stdout (jeśli uruchomione przez `tee`)

### `timing.csv`
Per-call latency dla analizy ogonów (p50, p95, p99 per phase).
```csv
phase,url_hash,latency_s,ok,attempts
classify,abc123,0.234,true,1
entities_spo,abc123,12.3,true,1
```

### `sample_seed.txt`
Jeśli `--random` użyte — zapisany seed. Resume go odczytuje automatycznie.

## Cache `websites_cache/<hash>.json`

Markdown extraction cache. Jeden plik per URL.
```json
{
  "domain": "example.com",
  "url": "https://example.com/path",
  "content": "<markdown>"
}
```
Versioning w `_version.txt` (obecna v4). Mismatch → wipe `*.md`/`*.json` i regeneracja.

## Idempotency / resume

- Każdy reporter (`classified`, `entities`, `spo`, `final`) używa `JsonlReporter.load_existing_hashes(only_ok=True)` — pomija URL z już-OK rekordami przy resume.
- `--resume final_results/<ts>__<pipeline>_<tag>` wznawia konkretny run.
- Failures (ok=False) są ponawiane — `only_ok=True`.
- Pre-filter junks zapisane są jako `ok=True, is_junk=true, ml_skipped=true` — nie są ponownie LLM-klasyfikowane przy resume.

## Co downstream może traktować jako stable API

- Klucze `url_hash`, `is_junk`, `entities[].name`, `entities[].type`, `entities[].is_central`, `triples[].s/p/o` — stable cross-pipeline (v1 == v2).
- Pola dodatkowe (np. `parse_errors`, `triples_o_unmatched`) — pipeline-specific, sprawdzaj `pipeline` w `run_meta.json`.
