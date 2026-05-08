# SPO v1 — bootstrap discovery plan

## Cel

Dodanie do pipeline'u trzech sygnałów semantycznych:
1. **Kanoniczne nazwy encji** (`open ai`, `OpenAI`, `OAI` → `OpenAI`).
2. **Flaga centralności** (`is_central`) — encje, o których artykuł jest **tematycznie**, max 5 per artykuł.
3. **SPO triples** (Subject-Predicate-Object) — fundament pod knowledge graph, **free-form predicates** (bootstrap discovery).

Obie pierwsze rzeczy dziedziczą z dotychczasowego stepu entities (jedno wywołanie LLM, model widzi tekst + listę encji w jednej odpowiedzi). Triplety wymuszają, żeby `s`/`o` matchowały `entity.name` (canonical).

**Architektura runa**: świadomie tylko **classify + entities_spo** (two-step). Pomijamy meta i sponsored — interesują nas TYLKO sygnały encyjne i graf. Jeśli wyniki będą dobre, kolejny etap = integracja z four-step (lub osobny pipeline produkcyjny).

## Decyzja architektoniczna: free-form predicates (bottom-up)

Zamiast projektować closed enum predicates z literatury (12 cherry-picked relacji typu `is_a`, `part_of`, `located_in`, ...), puszczamy **free-form predicates** z guidelines w prompcie (1-3 słowa, lowercase, czasownik, English) na pełnej próbce ~25k URL. Po runie:
- Statystyki top-N predicates w `SUMMARY.md` + dashboard
- Predicate clustering (Levenshtein) → kandydaci do zlewki
- Heurystyki: ile % triples pokrywa top-50 / top-100 / long-tail
- Decyzja closed vocab v2 (D9) podejmowana z danych

Argumenty za bottom-up:
- 25k URL × ~10 triples = ~250k triplet — większa próbka niż wszystkie KG-extraction papers cytują w benchmarkach
- Nie znamy dystrybucji domen (biznews, intymnehistorie, praktycznyekspert, ...) i ich typowych relacji
- Closed vocab z literatury (EDC, schema.org subset) ma bias — trening na English research papers, nie polskich blogach SEO

Argumenty przeciw (świadomy trade-off):
- Synonimy (`founded`, `established`, `created`) — wymagają post-hoc clusteringu
- Cross-language inconsistency (`tworzy` vs `creates`) — mitygujemy regułą "predicate w ENGLISH"
- Większy output → +tokeny → wolniej. Akceptujemy: noc, conc=8, ~5-8h estimate.

## Stack zmian

### Nowe pliki

| Plik | Rola |
|---|---|
| `prompts/spo_entities_v1_system.md` | Prompt: 51 Azure types + canonical rules + central flag rules + free-form SPO rules + 3 examples (PL+EN+recipe) |
| `prompts/spo_schema_v1.json` | JSON Schema: entities[{name, type, is_central}] + triples[{s, p, o}] |
| `lib/spo_pipeline_v1.py` | `process_entities_spo` (LLM call + dedup + cap is_central=5 + lowercase predicates + warning gdy s∉entities) + `join_final_spo` + `make_junk_stub_final_spo` |
| `scripts/run_spo_v1.py` | Two-step orchestrator (classify + entities_spo). Single ThreadPoolExecutor, 2 priority queues. CLI: --limit, --random, --seed, --concurrency, --tag, --websites, --resume |
| `scripts/spo_summary_v1.py` | Auto-summary: top-100 predicates, top-50 central entities, type×is_central, domain×junk, sample 30 triples, heurystyki dla closed vocab v2 |
| `dashboard/views/spo.py` | Karta `🕸️ SPO / Knowledge Graph`: top metrics, predicate distribution, predicate clustering hint, central entities, type×is_central, sample browser |

### Zmiany

| Plik | Akcja |
|---|---|
| `dashboard/main.py` | dodaj `from dashboard.views import spo` + entry `"spo"` w `PAGES` |

### Reuse (bez zmian)

- `lib.pipeline_threestep_v2.process_classify_v2` — junk classifier binary (head+tail input + URL signals)
- `lib.pipeline.enrich_entity` — mapowanie type → (category, strength)
- `lib.pipeline.dedup_entities` — case-insensitive dedup po (name.lower(), type)
- `lib.vllm_client.VLLMClient` — guided_json
- `lib.reporter.JsonlReporter` — append + idempotency po url_hash
- `lib.data_loader.load_articles` — random sampling z seedem
- `lib.prompt_loader.{load_system_prompt, load_schema}` — lru_cache

## Output schema (`final.jsonl`)

```json
{
  "url_hash": "...",
  "id": "...",
  "url": "https://...",
  "domain": "...",
  "is_junk": false,
  "ok": true,
  "error": null,
  "ts": "2026-05-08T...",
  "entities": [
    {"name": "OpenAI", "type": "Organization", "category": "Organization", "strength": "strong", "is_central": true},
    {"name": "Sam Altman", "type": "Person", "category": "Person", "strength": "strong", "is_central": false}
  ],
  "triples": [
    {"s": "OpenAI", "p": "founded by", "o": "Sam Altman"}
  ],
  "n_central": 1,
  "entities_raw_count": 24,
  "triples_raw_count": 8,
  "triples_s_unmatched": 0,
  "triples_o_unmatched": 1
}
```

## Próbki / runy

### Smoke (5 URL, conc=4)

```bash
python3 scripts/run_spo_v1.py --limit 5 --concurrency 4 --tag spo_smoke
```

Cel: weryfikacja schemy, post-processing, schema xgrammar nie pęka.

### Pełen run (wszystkie URL, conc=8, w tmux)

```bash
# Po skopiowaniu websites_praktycznyekspert/* → websites/
# (po smoke teście)
tmux send-keys -t benchmark "python3 -u scripts/run_spo_v1.py --limit 0 --concurrency 8 --tag full_bootstrap 2>&1 | tee /tmp/spo_v1_full.log" Enter
```

ETA: 25667 URL × ~3.9 s/URL / 8 wątków ≈ 3.5h teoretycznie, realistycznie 5-8h.

Output: `final_results/<ts>__spo_v1_full_bootstrap/`.

Auto-summary po runie generuje `SUMMARY.md` (top predicates, central entities, type stats, sample).

## Kryteria sukcesu (decyzja D9 closed vocab v2)

Pozytywne sygnały:
- **Triple grounding**: ≥90% triples z `s ∈ entities.name` (model nie halucynuje encji w triplets)
- **Canonicalization**: spot-check 50 nazw — ≥80% w spodziewanej kanonicznej formie
- **Predicate concentration**: top-50 predicates pokrywa ≥60% triples (akceptowalna dystrybucja dla closed vocab)
- **is_central precision**: spot-check 30 artykułów — central entities są naprawdę głównym tematem
- **Wall**: <10h dla 25k URL

Negatywne sygnały (rollback / poprawa):
- <70% grounding → rozważ indeksy w schemie (hybrid v2)
- >50% predicates jednorazowych → free-form za luźny, zamknij vocab
- Schema fail rate >5% → schema redesign

## Dokumenty

- `PLANS/spo_v1_bootstrap_plan.md` — ten plik
- `PLANS/spo_v1_todo.md` — actionable checklist
- `SESSIONS_SUMMARY/2026-05-08_spo_v1_design.md` — log sesji projektowej (aktualizowany na bieżąco)
- `DECISIONS.md` D8 — wpis decyzyjny (po smoke teście, przed pełnym runem)
- `DECISIONS.md` D9 — closed vocab v2 vs free-form (po pełnym runie + analiza)
- `CHANGELOG.md` — wpis po pełnym runie

## Future work (poza scope tego planu)

- **Closed vocab v2** (D9) — z danych bottom-up
- **Post-hoc EDC canonicalizer** — embedding similarity cross-article unifikacja
- **Knowledge graph viewer** — visnetwork / pyvis w dashboard
- **Multi-language predicate normalization** — czasowniki PL→EN map
- **Integracja z four-step** — meta + sponsored + entities_spo w jednym pipeline produkcyjnym
- **Triple validation by judge** — drugi LLM weryfikuje faithfulness (drogie, deferred)
