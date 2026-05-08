# PLAN: SPO v3 — full parallel A/B (v1 cram + v2 split, 25667 art each)

**Sesja:** 2026-05-09 (rano). **Status:** w trakcie. **Następnik:** `PLANS/spo_predicate_refinement_plan.md`.

## Co zmieniamy względem v3 bench plan

Zmienione decyzje od czasu ostatniego planu (`PLANS/spo_rich_json_v3_plan.md`):

1. **Bench 1000 art ANULOWANY.** Zamiast 1000 + 1000 → 1000 → full, robimy **full × 2 parallel**.
   Powód: 1000 art bench v1 (po 35% / 354 final.jsonl) potwierdził że pipeline działa
   (parse_errors=0 w obu, triples sensowne) i na realnej próbce różnice się dowiedzą lepiej.
2. **`maxItems` usunięte ze schem v3** (entities, central_entities, triples).
   Powód: arbitralne capy 60/5/40 były ostrożnym estymatem. xgrammar i tak ma context
   window jako naturalny limit, model sam zdecyduje optymalną ilość. Strona wartości
   dla bootstrap predykatów: pozwala modelowi wygenerować long-tail predicates jeśli
   uzna za konieczne (więcej danych do harvestu w v4).
3. **Parallel v1 + v2** (każdy `--concurrency 4`, razem 8 inflight na vLLM = identyczna
   suma jak sekwencyjne conc=8). Plus: oba kończą się w tym samym wall window → tight
   apples-to-apples comparison na identycznym setupie (RAM, GPU, cache state).
4. **Cache pre-generation oddzielnie zmierzony.** trafilatura HTML→markdown to ~50ms/URL
   na 1 wątku × 25667 URL = ~21 min single-thread, ale parallel 8-thread ~3 min.
   Mierzymy osobno bo to **nie jest** koszt LLM inference — dla porównania z RTX 6000 Pro
   ten koszt zostaje (CPU-bound, nie GPU-bound).
5. **Decyzja `subject_type` / `object_type` w schemacie:** ZOSTAJE w v3 (po ostatnim
   refleksji user). Mimo że empirycznie subject_type=entities[name].type w 100% (sprawdzone
   na 354 art bench v1 cząstkowym, 0/2763 niezgodności) — zostają w schemie żeby w
   wynikach **mieć** typy w trypletach (bez konieczności post-hoc lookup). Trade-off
   token cost vs convenience: convenience wygrywa przy 25667 art (jednorazowo).
   Alternatywę można zaimplementować w v4 (post-hoc lookup w join_final_v3).

## Cele runa

1. **Junk %** — empirycznie z `step_junkclassify_v3` na 25667 PL/multi-language artykułów.
2. **Sponsored %** — empirycznie z `step_sponsored_v2` (paid_placement zlewa
   full_sponsored + link_insertion).
3. **Predykaty** — harvest `relation_type` + `predicate_phrase` distribution z obu
   pipeline'ów, ~25k × ~10-20 triples = 250-500k triples total. Daje solidną bazę
   do wyboru closed enum w v4 (`PLANS/spo_predicate_refinement_plan.md`).
4. **Czasy run-end-to-end** — z osobnym wyróżnieniem cache gen (CPU, trafilatura)
   i LLM inference (GPU, vLLM Gemma 4 26B). Dla ekstrapolacji ETA na RTX 6000 Pro.

## Setup

### Hardware
- **GPU:** DGX Spark, GB10 sm_121, vLLM marlin backend, NVFP4 weights, FP8 KV cache.
- **CPU:** dla cache gen (trafilatura), parallel ThreadPoolExecutor.
- **vLLM concurrency budget:** sum(v1, v2) = 8 inflight requests = sweet spot Sparka
  (>8 dławi się na MoE marlin).

### Pipeline
- **v1 cram** (`run_spo_v1.py`): single LLM call entities + rich SPO triples.
  Schema `spo_schema_v3` (entities + central_entities + triples), prompt
  `spo_entities_v3_system`. Plus parallel meta + sponsored.
- **v2 split** (`run_spo_v2.py`): entities_only first, then spo_pipe with rich SPO.
  Schemy `spo_entities_only_v2_schema` + `spo_pipe_v3_schema`. Plus parallel meta + sponsored.

### Master orchestrator
`scripts/run_spo_v1_v2_test.py`:
1. **Stage 1 — clear cache:** `find websites_cache -name "*.json" -delete`.
2. **Stage 2 — pre-warm cache** (parallel ThreadPool, default 8 workers, all 25667 art).
   Pulls each article through `stream_articles_async` (forces trafilatura HTML→markdown
   extraction + write to cache). Times the whole pass + per-batch progress.
   Output: `final_results/<ts>__spo_v1_v2_test/cache_warmup_meta.json` z polami
   `n_articles, elapsed_s, throughput_per_s, n_loader_workers`.
3. **Stage 3 — launch parallel runs:**
   - `run_spo_v1.py --limit 0 --concurrency 4 --tag v3_full_par_v1` w subprocess.
   - `run_spo_v2.py --limit 0 --concurrency 4 --tag v3_full_par_v2` w subprocess.
   Oba wczytują z TEGO SAMEGO cache (websites_cache/), ale każdy pisze własny katalog
   `final_results/<ts>__spo_v{1,2}_v3_full_par_v{1,2}/`.
4. **Stage 4 — wait + report:** wait for both, generate comparison report
   (uses `scripts/spo_compare_benches.py` adapted).

### Tmux
Jedna sesja `spo_test` która opakowuje cały orchestrator. Nie potrzebujemy oddzielnych
sesji per pipeline — orchestrator zarządza subprocess'ami sam.

## Ryzyka

- **vLLM saturation:** 8 inflight requests, mix v1 (1 long-output JSON call) + v2
  (2 calls — entities_only short + spo_pipe long). Może wystąpić unequal queueing.
  Mitigation: prefix caching ON (system prompty cached); KV cache budget OK na 24576
  max-model-len × 8 = 200k tokens vs 30GB VRAM Spark.
- **`maxItems=∞` może spowodować runaway:** model bez capa może wygenerować 100+ triples
  zamiast ~20-40. Mitigation: `max_tokens` (4500/4200) działa jako hard cap; jeśli
  osiągnięty, model przestanie. Plus prompt mówi "be concise, prefer high-quality central
  facts".
- **Cache concurrency:** dwa subprocess'y (v1 + v2) jednocześnie czytają z `websites_cache/`
  — read-only po warmupie, no race. Plus każdy pisze do swojego katalogu final_results.
- **Wall-time scaling:** parallel v1+v2 conc=4 might be slower per-pipeline niż gdyby
  każdy sam działał z conc=4 (bo dzielą GPU). Akceptujemy w zamian za apples-to-apples
  porównanie.

## Acceptance

- [ ] Cache pre-warm gen mierzy całość: full 25667 art od `find -delete` do ostatniego
      `<hash>.json` napisanego.
- [ ] Oba runy v1/v2 zakończone OK (`run_meta.json` ma `wall_s`).
- [ ] `final.jsonl` w obu zawiera 25667 rekordów (junk = stub, non-junk = pełen rich record).
- [ ] Compare report w `SESSIONS_SUMMARY/2026-05-09_spo_v1_v2_full_par.md`.
- [ ] DECISIONS D26 (maxItems removed) + D27 (parallel run + cache gen separate).
- [ ] CHANGELOG entry.
- [ ] Commits + push przed launch i po zakończeniu.

## Po zakończeniu (next session)

`PLANS/spo_predicate_refinement_plan.md` — analiza top-100 `relation_type` z obu pipeline'ów,
mapping synonimów, wybór closed enum (~25-30 schema.org-aligned), update do v4.
