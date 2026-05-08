# Sesja 2026-05-08 — SPO v1 design + bootstrap discovery

## Punkt wyjścia (kontynuacja sesji 2026-05-08 — fourstep + sponsored)

Stan przed sesją:
- `final_results/2026-05-08_15-53-53__fourstep_v1_v4_1000_v2_1/` — wall 3897s, junk 16.6%, sponsored 64.6%, 0 fails. Pipeline four-step v2.1 z URL signals + head+tail + technical-context anti-signal działa precyzyjnie.
- Scraper praktycznyekspert.pl: ~2997 URL gotowych w `websites_praktycznyekspert/`, leciał w tmux scraper.
- Aktywny run w tmux benchmark: `v2_1_500_seed123` (medium 500 URL, seed=123) — sprawdzanie false positives na junk i sponsored na nowym seedzie.

## Decyzje tej sesji

### 1. Cel: dodanie SPO + canonical + central do entities

User chce do encji:
- **SPO triplety** (Subject-Predicate-Object) — fundament pod knowledge graph
- **Centralność** encji (max 3-5 per artykuł)
- **Kanoniczne nazwy** (`open ai` / `OpenAI` / `OAI` → `OpenAI`)

### 2. Architektura: 1 LLM call (rozszerzenie entities), nie osobny step

User: "ale wiesz co 1 vs 2 jest takie ze encje i tekst musimy wyslac wtedy do modelu" — argument za jednym wywołaniem (model widzi tekst + listę encji w jednej odpowiedzi → spójność wymuszona).

### 3. Kanonizacja: pole `name` jest już kanoniczne (bez `canonical_name`)

User: "a nie mozna w prompcie wytlumaczyc ze entity_name ma byc kanoniczne?" — eliminujemy duplikację. Model dostaje twardą instrukcję żeby `name` był kanoniczny (Wikidata/Wikipedia label).

### 4. Centralność: boolean `is_central` + cap 5

Prosty boolean. Cap top-5 wymuszony promptem ORAZ post-processingiem (`_cap_central` w `lib/spo_pipeline_v1.py`).

### 5. Predykaty: free-form bootstrap (NIE closed vocab z literatury)

User w pewnym momencie zaproponował closed vocab z mapowaniem na schema.org / ConceptNet / Wikidata, ale potem zauważył:
> "ale jak tak zobie mysle to moze bysmy to na calej probce z free i na podstawie wyniku zobaczymy co sie stanie?"

Decyzja: **bottom-up discovery**. Free-form predicates z guidelines (1-3 słowa, lowercase, English verb phrase) w prompcie. Zliczamy top-N po runie, decyzja closed vocab v2 dopiero z danych.

### 6. Pipeline w test-runie: tylko classify + entities_spo (two-step, NIE four-step)

User: "mozemy w sumie to puscic na test bez zadnych dodatkowych rzeczy, czyli tylko junk klasyfikacja i potem to co nie jest junkiem wrzucic na entities z promptem juz dla spo".

Pomijamy meta i sponsored. Skupiamy się na sygnałach encyjnych + grafie. Po dobrych wynikach: integracja z four-step lub osobny five-step.

### 7. Concurrency=8, pełen run w tmux nocą

User: "pamietaj zeby run puscic na tmuxie juz na wsszysktich artykulach, bedziemy mieli duza probke. i dorob to tego dashboard karte jakas i zeby wygenerowalo sie rzetelne summary po tym runie bo to duo godzin nam zajmie generowanie tego. Dlatego mozesz tez puscic na concurency=8 bo i tak mamy zablokowany komputer."

→ conc=8, wszystkie URL z websites/ (po cp z websites_praktycznyekspert/ → 25667 URL łącznie), tmux benchmark, auto-summary, dashboard view.

### 8. Konwencja nazw: underscore prefix `spo_`

User wybrał wariant: wszystko z underscore'ami (`lib/spo_pipeline_v1.py`, `scripts/run_spo_v1.py`, `prompts/spo_entities_v1_system.md`, `prompts/spo_schema_v1.json`).

## Implementacja

| Komponent | Plik | Status |
|---|---|---|
| Prompt | `prompts/spo_entities_v1_system.md` | ✅ |
| Schema | `prompts/spo_schema_v1.json` | ✅ |
| Pipeline lib | `lib/spo_pipeline_v1.py` | ✅ |
| Orchestrator | `scripts/run_spo_v1.py` | ✅ |
| Auto-summary | `scripts/spo_summary_v1.py` | ✅ |
| Dashboard view | `dashboard/views/spo.py` | ✅ |
| Routing | `dashboard/main.py` (edit) | ✅ |
| Plan | `PLANS/spo_v1_bootstrap_plan.md` | ✅ |
| TODO | `PLANS/spo_v1_todo.md` | ✅ |
| Sesja log (ten plik) | `SESSIONS_SUMMARY/2026-05-08_spo_v1_design.md` | ✅ (in progress) |
| Setup websites | `cp -rn websites_praktycznyekspert/* websites/` | ✅ (25667 dirs) |
| Smoke test | `python3 scripts/run_spo_v1.py --limit 5 --concurrency 4 --tag spo_smoke` | ⏳ |
| Pełen run | tmux benchmark, conc=8, --limit 0 --tag full_bootstrap | ⏳ |
| DECISIONS.md D8 | wpis o SPO + canonical + central, free-form bootstrap | ⏳ (po smoke) |

## Kluczowe pliki referencyjne

- `prompts/step_entities_v2_system.md` — prompt entities v2 (źródło dla v1 SPO)
- `lib/pipeline_threestep_v2.py:process_classify_v2` — junk classifier reuse
- `lib/pipeline.py:enrich_entity, dedup_entities, TYPE_TO_CATEGORY` — reuse
- `scripts/run_fourstep_v1.py` — wzorzec orchestratora (uproszczony do two-step w SPO v1)

## Wyniki — placeholder

(Wypełniane po smoke + pełnym runie)

### Smoke (5 URL, conc=4) — `final_results/2026-05-08_18-31-09__spo_v1_spo_smoke/`
- Wall: 23s, 0 fails
- Junk: 2/5 (40% — mała próbka)
- Entities: 33 (avg 11/non-junk), Central: 7 (avg 2.33), Triples: 36 (avg 12/non-junk)
- **Triple grounding: 100%** (0 z 36 triples ma s ∉ entities)
- **Issue wykryty**: Predicates mieszały PL/EN. Artykuł o truflach (PL): `rośnie w`, `preferuje`, `wykrywa`, `jest w`. Artykuł Minecraft (mix): `is in`, `requires`, `is used to make`. → **wymusiliśmy hard rule "predicate MUST be English"** w prompcie + przykład trufli z odwrotem (`grows in` zamiast `rośnie w`).

### Pełen run #1 (aborted) — `final_results/2026-05-08_18-31-59__spo_v1_full_bootstrap/`
- Stop po ~15 min, ~155 classify ok, 11 junk, ~0 entities_spo done.
- Powód: stara wersja promptu (predicates mieszane PL/EN). Restart z hard-rule.

### Pełen run #2 (aborted) — `final_results/2026-05-08_18-59-07__spo_v1_full_bootstrap_en/`
- Stop ~19:15. Powód: zauważyliśmy że `load_articles()` blokował GPU przez ~15 min (sekwencyjny trafilatura na 25k HTMLs). Decyzja: streaming loader (D17) + alternatywna architektura v2 pipe (D18). User: "zrob to na multiagentach".

### Multi-agent implementation (19:00 - 19:19)
Spawnowane 2 agenty równolegle:
- **Agent A** — `lib/streaming_loader.py` + cache `websites_cache/` + `scripts/test_streaming_loader.py` + `PLANS/streaming_loader_plan.md`. Smoke: cold 1.91s → hot 0.34s (5.6×). Producer ThreadPool n=4, bounded queue maxsize=200, cache versioning.
- **Agent B** — v2 pipe pipeline: `prompts/spo_entities_only_v2_*` + `prompts/spo_pipe_v2_system.md` + `lib/spo_pipeline_v2.py` + `scripts/run_spo_v2.py` + `PLANS/spo_v2_pipe_plan.md`. Smoke 5 URL: 0 parse_errors, 0 s_unmatched, 100% EN predicates.

Po zwrotach agentów: integracja streaming loader w `run_spo_v1.py` + `run_spo_v2.py` (flagi `--no-streaming`, `--loader-workers`, `--cache-dir`, default streaming ON).

### Smoke v1+streaming i v2+streaming (po integracji)
- v1: wall 26.2s, 5/5 final ok, 32 triples, **wszystkie predicates EN** (`grows in`, `requires`, `is in`, `can be decorated with`)
- v2: wall **18.2s** (-31%), 49 triples (+53%), 0 parse_errors. **Pipe format znacząco szybszy** mimo +1 step.
- websites_cache/ populowany 21 plikami markdown.

### A/B run #1 (aborted ~19:30)
- v1: `final_results/2026-05-08_19-19-45__spo_v1_AB_full/` (1516 classified, 22 tag false negatives)
- v2: `final_results/2026-05-08_19-19-45__spo_v2_AB_full/` (1817 classified, 27 tag false negatives)
- Stop bo zauważyliśmy 27% recall miss na tag pages. Decyzja D19 — v3 classifier z pre-filter URL regex.

### v3 classifier + pre-filter (19:30 - 19:45)
- `lib/junk_pre_filter.py` — deterministyczny regex match na 100% pewnych patternach junk: `/tag/`, `/tags/`, `/tagi/`, `/author/`, `/autor/`, `/archive/`, `/archiwum/`, `/search/`, `/szukaj/`, `?s=...`, `?paged=N`, `?start=N`, `/topic/`, `/temat/`, `/label/`, `/etykieta/`. Match → skip LLM, zapisz stub z `ml_skipped=True`.
- `prompts/step_junkclassify_v3_system.md` — sekcja "OVERRIDE URL signals" zastępuje "Strong URL signals". Tag z 1-2 wpisami → STILL JUNK. Examples K (single-entry tag), L (single-entry author archive).
- Integracja: `run_spo_v1.py` + `run_spo_v2.py` ładują v3 prompt + wstawiają pre-filter check przed `q_classify.put`.

### Streaming loader fix (lxml threading bug)
- Trafilatura 2.0.0 + lxml 6.0.4 ma bug thread-safety: 30 URL × 4 loader workers = `malloc(): mismatching next->prev_size` (heap corruption). Nie OOM.
- Fix: default `n_loader_workers=4` → `2`. Override przez `--loader-workers`. 30 URL conc=4 + lw=2 = stable, 156s, 0 fails, 0 s_unmatched.

### Output schema refinements
- Cache `.md` → `.json` (extension + content). Format `{"domain","url","content"}` (bez cache_version w pliku — w `_version.txt`).
- Output split: `entities_spo.jsonl` → `entities.jsonl` + `spo.jsonl` (v1, plus legacy `entities_spo.jsonl` dla resume).
- v2 rename: `entities_only.jsonl` → `entities.jsonl`, `spo_pipe.jsonl` → `spo.jsonl`.
- `spo_raw.txt` — surowy pipe output, same triplety bez headerów (autentyczny w v2, reconstructed z JSON w v1).

### A/B run #2 (start 19:47)
- v1: `final_results/2026-05-08_19-47-43__spo_v1_AB_v3/` w `tmux benchmark`, conc=4
- v2: `final_results/2026-05-08_19-47-43__spo_v2_AB_v3/` w `tmux benchmark2`, conc=4
- Pełne 25667 URL, total conc=8 = max vLLM
- Streaming loader: pierwsze classify w <5s
- Pre-filter skutecznie łapie tag/author URLs: w pierwszych 100 classify ~30-40% to pre-filter junks
- ETA: 5-8h, auto-summary po obu

### Dokumentacja (równolegle do A/B run)
- `CHANGELOG.md` — entry "2026-05-08 (wieczór)" z pełnym listingiem zmian
- `README.md` — nowa sekcja "SPO pipelines (knowledge graph foundation)" + komendy + streaming loader explanation
- `PLANS/spo_v1_bootstrap_plan.md` — design v1
- `PLANS/spo_v2_pipe_plan.md` — design v2
- `PLANS/streaming_loader_plan.md` — streaming + cache
- `PLANS/output_schema.md` — opis 4-file schema (classified, entities, spo, final + spo_raw + cache JSON)
- `PLANS/spo_v1_todo.md` — phased checklist (Phase 1-3 done, Phase 4 czeka na A/B)
- `DECISIONS.md` — D16 (SPO+canonical+central+free-form), D17 (streaming+cache), D18 (v2 pipe), D19 (v3 classifier+pre-filter)

### Wyniki — placeholder do wypełnienia po A/B
- ⏳ Wall v1, v2 (oczekiwany v2 ~30% szybszy z smoke extrapolation)
- ⏳ Total junk% (oczekujemy ~10-12% z baseline + dodatkowe ~5-10% od pre-filtra na pomocedlaseniora dataset)
- ⏳ Top-50 predicates coverage (decyzja closed vocab v2)
- ⏳ Predicate language compliance (% EN post hard-rule)
- ⏳ Triples grounding (s ∈ entities)
- ⏳ Cross-pipeline jaccard predicate overlap (czy v1 i v2 zgadzają się w trójkach na tych samych artykułach)

## Otwarte pytania na potem

- Czy `triples_s_unmatched` rate >10% wymaga przejścia na hybrid (indeksy entities)?
- Czy free-form predicates per language (ENGLISH-only) generuje spójne predykaty dla artykułów PL?
- Cap `is_central=5` dobry? Może per-długość artykułu?

## Linki

- Plan szczegółowy: `PLANS/spo_v1_bootstrap_plan.md`
- TODO: `PLANS/spo_v1_todo.md`
- Plan mode file: `/home/spark001/.claude/plans/dobra-to-zaczynamy-nowa-synthetic-rocket.md`
