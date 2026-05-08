# Session: SPO v3 rich-JSON + ProcessPool warmup + parallel/sequential orchestrators
## 2026-05-08 (od ~17:00 do 23:24 CEST, ~6.5h)

## TL;DR

Długi marathon nad SPO pipeline. Trzy duże zmiany architektoniczne:

1. **SPO v3 rich-JSON format** — zastąpienie pipe (`s|p|o`) bogatym JSON-em z 9 polami
   per triple (`primary_topic`, `central_entities[primary/secondary]`,
   `subject_type`, `relation_type`, `predicate_phrase`, `object_type`, `object_kind`,
   `evidence_span`, `confidence`). xgrammar `response_format: json_schema` daje 0% parse
   errors (było 2-7% w pipe format).

2. **ProcessPoolExecutor w streaming_loader** — diagnostyka pokazała że ThreadPool 8w
   na 20-rdzeniowym Sparku daje tylko **7/s** (GIL z Python-side trafilatura). Refactor
   na ProcessPool 64w → **274/s**, **39× speedup**. Krytyczne dla skalowania na 26M URL
   (44 dni → 1 dzień warmup).

3. **Drain-first worker scheduling + meta+sponsored steps** w SPO pipeline'ach. v1 i
   v2 dostały pełną quadruple stage (entities + spo + meta + sponsored).

Zakończone: **sekwencyjny benchmark v1 → v2 conc=8 leci** (od 23:22:24, ETA ~28h dla
v1 → ~57h total, master_dir w `final_results/2026-05-08_23-22-24__spo_v1_v2_seq_v3_seq/`).

## Chronologia (skondensowana)

### 17:00-19:00 — Drain-first scheduling fix (D21)
- Diagnoza: `spo_pipe.log = 0 B` po 17 minutach runa parallel v1+v2.
  classified.jsonl rosło, entities/spo zamarły.
- Worker priority `classify > entities > spo` + producer zalewający unbounded
  `q_classify` → starvation późnych etapów.
- Fix: drain-first (`spo > entities > classify` w v2; `entities_spo > classify` w v1).
  Bounded `q_classify = queue.Queue(maxsize=concurrency*8)`.
- Plus: rich entity context dla spo_pipe_v2 (`* name [type, central]` zamiast
  przecinkowej listy).

### 19:00-20:30 — Bug + meta/sponsored steps + stdout fix
- **Cichy worker death** w ThreadPoolExecutor — moja regresja w `process_spo_pipe_v2`
  (NameError na `entity_names`). Wszystkie 4 workery padły, producer blocked.
- Fix + zaktualizowano `lib/spo_pipeline_v1/v2.py`: dorzucono helpers
  `_merge_meta_into`, `_merge_sponsored_into`, extended joins z meta + sponsored.
- Każda pipeline SPO teraz robi: `classify → (entities + meta + sponsored)` + (dla v2)
  spo_pipe po entities. 4 LLM calls per non-junk article w v1, 5 w v2.
- `Tee(stdout/stderr) → out_dir/stdout.log` + dorzucono `FileHandler` do root loggera
  żeby łapać logger output (Tee na sys.stderr nie wystarczył bo logging trzyma
  reference do oryginalnego stderr z `basicConfig`).

### 20:30-22:00 — pipe → rich JSON migration (D23, D24, D25)
- Smokes pokazały **2-7% parse errors** w pipe format:
  - Extra-pipe: `lody|stored in|lodówka|at least|4 godziny` (5 segmentów)
  - Missing-pipe: `Badanie UFL|is non-invasive` (2 segmenty)
- Dwie iteracje wzmocnienia promptu nie wyeliminowały problemu — zaakceptowane jako
  fundamentalne ograniczenie modelu (nawet z self-check rules).
- **Decyzja:** rich JSON enforced przez xgrammar. Strukturalna poprawność = 100%.
- Nowy moduł `lib/spo_pipeline_v3.py` z `process_entities_spo_v3` (cram dla spo_v1)
  + `process_spo_pipe_v3` (split dla spo_v2) + `join_final_v3`.
- Nowe schemy + prompty v3.
- `relation_type` zostało **freeform string** (bootstrap dla harvest dystrybucji,
  closed enum dopiero w v4 po runie na full sample).
- Smoke v1 cram seed=42 n=10: 9.4 s/URL, 8.75 triples/art, **0 parse_errors**.
- Smoke v2 split seed=42 n=10: 13.3 s/URL, 11.88 triples/art, **0 parse_errors**.

### 22:00-23:00 — Cache warmup deep dive + ProcessPool refactor (D28)
- User: "ten format SPO jest za trudny" — discussions o usunięciu `subject_type`/`object_type`.
  Dane częściowe (354 art bench v1 cząstkowy) pokazały że subject_type 100% zgodne z
  entities[name].type → potencjalna redundancja. **Final decyzja:** ZOSTAJĄ w schemie
  (convenience > token cost) per użytkownika; harvest pól da więcej info.
- Cache warmup z 25668 art @ 16 ThreadPool workers szedł ~7/s — bottleneck zauważony.
- py-spy dump pokazał wszystkie 8 workerów w `lxml.text_content` ALE total CPU = 1.4 cores.
  Wniosek: trafilatura dużo Pythona po stronie heurystyk → GIL serializuje wątki.
- **Refactor:** `lib/streaming_loader.py` dostało parametr `executor_kind: "thread" | "process"`.
  Wyciągnięto `_load_one_core` na poziom modułu (picklable: tylko strings + ints).
  ProcessPool path: każdy worker independent Python interpreter, własny GIL, własny lxml.
  Stats agregowane z return tuple (parent process aggregates from worker deltas).
- A/B (clean, 200 art, cold cache):

  | Tryb | Workers | Throughput | Speedup |
  |---|---|---|---|
  | ThreadPool | 8 | 18/s | 1× |
  | ProcessPool | 8 | 53/s | 2.9× |
  | ProcessPool | 16 | **84/s** | 4.7× |
  | ProcessPool | 20 | 83/s | (saturated) |

- Live warmup w produkcji 16w: 25667 art w 186s = **137/s**.
- Test 64w (po pytaniu usera): **274/s**, 25667 art w 93.5s. Load avg 41 (oversubscription),
  RAM peak 108GB / 121GB (głównie vLLM unified memory). Stabilny rate od 1k do 25k art.

### 23:00-23:08 — Cache portability discovery (D29 candidate)
- User: "mozemy cache zbudowac na sparku i wgrac na maszyne produkcyjna co nie?"
- TAK — cache deterministyczny (`<sha256(url)>.json` z `{domain, url, content}`).
- Cache size: 181 MB raw / **44 MB tar.gz** (4.1× kompresja) dla 25667 art.
- Estymata 26M: ~45 GB tar.gz, transfer rsync ~8 min @ 1 Gbps.
- **Strategia prod:** build cache na Sparku ($0 operacyjne, ~26h dla 26M URL na 64w),
  rsync do RTX 6000 Pro host, `--no-warmup --no-clear-cache` na prod runie.
- Plan: `PLANS/production_deployment.md` z RAM budget per host (32/64/128 GB →
  32-96 ProcessPool workerów), CPU topology Spark (20 cores ARM, no SMT) vs prod
  (32-core x86 + SMT), deployment steps + TODO.

### 23:13-23:22 — Parallel A/B → kill → Sequential
- Najpierw odpalony parallel master (`run_spo_v1_v2_test.py`) z `--no-warmup
  --no-clear-cache`. Cache hot, idzie wprost na LLM v1+v2 conc=4 each.
- User: "czy sensowniejsze i szybsze bedzie sequentnial conc=8?"
- Analiza: total wall ~ten sam (GPU bottleneck nie skaluje powyżej batch 8), ale
  sequential daje **clean per-pipeline wall_s** dla ETA RTX 6000 Pro + max prefix
  cache hit + pierwsze wyniki @ ~t+28h.
- Kill parallel + nowy `scripts/run_spo_v1_v2_sequential.sh`.
- 23:20-23:22: user przypadkowo usunął pliki, restart cleanup + relaunch.
- **23:22:24:** sequential ruszył. v1 conc=8 najpierw, potem auto v2 conc=8, potem
  comparison_report.md.

## Pliki dotknięte (commits sesji)

Wszystkie pushed na `main`:

| Commit | Co |
|---|---|
| `dda30ac` | SPO v1+v2 + drain-first + rich entity context (Phase 1) |
| `ebff158` | SPO v1+v2: tee stdout/stderr → out_dir/stdout.log |
| `8140c60` | SPO v3 rich-JSON: schemas, prompts, lib v3, orchestrator switches |
| `b98fcc5` | lib v1/v2 helpers (prereq dla v3) |
| `8a8c7c2` | spo_v1 fix: spo_record=None explicit kwarg |
| `0e0d13a` | docs: CHANGELOG + DECISIONS D23/D24/D25 + SESSIONS_SUMMARY |
| `2c8d302` | overnight master script (deprecated by sequential) |
| `32bdbd8` | scripts/spo_compare_benches.py + master integration |
| `a105c3b` | full parallel A/B + maxItems removed (D26, D27) |
| `0598cb2` | maxItems removed w legacy v1/v2 schemas (consistency) |
| `a2fc0e8` | streaming_loader: ProcessPoolExecutor option (12× speedup) |
| `7db4969` | DECISIONS: D28 (ProcessPool option) |
| `f06b490` | PLANS/production_deployment.md (cache portability + 64w bench) |
| `9dd82a5` | scripts/run_spo_v1_v2_sequential.sh |

## Kluczowe decyzje (DECISIONS.md)

- **D21** — Drain-first worker priority + bounded q_classify (SPO v1/v2)
- **D22** — SPO v2 entity context: `* name [type, central]` zamiast przecinkowej listy
- **D23** — pipe → rich JSON (xgrammar guarantees structure, 0 parse errors)
- **D24** — `relation_type` freeform w v3 (closed enum dopiero v4 po harvescie)
- **D25** — spo_v1 cram vs spo_v2 split A/B (preliminary winner: v2 split)
- **D26** — schemas v3 maxItems removed (model autonomously decides count)
- **D27** — parallel run + cache gen mierzony osobno (od 23:13 do 23:22, później
  zastąpione sequential)
- **D28** — streaming_loader ProcessPool opcja (12-30× cache warmup speedup)
- **D29 candidate** — cache portability Spark→prod (`PLANS/production_deployment.md`)

## Pliki kodu (FILES.md odzwierciedla aktualny stan)

### Nowe
- `lib/spo_pipeline_v3.py` — rich JSON impl (cram + split funkcje)
- `prompts/spo_pipe_v3_system.md` + `spo_pipe_v3_schema.json`
- `prompts/spo_entities_v3_system.md` + `spo_schema_v3.json`
- `scripts/run_spo_v1_v2_test.py` — parallel orchestrator
- `scripts/run_spo_v1_v2_sequential.sh` — sequential orchestrator (current)
- `scripts/spo_v3_overnight.sh` — sequential overnight (deprecated)
- `scripts/spo_compare_benches.py` — v1 vs v2 markdown report
- `FILES.md` — file inventory
- `PLANS/spo_rich_json_v3_plan.md`
- `PLANS/spo_predicate_refinement_plan.md` (TODO na v4 enum)
- `PLANS/spo_v3_full_parallel_plan.md`
- `PLANS/production_deployment.md` (cache portability)
- `SESSIONS_SUMMARY/2026-05-08_spo_rich_json.md`

### Zmodyfikowane
- `lib/streaming_loader.py` (ProcessPoolExecutor option, _load_one_core picklable)
- `lib/spo_pipeline_v1.py` + `lib/spo_pipeline_v2.py` (helpers + extended joins)
- `scripts/run_spo_v1.py` + `scripts/run_spo_v2.py` (drain-first, meta+sponsored,
  v3 prompts/schemas, stdout tee + FileHandler)
- `prompts/spo_pipe_v2_system.md` (kept jako legacy pipe ref)
- `prompts/spo_schema_v1.json`, `spo_entities_only_v2_schema.json` (maxItems removed)
- `CLAUDE.md`, `CHANGELOG.md`, `DECISIONS.md` — refresh

## Wyniki kluczowe

### Smoke v3 rich JSON (n=10, seed=42, conc=8, cold cache)
- v1 cram: 9.4 s/URL, 8.75 triples/art, **0 parse errors**
- v2 split: 13.3 s/URL, 11.88 triples/art, **0 parse errors**

### Cache warmup (Spark, 25667 art)
- ThreadPool 8w: 7/s (50 min total, **OPRACOWANE jako bottleneck**)
- ProcessPool 16w: 137/s (3.1 min) — 19.5× speedup
- ProcessPool 64w: 274/s (1.6 min) — 39× speedup, RAM peak 108GB

### Cache portability
- Raw: 181 MB / 25667 art
- tar.gz: 44 MB (4.1× compression)
- Estymata 26M: 45 GB tar.gz
- Transfer @ 1 Gbps: ~8 min

### Spark thermal (sample 23:16 podczas LLM run)
- GPU GB10: 61°C, 38W, 96% util
- CPU 58-65°C
- Load 1.23
- Sprzęt nawet nie pocący się

## Aktualnie running (na koniec sesji 23:24)

- **tmux:** `spo_seq` (sequential orchestrator)
- **PID:** master 1601788, v1 1601810
- **Stage 1 (v1 cram conc=8)** w toku, vLLM 7-8 inflight
- **Master dir:** `final_results/2026-05-08_23-22-24__spo_v1_v2_seq_v3_seq/`
- **ETA:**
  - v1 cram conc=8 alone: ~28-35h (do ~04:00 May 10)
  - v2 split conc=8 alone: ~30-40h (do ~12:00-20:00 May 10/11)
  - **Total wall: ~58-75h** (do soboty 11.05 lub niedzieli 12.05 rano)
- **Po zakończeniu:** `comparison_report.md` w master dir z:
  - Wall_s per pipeline (clean — nie shared GPU)
  - Triples count distribution
  - relation_type top-30 distribution per pipeline + overlap
  - Confidence histogram
  - Junk % i Sponsored % (empirycznie z 25667 PL artykułów)

## Dalej (next session)

`PLANS/spo_predicate_refinement_plan.md`:
1. Po zakończeniu sequential — harvest `relation_type` distribution z `spo.jsonl`
   obu pipeline'ów.
2. Mapping synonim clusters (`is_in/located_in/lives_in/is_from`, etc.).
3. Wybór finalnego enum ~25-30 predykatów schema.org/ConceptNet aligned.
4. v4 schemas + prompts z closed enum w xgrammar.
5. Re-bench na 1000 art seed=42 dla walidacji 100% coverage.
6. **Atrybuty z SPO → entity.metadata:** wszystkie `has_X | value` (hex_code,
   fat_content, interest_rate, weight) wynieść do struktury encji (Step 1 schema już
   ma to dla Quantity types, rozszerzyć).
7. Pinować `trafilatura` w `requirements.txt` przed prod runa.

## TODO przed prod runa (po zakończeniu sequential)

- [ ] Pin `trafilatura==X.Y.Z` w `requirements.txt`
- [ ] Skrypt verify cache integrity (sprawdzenie `_version.txt` + sample JSON validity)
- [ ] Test pełen flow Spark → tar → rsync → prod local → load → run (mały sample)
- [ ] DECISIONS D29 — cache portability protokół (po empirycznym teście)
- [ ] Pomiar `cache_warmup_meta.json` na rzeczywistym RTX 6000 hoście (kalibracja
      ETA 26M URL)

## Kluczowe lekcje sesji

1. **Nigdy nie zakładaj że ThreadPool używa wielu rdzeni** — Python's GIL może to
   zablokować nawet gdy library claims releases. Zawsze pomiar `top -H` lub `htop`.
2. **xgrammar guided_json eliminuje całą klasę bugów** — model nie może emit
   malformed structured output. Cost: niewielki overhead, gain: 0 parse errors,
   zero retry logic, zero parser tolerance code.
3. **Drain-first scheduling** w bounded queues kluczowe dla multi-stage pipelines —
   inaczej producer głodzi późne etapy.
4. **Silent worker death** w ThreadPoolExecutor (i Process) jest zdradliwe — exception
   ląduje w future, ale `f.result()` woła się dopiero przy `pool.shutdown()`.
   Mitigation: tee stdout/stderr + FileHandler na root logger.
5. **Cache portability** odblokowuje strategię "preprocess on cheap, infer on
   expensive" — duża wygrana finansowa dla prod runa na płatnym GPU.
6. **Bootstrap discovery > pre-mature optimization** — closed predicate enum bez
   empirycznego rozkładu = ryzyko wybrać zły zestaw. Najpierw zbieramy dane (v3
   freeform), potem zamykamy (v4 enum).

---

**Dobranoc.** 🌙

Sesja zakończona 2026-05-08 23:24 CEST. Sequential master leci, jutro będą wyniki v1.
