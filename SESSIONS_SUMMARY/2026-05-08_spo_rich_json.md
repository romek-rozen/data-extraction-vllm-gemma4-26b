# Session: SPO rich-JSON v3 (replace pipe format) — 2026-05-08

## TL;DR

Switched the SPO output format from pipe-separated text (`s|p|o\n`) to rich JSON enforced
by xgrammar (`response_format: json_schema`). Eliminated the 2-7% parse-error rate the
pipe format produced (extra-pipe qualifiers, missing-pipe glue) and added 7 new fields
per triple (subject_type, relation_type, predicate_phrase, object_type, object_kind,
evidence_span, confidence) plus document-level `primary_topic` and `central_entities`
with `primary`/`secondary` gradation.

`relation_type` stays **freeform string** (snake_case English, hint in prompt) for
bootstrap; closed enum lands in v4 after harvesting from full-sample bench
(`PLANS/spo_predicate_refinement_plan.md`).

## Context — what triggered the rewrite

Earlier smokes of v2 pipe format (`prompts/spo_pipe_v2_system.md`) had:
- 3 parse errors per 10 articles in `seed=7` (extra-pipe qualifiers like
  `lody waniliowe|stored in|lodówka|at least|4 godziny` — 5 segments, 4 pipes).
- 1-4 parse errors per 10 articles across other seeds (missing-pipe glue like
  `Badanie UFL|is non-invasive` — 2 segments, predicate concatenated with object).

Two prompt-strengthening attempts (explicit WRONG/RIGHT examples, "exactly two `|` per
line" self-check) reduced but didn't eliminate the issue. Auto-correction loop was
considered and rejected (cost + uncertain success). Final call: switch to JSON via
xgrammar guided_json — structural validity is then guaranteed by construction.

User raised the architectural design (paraphrased):

> What about a richer schema with primary_topic, central_entities[primary/secondary],
> and triples carrying subject_type, relation_type, predicate_phrase, object_type,
> object_kind, evidence_span, confidence? More information per article, audit trail for
> debugging, type-aware aggregation downstream.

We implemented that design. Closed predicate enum was deferred — first we want the model
to bootstrap natural distribution under guided_json, then harvest a clean enum after
real-data benchmark.

## What changed in this session

### New schemas (v3)

- `prompts/spo_pipe_v3_schema.json` — for split-call `spo_pipe` step (run_spo_v2.py).
  Top-level: `primary_topic`, `central_entities[]`, `triples[]`. 9 required fields per
  triple including `subject_type`/`object_type` (Azure NER 51-type enum) and
  `confidence` (0-1).
- `prompts/spo_schema_v3.json` — for single-call cram (run_spo_v1.py). Same structure
  plus the `entities[]` array (so entity extraction + SPO emission happen in one LLM
  call).

### New prompts

- `prompts/spo_pipe_v3_system.md` — full instructions, canonical-direction rule
  (active voice, agent first), per-field semantics, 1 worked PL example (coffee machine).
- `prompts/spo_entities_v3_system.md` — same instructions plus the entity-extraction
  rules (Azure NER 51 types, dedup, canonicalization, is_central caps to 5).

### New lib module

- `lib/spo_pipeline_v3.py`:
  - `process_entities_spo_v3` — for spo_v1 (cram). Calls `client.chat_json` with v3
    schema, enriches entities (`enrich_entity` for category+strength), dedups, validates
    triples against entity list (informational metric only — does not drop).
  - `process_spo_pipe_v3` — for spo_v2 (split). Receives entities from upstream
    `entities_only` step, embeds them in the user prompt, calls `client.chat_json`.
  - `join_final_v3` — combines classify + entities + spo + meta + sponsored records.
    Preserves rich triple fields verbatim into final.jsonl. Works for both pipelines
    (cram passes spo_record=None and reads triples from entities_record; split passes
    both).
  - Helpers: `_normalize_rich_triple` (lowercase relation_type), `_dedup_rich_triples`
    (key = lower(s) + relation_type + lower(o)), `_validate_triples_against_entities`.
  - Re-exports `make_junk_stub_final_spo` from v1 and `process_classify_v2` from
    threestep_v2 (so orchestrators don't need multi-module imports).

### Orchestrator updates

- `scripts/run_spo_v1.py`:
  - Switched lib imports to v3.
  - Loads `spo_entities_v3_system` + `spo_schema_v3`.
  - Bumped max_tokens to 4500 (rich JSON verbosity).
  - `spo_raw.txt` now stores one JSON object per article (rich fields can't be flattened
    to text).
  - Fix in commit 8a8c7c2: pass `spo_record=None` explicitly in `join_final_v3` call so
    `meta_rec` doesn't end up in the spo_record slot.
- `scripts/run_spo_v2.py`:
  - Switched lib imports to v3.
  - Loads `spo_pipe_v3_system` + `spo_pipe_v3_schema`.
  - Bumped max_tokens to 4200.
  - `spo_raw.txt` writes JSON-per-line (replaces legacy raw-pipe-text dump).

### Old prompts/lib preserved (not deleted)

- `prompts/spo_pipe_v2_system.md` — pipe format, deprecated.
- `prompts/spo_schema_v1.json` — basic SPO triples (s/p/o), deprecated.
- `prompts/spo_entities_v1_system.md` — bootstrap-discovery prompt for v1 pipeline.
- `lib/spo_pipeline_v1.py`, `lib/spo_pipeline_v2.py` — kept as deprecated callable refs
  (still re-imported by v3 module for shared helpers like `enrich_entity`,
  `make_junk_stub_final_spo`, `_merge_meta_into`, `_merge_sponsored_into`).

## Pre-bench smoke results (n=10, seed=42, concurrency=8, cold cache)

| Pipeline | wall (s) | s/URL | triples/article | s_unmatched | parse_errors |
|---|---|---|---|---|---|
| v1 cram (`run_spo_v1.py`) | 93.7 | 9.37 | 8.75 | 4.29% | **0** |
| v2 split (`run_spo_v2.py`) | 133.4 | 13.34 | **11.88** | **2.11%** | **0** |

Observations:
- **Zero parse errors** in both — xgrammar guided_json is doing its job.
- v2 split produces 35% more triples per article (the dedicated SPO step has more
  output budget and more focused attention).
- v2 split has lower subject-mismatch rate (2.11% vs 4.29%) — entities are extracted
  first, then SPO is generated with those entities embedded in the user prompt as a
  hard constraint; cram has both happening together so the model sometimes drifts.
- v1 cram is 42% faster wall-time per article (single LLM call vs two).

Trade-off summary: v1 = throughput, v2 = quality.

## Bench plan (executes overnight)

1. **Bench v1** — 1000 articles, `--random --seed 42 --concurrency 8`, cold cache.
2. **Bench v2** — 1000 articles, same seed=42 (identical articles), cold cache.
3. Compare wall, triples_per_article, s_unmatched, predicate distribution, central_entities
   coherence.
4. **Pick winner** for full-sample run (likely v2 per user's expectation that quality
   wins on a 21M-URL knowledge graph).
5. **Full run** of winner: all 25667 articles, `--limit 0 --concurrency 8`, cold cache.

User decision criterion (from earlier discussion): full run should produce data quality
suitable for closed predicate enum derivation in v4. v2 split (more triples, better
matched) is the leading candidate.

## Files dotknięte (commits z tej sesji)

- `8140c60` — SPO v3 rich-JSON: schemas, prompts, lib module, orchestrator switches.
- `b98fcc5` — lib v1/v2 helpers (prereq for v3 module).
- `8a8c7c2` — v1 join args fix (spo_record=None explicit kwarg).
- (this commit, after bench) — bench results, predicate distribution analysis.

## Open work (next session)

- `PLANS/spo_predicate_refinement_plan.md` — harvest predicates from bench data, build
  closed enum (~25-30 predicates schema.org/ConceptNet aligned), update prompts/schemas
  to v4. Target: 100% `relation_type ∈ enum` after re-bench.
- Atrybuty udające relacje (`has_hex_code`, `has_fat_content`) — wynieść z SPO do
  `entity.metadata` (Step 1 schema już ma to dla Quantity types, rozszerzyć).
- Inverse-relation pairs — pick canonical direction per pair, document in
  `prompts/spo_predicates.json`.
- Cross-article entity resolution (fuzzy matching) — separate big session.
- JSON-LD export from rich triples — content brief integration.

## Internet question (asked earlier in session)

User asked whether internet can be disconnected during the full run. **Yes** — entire
pipeline is local:
- vLLM in Docker (`vllm-gemma4`) bound to `localhost:8001`.
- Tokenizer loaded from local disk (`/home/spark001/models/gemma4-26b-nvfp4-bg/`).
- trafilatura local Python lib.
- Article HTMLs from `websites/<hash>/` on disk.
- No external API calls at runtime.
- The streaming loader writes to `websites_cache/` on disk.

Only `localhost:8001` and disk access need to work.
