# SPO v1 TODO

## Phase 1 — implementacja (DONE)

- [x] Stwórz `prompts/spo_entities_v1_system.md` (canonical + central + free-form SPO)
- [x] Stwórz `prompts/spo_schema_v1.json` (entities z is_central + triples array)
- [x] Stwórz `lib/spo_pipeline_v1.py` (process_entities_spo + join_final_spo + make_junk_stub)
- [x] Stwórz `scripts/run_spo_v1.py` (two-step orchestrator)
- [x] Stwórz `scripts/spo_summary_v1.py` (auto-summary po runie)
- [x] Stwórz `dashboard/views/spo.py` + edit `dashboard/main.py` (routing)
- [x] Skopiuj `websites_praktycznyekspert/*` → `websites/` (cp -rn)

## Phase 2 — weryfikacja (DONE)

- [x] Smoke test: `python3 scripts/run_spo_v1.py --limit 5 --concurrency 4 --tag spo_smoke`
- [x] Inspect `final.jsonl` (entities mają is_central, triples sformowane, s∈entities)
- [x] `SUMMARY.md` wygenerowany poprawnie
- [x] Dashboard karta 🕸️ SPO działa

## Phase 2.5 — refinements (DONE)

- [x] Hard rule "predicate MUST be ENGLISH" dodany do promptu (po smoke v1 mieszało PL/EN)
- [x] Streaming loader z disk cache `websites_cache/<hash>.json` — 5.6× speedup
- [x] v2 alternatywna architektura (three-step pipe) zaimplementowana — smoke ~31% szybsza wall, +53% triples
- [x] v3 classifier prompt + pre-filter URL regex (`/tag/`, `/author/`, `/archive/`, `?paged=`, etc.)
- [x] Output split: `entities.jsonl` + `spo.jsonl` osobno (v1 i v2)
- [x] Cache JSON `{domain, url, content}` self-describing
- [x] `spo_raw.txt` — surowy pipe output (autentyczny v2 / reconstructed v1) bez headerów

## Phase 3 — pełen A/B run (in progress)

- [x] Stop wcześniejszych aborted runów
- [x] Restart conc=4 each (oba pipeline'y v1+v2 równolegle, total 8 = max vLLM)
- [x] Streaming loader pierwsze artykuły do GPU w <5s (vs 5-15min idle)
- [ ] Czeka na zakończenie (~5-8h dla 25667 URL)
- [ ] Po runie: porównanie A/B v1 vs v2 (wall, throughput, parse_errors, predicate distribution overlap)
- [ ] Po runie: `cat final_results/2026-05-08_19-47-43__spo_v1_AB_v3/SUMMARY.md` + `..._v2_..../SUMMARY.md`

## Phase 4 — analiza i decyzje (po zakończeniu A/B)

- [ ] Eyeball 30 random artykułów z dashboardu (sample browser) — v1 i v2
- [ ] Predicate distribution: top-50 cover ≥60% triples?
- [ ] Triple grounding: %s ∈ entities ≥90%?
- [ ] % predicates EN (sprawdź czy hard rule trzyma się poza smoke)
- [ ] Canonicalization: 50 random nazw — ile w canonical?
- [ ] is_central precision: 30 artykułów — central są naprawdę o tym?
- [ ] Predicate clustering hint: które predykaty się duplikują (`founded by` / `founded in`)?
- [ ] v1 vs v2 compare: wall, throughput, parse_errors (v2), s_unmatched, jaccard predicate overlap
- [ ] DECISIONS.md D20 — wybór: v1 / v2 / hybrid jako default, plus closed vocab v2 (z bottom-up data)

## Phase 5 — follow-up (jeśli wyniki dobre)

- [ ] Closed vocab v2 design — bottom-up z top-N predicates
- [ ] Integracja entities_spo do four-step (lub spojrzenie na osobny five-step)
- [ ] Knowledge graph viewer w dashboardzie (pyvis/visnetwork)
- [ ] Post-hoc EDC canonicalizer (cross-article)

## Pułapki / uwagi

- vLLM `--max-num-seqs 8` — conc=8 to maksimum (weryfikacja w `scripts/start_vllm.sh`)
- Free-form predicates rosną output: bufor `MAX_TOKENS_STEP1=4000` może pęknąć dla artykułów 50+ encji + 40 triples — monitoruj `finish_reason="length"`
- Resume: `--resume final_results/<ts>__spo_v1_<tag>` używa `entities_spo.jsonl` jako idempotency cache (load_existing_hashes)
- Auto-summary po runie wywołuje `scripts/spo_summary_v1.py --out-dir <out_dir>` — błąd nie crashuje runa (try/except w run_spo_v1.py)
