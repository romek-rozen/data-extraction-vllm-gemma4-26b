# PLAN: dashboard dla testu SPO v3 (rich JSON) — następna sesja

**Status:** TODO. **Zależność:** sequential bench v1+v2 musi się skończyć (current run
`final_results/2026-05-08_23-22-24__spo_v1_v2_seq_v3_seq/`, ETA niedziela 11.05 rano).
**Następnik:** `PLANS/spo_predicate_refinement_plan.md` (v4 closed enum).

## Po co dashboard

Sequential bench wyrzuca dwa katalogi (v1 cram, v2 split) z ~25k rich-JSON triples per
pipeline. Mamy dane, ale bez wizualizacji ciężko:
- ocenić jakość ekstrakcji (czy `subject_type` zgadza się z `entities[name].type`?
  czy `evidence_span` faktycznie potwierdza fact?)
- porównać v1 vs v2 (które daje czystsze relation_types? które ma lepsze coverage
  centralnych encji?)
- zobaczyć rozkłady (`confidence`, `relation_type`, `object_kind` entity vs literal,
  `object_type` × `relation_type` pivot)
- znaleźć dziwne triples (low confidence, s_unmatched, zerowy evidence_span)
- wybrać top-30 predicates do v4 closed enum

Istniejący `dashboard/views/spo.py` jest dla **legacy v1** (`entities_spo.jsonl`,
basic `s/p/o`). Trzeba **rozszerzyć lub napisać od nowa** dla rich JSON.

## Czego potrzebujemy w dashboardzie v3

### Run picker
Dropdown wszystkich runów `__spo_v1_v3*` i `__spo_v2_v3*` z `final_results/`. Plus
"compare two runs" mode (v1 vs v2 side-by-side).

### Top metrics tile
Z `run_meta.json` + `final.jsonl`:
- wall_s, URL/h, s/URL
- counters: classify_ok / junk %, entities_ok / fail, spo_ok / fail, meta_ok,
  sponsored_ok / sponsored_true %, final_ok / fail
- triples_total, entities_total, central_total
- s_unmatched_total + rate %
- parse_errors (powinno być 0 — sanity check xgrammar)
- avg confidence

### Triples explorer
DataFrame z 9 polami per triple + `url_hash`, `id`, `domain`, `primary_topic`,
`url`. Filtry:
- po `relation_type` (multi-select)
- po `object_kind` (entity / literal)
- po `confidence` slider (np. >0.7)
- po `subject_type` / `object_type`
- po domenie publishera
- po długości `evidence_span` (czy są puste / jednolinijkowe / długie?)
- search po `subject` / `object` / `predicate_phrase` (substring)

Każdy wiersz pokazuje + expandable row z:
- pełnym evidence_span
- linkiem do article (dashboard/views/articles.py? albo external `url`)
- innymi triples z tego samego artykułu (context)

### Distribution charts
- **`relation_type` top-50 bar chart** (per pipeline, +overlap chart v1∩v2)
- **`predicate_phrase` top-50** (multi-language, sygnał dla v4 enum)
- **`confidence` histogram** (binned 0.0-1.0, krzywa per pipeline)
- **`subject_type` × `relation_type` pivot heatmap** — które typy dla których relacji
- **`object_type` × `object_kind` pivot** — Entity vs Literal split per type
- **Triples per article** distribution (avg, p50, p95, max)
- **Centrality coverage:** % triples z central subject (czy model preferuje centralne
  encje jako subjects per D22 prior)

### Entity insights
- Top-100 most-frequent central entities (cross-article)
- Entity type distribution (Azure NER 51-type pie / treemap)
- Most "popular" entities (subject + object combined frequency)
- Domains × top entity per domain

### Audit / quality
- **Empty evidence_span** count (powinien być 0)
- **`subject_type` mismatch z entities[subject].type** count + sample
- **`s_unmatched` triples** (subject nie w entities array) — rare, ale flag
- **Confidence outliers** (<0.5 — może bytsię nie powinno emit)
- **Long predicate_phrase** (>80 chars — może to actually full sentence not phrase)
- **Single-character or numeric subjects** (Person="A", Number="2" without context)

### Performance per phase (z timing.csv)
- Latency distribution per phase (classify, entities, spo, meta, sponsored)
- Per phase ok / fail rate
- Outliers (>p95) per phase

### v1 cram vs v2 split compare
Side-by-side z `comparison_report.md` plus interactive chart:
- Triples per article (v1 vs v2 boxplot)
- Confidence distribution (v1 vs v2 KDE)
- relation_type Jaccard overlap top-50
- Wall_s per pipeline
- Ile triples ma identyczny `(subject, relation_type, object)` w v1 i v2 dla tego
  samego `url_hash` (precyzja zgodności)

## Implementacja (estymata 4-6h)

### Krok 1 — refactor `dashboard/views/spo.py`
Aktualnie czyta `entities_spo.jsonl` (legacy combined). Nowy v3 layout w `final.jsonl`
ma rich triples bezpośrednio. Schema:

```python
# v3 final.jsonl record
{
  "url_hash": "...", "id": "...", "url": "...", "domain": "...",
  "is_junk": false, "ok": true, "error": null,
  "primary_topic": "...",
  "central_entities": [{"entity_name": "...", "centrality": "primary"}, ...],
  "entities": [{"name": "...", "type": "...", "is_central": true, ...}, ...],
  "triples": [
    {
      "subject": "...", "subject_type": "...",
      "relation_type": "...", "predicate_phrase": "...",
      "object": "...", "object_type": "...",
      "object_kind": "entity|literal",
      "evidence_span": "...", "confidence": 0.95
    },
    ...
  ],
  # meta fields
  "language": "pl", "category": "...", "title": "...", "meta_description": "...",
  "h1": "...", "article_summary": "...",
  # sponsored fields
  "sponsored": false, "sponsored_subtype": null, "sponsored_justification": "",
  # ts
  "ts": "..."
}
```

- Zachować legacy widok dla starych runów (`__spo_v1` bez `_v3` w tagu) jako
  separate dropdown.
- Detect rich vs legacy po obecności `primary_topic` lub `triples[0].relation_type`.

### Krok 2 — funkcje agregujące
- `load_run(run_dir)` → `(meta, df_triples, df_entities, df_articles)`.
- `top_relation_types(df_triples, n=50)`.
- `confidence_histogram(df_triples, bins=20)`.
- `subject_object_type_pivot(df_triples)`.
- `quality_audit(df_triples, df_entities)` → dict z all the audit metrics powyżej.

### Krok 3 — UI sections (Streamlit)
- 1 sidebar (run picker, filters)
- 5-6 tabs: `📊 Overview` / `🔗 Triples Explorer` / `📈 Distributions` /
  `🏷️ Entity Insights` / `🔍 Quality Audit` / `🆚 v1 vs v2 Compare`

### Krok 4 — performance
- `@st.cache_data(ttl=300)` dla `load_run` i agregacji.
- Triples explorer: jeśli >100k triples, paginacja (st.dataframe nie radzi z 250k+).
- Lazy load: tab activation triggers compute (nie wszystko w main).

### Krok 5 — testowanie
- Smoke run z `--limit 100 --random` (oba pipeline'y) → dashboard pokazuje sensowne
  liczby.
- Edge cases: pusty `final.jsonl`, brak `triples`, parse error w `final.jsonl`.
- Cross-browser (jeśli używasz Chrome/Firefox/Safari).

## Nice-to-haves (drugi rzut, nie blockery)

- **Export filtered triples to CSV/JSON** (download button).
- **Knowledge graph viz:** dla pojedynczego artykułu, render entities + triples jako
  nodes+edges via `streamlit-agraph` lub `pyvis`.
- **JSON-LD preview:** dla wybranego artykułu wygenerować schema.org JSON-LD ze
  wszystkich triples (early demo "wartość dla SEO").
- **Compare different prompts:** jeśli zrobimy v4 z closed enum, dashboard porównuje
  v3 vs v4 (overlap predicates, coverage drift, evidence quality drift).

## Plik do utworzenia / refactoru

- `dashboard/views/spo_v3.py` (nowy, dla rich JSON; legacy `spo.py` zostaje dla starych
  runów)
- ALBO: `dashboard/views/spo.py` rozszerzyć o auto-detect i obsługiwać oba
- `dashboard/main.py` — register nową kartę

## Acceptance

- [ ] Dashboard otwiera oba aktualne run dirs (`__spo_v1_v3_seq`, `__spo_v2_v3_seq`)
      bez crashu.
- [ ] Top metrics zgadzają się z `run_meta.json` + `summary.txt`.
- [ ] Triples explorer paginated dla 25k+ rows.
- [ ] Top-50 `relation_type` chart per pipeline + overlap.
- [ ] `evidence_span` audit pokazuje sample low-quality triples.
- [ ] v1 vs v2 compare panel z konkretnymi metrykami (Jaccard, triples/art delta,
      avg confidence delta).
- [ ] Performance OK na MacBook (avg page load <3s).

## Po dashboardzie — sesja kolejna

Po użyciu dashboardu do harvest top-50 predicates → przechodzimy do
**`PLANS/spo_predicate_refinement_plan.md`**:

1. Mapping synonim clusters (`is_in/located_in/lives_in`, etc.).
2. Mapping na schema.org / ConceptNet / Wikidata (D17 follow-up).
3. Wybór finalnego enum ~25-30 predicates.
4. v4 schemas + prompts (`prompts/spo_pipe_v4_*` + `spo_entities_v4_*`).
5. Re-bench na 1000 art seed=42 z closed enum (wymuszany przez xgrammar).
6. Walidacja: 100% `relation_type ∈ enum`, parse_errors = 0, drift Jaccard vs v3.
7. Atrybuty (`has_hex_code`, `has_fat_content`, ...) wynieść z SPO do
   `entity.metadata` (Step 1 schema już ma to dla Quantity types).
8. DECISIONS D30 (closed enum + mappings).

## Po v4 enum — sesja jeszcze dalej

`PLANS/production_deployment.md`:

1. Pin `trafilatura` w `requirements.txt`.
2. Cache integrity verifier (`scripts/verify_cache.py`).
3. Test pełen flow Spark → tar.gz → rsync → prod local → load → run (na małym sample,
   może jakiś remote VPS jako stand-in dla RTX 6000 Pro hosta).
4. Pomiar `cache_warmup_meta.json` na rzeczywistym RTX 6000 hoście (kalibracja ETA
   dla 26M URL w obu strategiach).
5. Decyzja: build cache na Sparku czy na prod hoście? (zależy od dostępu do Sparka po
   migracji).
6. Production run scheduling (1× lub 2× RTX 6000 Pro, sequential strategy, idempotent
   writes, checkpoints/1000 URL, failed queue, backup S3/GCS).
7. Cost estimate (RunPod RTX 6000 Pro $/h × ETA = total budget).

## Linki

- `PLANS/spo_predicate_refinement_plan.md` — sesja po dashboardzie.
- `PLANS/production_deployment.md` — cache portability + prod deployment.
- `PLANS/rtx_pro_6000_optimization.md` — GPU-side optimizations dla prod.
- `PLANS/spo_v3_full_parallel_plan.md` — current run plan (sequential, conc=8).
- `SESSIONS_SUMMARY/2026-05-08_session_close.md` — what got us here.
- `dashboard/views/spo.py` — legacy SPO view (referencja do refactoru).
- `lib/spo_pipeline_v3.py` — current SPO impl (rich JSON cram + split).

---

**Estymata całej post-bench roadmapy:**

| Sesja | Co | Czas |
|---|---|---|
| 1 (next) | Dashboard SPO v3 (ten plan) | 4-6h |
| 2 | Predicate refinement v4 (closed enum) | 6-8h |
| 3 | Atrybuty z SPO → entity.metadata + walidacja | 3-4h |
| 4 | Production deployment plan + verifiers | 4-6h |
| 5 | Migracja na RTX 6000 Pro (RunPod setup, smoke 1000) | 6-8h |
| 6 | Production run 26M URL launch | start, monitor |

Total przed pełnym prod runem: **~25-35h pracy** w sesjach.
