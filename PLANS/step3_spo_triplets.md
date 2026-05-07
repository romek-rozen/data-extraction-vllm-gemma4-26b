# Eksperymentalny Step 3 — SPO Triplet Extraction

## Context

Cel: rozszerzyć dwustopniowy pipeline (Step 1 = encje Azure NER, Step 2 = SEO meta) o **eksperymentalny Step 3**, który na podstawie:
- listy encji z Step 1 (`entity_record.entities`),
- tekstu artykułu (markdown z `lib/data_loader.py`),

generuje **trójki semantyczne (Subject–Predicate–Object)** w postaci znormalizowanego knowledge graph per artykuł.

**Po co:** wzbogacenie warstwy semantycznej dla SEO/IR — encje same w sobie nie niosą *relacji* między bytami. Trójki dają:
- knowledge graph na poziomie artykułu (do internal linking, topical authority, FAQ/PAA),
- sygnał kontekstowy do embeddings/retrieval (np. retrieval-augmented),
- możliwość agregacji w graf domenowy (cross-article).

Zachowujemy filozofię repo: vLLM + xgrammar (`guided_json`), Google sampling defaults, idempotencja per `url_hash`, brak ingerencji w istniejący Step 1/Step 2 — Step 3 jest **opcjonalny** i włączany flagą.

## Research — jak to zrobić sensownie

### Opcje architektoniczne

| Opcja | Opis | Plusy | Minusy |
|---|---|---|---|
| **A. Joint w Step 1** | rozszerzyć schemat Step 1 o pole `triplets[]` | 1 inference, article już w prompcie | dłuższy output (+~30% completion), trudny A/B, łamie istniejący prefix cache (zmiana system promptu inwaliduje cache wszystkich URL już przetworzonych) |
| **B. Standalone Step 3** ✅ | osobne wywołanie po Step 1, z encjami w prompcie | modularny, łatwy A/B, nowy system prompt = własny prefix cache, można wyłączyć, inne sampling defaults | duplikujemy article w prompcie (brak współdzielenia z Step 1) |
| **C. Lekki post-process bez LLM** | regułowa ekstrakcja po PoS/dependency (spaCy) | bez LLM-overhead | jakość znacząco gorsza dla 140+ języków, wymaga modeli per-język, słabe predicates |

**Rekomendacja: Opcja B** — spójna z istniejącą architekturą, pozwala A/B testować jakość vs koszt bez ryzyka regresji w Step 1/2.

### Format trójek — closed vs open vocabulary

Trzy poziomy restrykcji predykatu:

1. **Open** — predicate jako wolny string (np. „is treated by"). Najbogatsze, ale szum, brak deduplikacji cross-article.
2. **Closed (controlled vocabulary)** — `enum` na predykacie, ~30–50 wybranych relacji. Przewidywalne, deduplikowalne, kosztują 1 token na predykat dzięki xgrammar enum.
3. **Hybrid** ✅ — `relation_type` z enum (closed) + opcjonalne `predicate_phrase` (free string, lokalny opis).

**Rekomendacja: Hybrid** — closed `relation_type` zapewnia agregację cross-article, `predicate_phrase` zachowuje niuans językowy.

#### Proponowany słownik `relation_type` (~32 typy, podzbiór Wikidata + dodatki)

```
Taksonomia:        instance_of, subclass_of, part_of, has_part
Lokalizacja:       located_in, origin_from, headquartered_in
Czas:              occurred_on, founded_in, ended_in, valid_from
Akcja/funkcja:     produces, uses, requires, performs, provides
Medyczne/przyczynowe: treats, causes, prevents, symptom_of, side_effect_of
Atrybucja:         has_property, measured_as, priced_at, rated_as
Społeczne:         created_by, owned_by, member_of, employed_by, collaborates_with
Relacja:           related_to, similar_to, opposite_of, derived_from, replaces
```

### Subject/Object grounding

Aby trójki były **agregowalne** i nie halucynowały, `subject` i `object` powinny być:
- preferencyjnie nazwami z `entity_record.entities[].name` (Step 1) → forsujemy `enum` na poziomie xgrammar,
- z fallbackiem na *typed literal* (Quantity, DateTime) gdy obiektem jest wartość, nie encja.

**Wzorzec schematu (skrót):**

```jsonc
{
  "type": "object",
  "additionalProperties": false,
  "required": ["triplets"],
  "properties": {
    "triplets": {
      "type": "array",
      "minItems": 0,
      "maxItems": 25,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["subject", "relation_type", "object", "object_kind", "confidence"],
        "properties": {
          "subject":        {"type": "string"},   // nazwa encji ze Step 1
          "subject_type":   {"type": "string", "enum": [<51 Azure types>]},
          "relation_type":  {"type": "string", "enum": [<32 relacje>]},
          "predicate_phrase": {"type": "string", "maxLength": 80},
          "object":         {"type": "string"},
          "object_kind":    {"type": "string", "enum": ["entity", "literal"]},
          "object_type":    {"type": "string", "enum": [<51 Azure types>]},
          "confidence":     {"type": "string", "enum": ["high", "medium", "low"]},
          "evidence_span":  {"type": "string", "maxLength": 200}
        }
      }
    }
  }
}
```

> **Uwaga o `enum` na nazwach:** xgrammar enum z dynamicznymi 50+ wartościami per request działa, ale schema musi być budowany **per artykuł** (z faktycznych encji Step 1). To inwaliduje schema cache xgrammar — koszt to +50–150 ms compile time per request. Akceptowalne (tłumi się przy concurrency). Alternatywa: nie wymuszamy enum, walidujemy post-hoc przez normalizację nazw.

**Decyzja:** **bez enum na subject/object** w schemacie — zamiast tego w system prompt twardo: *"subject and object MUST be names from entities_list provided"*; post-hoc filter w `lib/pipeline.py` odrzuca trójki, gdzie `subject` / `object` (po normalizacji) nie ma w encjach + jest typu `entity`. Daje kompromis: zero compile overhead, łatwa walidacja, prosta deduplikacja.

### Szacowany narzut (overhead)

Bazując na pomiarach repo (DECISIONS.md D12, snapshot z Phase 2):

| Komponent | Step 1 | Step 2 | **Step 3 (estymata)** |
|---|---|---|---|
| System prompt (cached) | 2 929 tok | ~1 200 tok | **~1 800 tok** (32 relacje + 5–7 few-shot) |
| User: article markdown | ~1 247 tok p50 | — (tylko encje) | ~1 247 tok p50 |
| User: entity list | — | ~400 tok | ~400 tok |
| Completion | ~301 tok p50 | ~180 tok | **~400 tok p50** (15 trójek × ~25 tok) |
| Latency p50 (concurrency 8) | ~1.7 s | ~0.9 s | **~1.6 s** |
| Wall-clock 100 URL @ c=8 | 21 s | 11 s | **~20 s** |

**Narzut sumaryczny:**
- **Per artykuł:** +~3 450 input tok + ~400 output tok ≈ **+~3 850 tok/URL** (~+45% vs Step1+Step2 łącznie).
- **Wall-clock E2E (100 URL, c=8):** z 32 s → ~52 s (**+62%**).
- **Wall-clock 21M URL na RTX 5090** (linearna ekstrapolacja z Phase 2): jeżeli Step1+Step2 = X dni, Step3 dokłada ~0.6X. Jeżeli X = 30 dni, Step3 = +18 dni. Można skrócić batchowaniem nocnym lub samplingiem (np. tylko TOP-1M domen).
- **Cache:** nowy system prompt ma własny prefix → po rozgrzaniu hit-rate ~76% jak w Step 1.

### Pułapki (do udokumentowania w DECISIONS.md jeśli wdrożymy)

- **`temperature` dla relacji:** Google default 1.0 może produkować halucynacje predykatów. Phase 3 A/B sugeruje 0.6–0.8 dla zadań strukturalnych — start z **0.7**, zmierzyć.
- **`maxItems: 25`** — z eyeballingu artykułów SEO 10–20 trójek na 1.2k tok wystarcza. Powyżej = długi ogon szumu.
- **Halucynacja subjektu** — model lubi tworzyć encje spoza Step 1. Stąd post-hoc filter + `evidence_span` (cytat z artykułu) jako sanity-check.
- **`additionalProperties: false`** — KRYTYCZNE w schemacie (zgodnie z konwencją Step 1/2), inaczej xgrammar pozwoli na drift.
- **Język predicate_phrase:** w języku artykułu (jak `article_summary` w Step 2), `relation_type` zawsze po angielsku (kanoniczny).
- **NIE używać `repetition_penalty > 1.0`** — łamie powtarzające się klucze JSON (znana pułapka repo).

## Plan implementacji (do wykonania po akceptacji)

### Pliki do utworzenia
- `prompts/step3_system.md` — system prompt EN: definicje 32 relacji, reguły grounding, 5–7 few-shot par (artykuł → trójki), constraint *"subject/object MUST come from entities_list"*.
- `prompts/schema_step3.json` — JSON Schema jak wyżej, `additionalProperties: false`, `maxItems: 25`.
- `scripts/run_step3.py` — wzorowany 1:1 na `scripts/run_step2.py`: ThreadPoolExecutor, idempotentny `JsonlReporter`, czyta `result/entity_layer.jsonl` + ładuje artykuły przez `lib/data_loader.load_articles`.
- `scripts/analyze_triplets.py` — sanity: rozkład `relation_type`, % trójek po post-hoc filter, top subjects, distinct edges.

### Pliki do zmiany
- `lib/pipeline.py` — nowa funkcja `process_step3(client, system, schema, article, entity_record, max_tokens, sampling)`:
  - buduje user prompt: `article["text"]` + zserializowane `entity_record["entities"]` (name+type),
  - wywołuje `client.chat_json(...)` (istniejąca metoda `lib/vllm_client.py`),
  - post-hoc filter: dla każdej trójki gdzie `object_kind == "entity"` lub zawsze dla `subject` → sprawdź czy nazwa jest w `{e["name"].lower() for e in entity_record["entities"]}`; odfiltruj „sieroty", zapisz licznik do `result.metadata`.
- `lib/config.py` — sekcja `STEP3`: `temperature=0.7, top_p=0.95, top_k=64, max_tokens=800, repetition_penalty=1.0`.
- `scripts/run_full.py` — flaga `--with-triplets` (domyślnie OFF) wywołująca Step 3 po Step 1, równolegle do Step 2.
- `lib/reporter.py` — bez zmian (już idempotentny po `url_hash`); nowy plik `result/triplets_layer.jsonl`.

### Output schema (per artykuł, dopisek do triplets_layer.jsonl)

```json
{
  "url_hash": "…",
  "ok": true,
  "latency_s": 1.61,
  "usage": {"prompt_tokens": 3492, "completion_tokens": 412, "total_tokens": 3904},
  "triplets_raw_count": 18,
  "triplets_kept_count": 15,
  "triplets": [
    {
      "subject": "rosół",
      "subject_type": "Product",
      "relation_type": "has_part",
      "predicate_phrase": "zawiera",
      "object": "marchew",
      "object_kind": "entity",
      "object_type": "Product",
      "confidence": "high",
      "evidence_span": "Do rosołu dodajemy marchew, pietruszkę..."
    }
  ]
}
```

### Krytyczne pliki do referencji (już zbadane)
- `lib/pipeline.py:170-260` — wzorzec `process_step1`, `process_step2`,
- `lib/vllm_client.py:28-130` — `chat_json` z `guided_json`,
- `prompts/schema_step1.json` — wzorzec schematu z `additionalProperties: false` + enum,
- `scripts/run_step2.py` — wzorzec idempotentnego runnera z thread pool,
- `lib/reporter.py` — `JsonlReporter` (idempotencja po `url_hash`),
- `INSTRUCTIONS_FROM_CLAUDE.md` — zaktualizować o sekcję Step 3 (eksperymentalny),
- `DECISIONS.md` — wpis D17 (lub kolejny): „Step 3 SPO triplets — vocabulary, sampling, post-hoc filter, A/B koszt".

## Weryfikacja end-to-end

1. **Smoke test (3 URL):**
   ```bash
   bash scripts/start_vllm.sh
   python3 -u scripts/run_step1.py --limit 3 --concurrency 1
   python3 -u scripts/run_step3.py --limit 3 --concurrency 1
   cat result/triplets_layer.jsonl | jq '.triplets[0]'
   ```
   Eyeball: czy trójki mają sens dla treści; czy subject/object faktycznie są z `entities`.

2. **Pomiar narzutu (100 URL):**
   ```bash
   python3 -u scripts/run_step3.py --limit 100 --concurrency 8
   python3 scripts/analyze_triplets.py --top 30
   ```
   Wyniki do D17: median latency, p95 completion_tokens, % filtered, top relation_types.

3. **A/B sampling Step 3** (po wstępnej walidacji):
   ```bash
   python3 scripts/ab_sampling.py --step 3 --limit 100 --concurrency 8
   ```
   Porównać `temperature` 0.5 / 0.7 / 1.0 — metryka: % „kept" po filter + distinct edges.

4. **Sanity schematu:**
   - `jq 'select(.ok==true) | .triplets | length' result/triplets_layer.jsonl | sort | uniq -c` — rozkład liczby trójek/URL.
   - 0 błędów `finish_reason: "length"` przy `max_tokens=800`.

## Otwarte pytania (do uzgodnienia przed implementacją)

1. **Zakres słownika relacji** — proponowane 32 typy OK, czy chcesz inny podzbiór (np. tylko SEO-relevant: ~12 typów)?
2. **`predicate_phrase`** w języku artykułu czy zawsze EN? (sugestia: język artykułu — bogatszy sygnał).
3. **`evidence_span`** — trzymać czy pominąć? (+~80 tok/trójkę output, ale ułatwia debug i dalsze QA).
4. **Włączenie do `run_full.py`:** Step 3 ma być domyślnie OFF (flaga `--with-triplets`), czy ON od razu?
