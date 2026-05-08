# PLAN: SPO rich-JSON v3 (replace pipe format)

**Sesja:** 2026-05-08 (późny wieczór). **Status:** w trakcie.

## Motywacja

Format `s|p|o` per linia (v2 spo_pipe) miał 2-7% parse errors w smokes:

1. **Extra-pipe** (>3 segmenty): model wstawia `|` jako separator dla qualifierów.
   Przykład: `lody waniliowe|stored in|lodówka|at least|4 godziny` (5 segm.)
2. **Missing-pipe** (<3 segmenty): model klei predykat z obiektem.
   Przykład: `Badanie UFL|is non-invasive` (2 segm.)

Próbowaliśmy wzmocnić prompt dwa razy (sekcja `## HARD RULES`, explicite WRONG vs RIGHT examples,
self-check instruction "exactly two `|` per line") — efekt 3-7 errors per 10-15 art smoke.

**Decyzja:** rezygnujemy z plain text pipe, przechodzimy na JSON wymuszany przez xgrammar
(`response_format: json_schema`). Strukturalna poprawność = 100%. Koszt: ~2× tokenów output,
ale Gemma 4 dobrze podąża za JSON i mamy `enable_prefix_caching` na system promptach.

## Format docelowy (rich JSON)

```json
{
  "primary_topic": "<noun phrase, 1-6 words, article language>",
  "central_entities": [
    {"entity_name": "...", "centrality": "primary"},
    {"entity_name": "...", "centrality": "secondary"}
  ],
  "triples": [
    {
      "subject":          "<canonical entity name from list>",
      "subject_type":     "<Azure NER type, 1 of 51>",
      "relation_type":    "<freeform snake_case English, ≤50 chars>",
      "predicate_phrase": "<natural language phrase from text, article language>",
      "object":           "<entity name OR literal>",
      "object_type":      "<Azure NER type>",
      "object_kind":      "entity | literal",
      "evidence_span":    "<verbatim fragment ≤500 chars>",
      "confidence":       0.0-1.0
    }
  ]
}
```

### Field rationale

- **`primary_topic`** — syntetyczny hyperonim (może być spoza entities list, np. "lampy"
  dla list page nie mającego encji "lampy"). Kotwica dla downstream content briefów.
- **`central_entities[primary/secondary]`** — gradacja ważności (primary=top 1-2, secondary=3-5).
  Mocniejszy sygnał niż boolean `is_central`.
- **`relation_type`** — **freeform STRING bez enum w v3** (D23 będzie później po benchach).
  Model emit snake_case English (hint w prompcie). Cel: zebrać empiryczną dystrybucję.
- **`predicate_phrase`** — naturalna fraza z tekstu (article language). Backup gdy
  `relation_type` mało precyzyjny + sygnał lingwistyczny dla cross-language alignmentu.
- **`object_kind: entity | literal`** — entity = węzeł grafu (subject/object w entities list),
  literal = wartość (Number/Currency/Temperature) dla `has_property`-style faktów.
- **`evidence_span`** — verbatim fragment dla audytu, debug, offline benchmarków, cytowań.
- **`confidence`** — 0-1 raportowane przez model. Filtrowanie downstream:
  >0.7 strict graph, >0.5 recall-heavy.

## Implementacja

### v2 (split: entities_only + spo_pipe SEPARATE — pozostaje split)
- `prompts/spo_pipe_v2_schema.json` — rich JSON schema (overwritten z bare-array bootstrap).
- `prompts/spo_pipe_v2_system.md` — przepisany pod JSON, full HARD RULES + 1 example
  (PL coffee machine).
- `lib/spo_pipeline_v2.py:process_spo_pipe_v2` — przepisany na `client.chat_json` zamiast
  raw POST. Parser triple'i = pętla po `parsed["triples"]` (zero parse errors możliwych).
- `entities_only` schema bez zmian — zostaje minimalny (`{name, type, is_central}`).

### v1 (cram: single-call entities + SPO razem — testujemy czy szybsze)
- `prompts/spo_schema_v1.json` — extended z entities + central_entities + primary_topic + rich triples.
- `prompts/spo_entities_v1_system.md` — przepisany: ekstrakcja encji + emit rich SPO triples
  w jednym call (więcej tokenów input/output, ale mniej round-tripów).
- `lib/spo_pipeline_v1.py:process_entities_spo` — parsuje rich triples z output.

### Wspólne dla v1+v2
- `join_final_spo` / `join_final_spo_v2` — preserve rich triple fields w final.jsonl.
- `JsonlReporter` — bez zmian (writes whatever rec dict comes in).
- `make_junk_stub_final_spo` — bez zmian (junk = empty entities/triples/topic).

## Benchmarking plan

**Cele:**
1. Empirycznie zebrać `relation_type` distribution → fundament pod D23 (predicate enum).
2. Wall-time v1 (cram) vs v2 (split) na identycznym setupie.
3. Realny czas dla kalkulacji ETA na RTX 6000 Pro (target hardware).
4. Cost trafilatura+cleanup — clear cache before each run, mierz cold load.

**Setup:**
- vLLM Gemma 4 26B A4B NVFP4 na DGX Spark, sm_121, marlin backend.
- `--concurrency 8` (Spark dławi się przy 8+, sweet spot per testy 2026-05-08).
- Sequential, jeden run na raz (nie konkurencja o GPU).
- Cache czyszczone przed każdym (pierwsze trafilatura miss).

**Etapy:**
1. **Smoke 10 art** (`--limit 10 --random --seed 42`) — sanity, oba pipeline'y po kolei.
2. **Bench v1 1000 art** (`--limit 1000 --random --seed 42 --concurrency 8`) — cache cold.
3. **Bench v2 1000 art** (ten sam seed=42 → identyczne URL-e dla porównania jakości i czasów).
4. (Opcjonalnie) **Bench powtórka** seed=42 z cold cache — wariancja runu.

**Metryki do raportu (post-bench):**
- `wall_s` per run.
- `s/URL` — sumarycznie i per phase (timing.csv).
- `triples_per_url` (avg, p50, p95) — rich triples count.
- `relation_type` top-50 distribution.
- `confidence` histogram.
- `parse_errors` — powinien być 0 (xgrammar). Sanity check.
- `entities_count` per artykuł.
- Cache cold load time (loader stats — pierwsza godzina).

## Po benchach (next session)

`PLANS/spo_predicate_refinement_plan.md` — analiza dystrybucji predykatów, mapping synonimów,
wybór finalnego enum (~25-30 predykatów schema.org-aligned), update prompt+schema dla v4.

## Pliki dotknięte w tej sesji

- `prompts/spo_pipe_v2_schema.json` (rewrite)
- `prompts/spo_pipe_v2_system.md` (rewrite)
- `prompts/spo_schema_v1.json` (extend)
- `prompts/spo_entities_v1_system.md` (rewrite)
- `lib/spo_pipeline_v1.py` (process_entities_spo, parse logic)
- `lib/spo_pipeline_v2.py` (process_spo_pipe_v2 → chat_json)
- `lib/spo_pipeline_v1.py:join_final_spo` (preserve rich fields)
- `lib/spo_pipeline_v2.py:join_final_spo_v2` (preserve rich fields)
- `scripts/run_spo_v1.py` (no large change, may need reporter tweaks)
- `scripts/run_spo_v2.py` (no large change)
- `CHANGELOG.md`, `DECISIONS.md` (D23 rich JSON, D24 v1 cram vs v2 split, D25 freeform predicates bootstrap)
- `SESSIONS_SUMMARY/2026-05-08_spo_rich_json.md`
- `PLANS/spo_rich_json_v3_plan.md` (this file)
- `PLANS/spo_predicate_refinement_plan.md` (placeholder for next session)
