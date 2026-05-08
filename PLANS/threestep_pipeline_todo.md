# Three-step pipeline — TODO

## Implementacja

- [x] PLAN + TODO w PLANS/
- [ ] `prompts/step_classify_system.md` — klasyfikator (kategorie + lang, bez NER)
- [ ] `prompts/schema_classify.json` — `{language, category}`
- [ ] `lib/pipeline_threestep.py` — `process_classify`, `process_meta`, `process_entities`
- [ ] `scripts/run_threestep.py` — orchestrator z 3 asyncio.Queue
  - [ ] flagi: `--limit N --concurrency-classify C1 --concurrency-meta C2 --concurrency-entities C3 --tag X --random --seed 42 --skip-junk`
  - [ ] writer per faza (append do *.jsonl, idempotencja po url_hash)
  - [ ] joiner do final.jsonl po skończeniu (lub on-the-fly)
  - [ ] timing.csv per faza
  - [ ] run_meta.json z config + history segmentów
- [ ] smoke test: 5 URL, eyeball
- [ ] run 500 URL z seed=42 (subset baseline5000)
- [ ] aktualizacja `PLANS/threestep_pipeline_plan.md` o wyniki (sekcja "Wyniki P1")

## Pomiary po runie 500

- [ ] wall time total + per phase
- [ ] % junku, fail rate per phase
- [ ] classifier latency mean/p95
- [ ] porównanie z baseline5000 (te same url_hash) — Jaccard encji, category match, missing SEO

## Po sukcesie P1

- [ ] Wpis w DECISIONS.md (D7c: three-step jako kandydat)
- [ ] Pełen run 5000 (Phase P2)
- [ ] Jeśli pass D7c — propozycja zmiany defaultu (drugi wpis DECISIONS.md)

## Wyniki P1 — fail (zob. threestep_pipeline_plan.md sekcja "Wyniki P1")

- Wall 4.58 s/URL vs baseline 3.48 → +32% wolniej.
- Junk recall 8.9% (5/56 wspólnych) — classifier prompt zbyt konserwatywny.
- Entities Jaccard 0.552 vs baseline (różny prompt context).

## Iteracja P2 — kandydaty (kolejność wg kosztu pracy)

- [ ] **D**: dopisać w istniejącym `scripts/run_step2.py` skip dla `category=="junkey"`. Pomiar na 500 URL. Oczekiwana oszczędność ~5% wall, zero ryzyka. **Najpierw to.**
- [ ] **A**: tańszy classifier — obciąć input do 1000 znaków, cel ≤0.5 s/URL.
- [ ] **B**: łagodniejszy prompt junkowy z konkretnymi pozytywnymi przykładami (cookie wall, paywall stub, link farm).
- [ ] **C**: nowy `schema_step1_no_cat.json` — entities-only schema, kategoria z classifier'a wstrzyknięta do user-promptu.
- [ ] Jeśli A+B+C dają wall ≤ 0.92× baseline na 500 → P2 pełen run 5000 + decyzja D7c.

## v2 zaimplementowane (2026-05-08)

- [x] **A** — classifier truncated 1000 chars + binary `0`/`1` przez `guided_choice` → smoke 0.21 s/URL (12× szybciej vs v1)
- [x] **B** — prompt z few-shot examples (3 junk + 3 non-junk z baseline + 3 syntetyczne edge cases)
- [x] **C** — entities-only schema (`schema_entities_v2.json`); kategoria w meta (`schema_meta_v2.json`)
- [x] Per-stage logi: `classify.log`, `meta.log`, `entities.log`, `run.log`
- [x] Tmux session `benchmark` dla powtarzalnych runów
- [x] Smoke test 5 URL — pass
- [/] Pełen run 500 b2 (concurrency 1+3+4=8) — w trakcie

## v3 zaimplementowane (2026-05-08)

- [x] **Wzorzec A** — single `ThreadPoolExecutor(6)` + 3 priority queues (`classify > meta > entities`)
- [x] Concurrency=6 (Spark dławi się na 8)
- [x] Network retry dla classifier'a w `call_junk_classifier_binary`
- [x] `scripts/run_threestep_v3.py` — gotowy, czeka na wolny slot tmux

## Następne kroki

- [x] Dokończyć b2 → liczby w PLANS/threestep_pipeline_plan.md
- [x] Run v3 na 500 URL seed=42, tag `v3_500_c6_fix` (po naprawie load-balance bugu)
- [ ] **Fair-baseline run two-step** (concurrency=6 na 500 URL seed=42) — wciąż brakuje
- [ ] Porównanie 4-kolumnowe w tabeli (baseline5000 vs v2 b2 vs v3 vs baseline-fair)
- [ ] GPU util pomiar (`nvidia-smi dmon`) podczas v3/v4 — czy bumpować `--max-num-seqs`?
- [ ] Decyzja D7c → wpis w `DECISIONS.md` (only po fair-baseline porównaniu)

## v4 fourstep (2026-05-08)

- [x] Schema + prompt: `prompts/schema_sponsored_v1.json`, `prompts/step_sponsored_v1_system.md`
- [x] `lib/pipeline_fourstep_v1.py` — reuse v2 functions + `process_sponsored_v1`, `join_final_v4`
- [x] `scripts/run_fourstep_v1.py` — single pool 6 + 4 priority queues z load-balancingiem
- [x] Smoke n=5 — 3/3 z biznews.com.pl sponsored, 2/2 owner-commercial false (po fixie domain context)
- [x] Fix: `PUBLISHER DOMAIN: <domain>` w user-prompt — model rozróżnia internal vs external
- [x] Decyzja: `affiliate_review` to editorial (sponsored=false) — usunięte z subtype enum
- [ ] Pełen run na 500 URL — czeka na zielone światło użytkownika
- [ ] Eyeball ground-truth sponsored (n=200) — precision/recall walidacja
- [ ] Run v4 na intymnehistorie.pl i exposilesia.pl (nowo zescrapowane domeny)

## Scrapery (2026-05-08)

- [x] `scraper/scrape_domain.py` UPDATE: `RobotsChecker` per-domena cache + filtry na 3 etapach
- [x] `scraper/scrape_domain.py` UPDATE: Content-Type `text/html` filter w `crawl_urls`
- [x] `--ignore-robots`, `--user-agent` flagi
- [x] Sanity check na motoryzacjamag.eu, codziennyekspert.pl
- [x] Run intymnehistorie.pl: 139/139 OK, zero blocked, zero fails
- [/] Run exposilesia.pl: w trakcie (141 URL ze sitemapy)
