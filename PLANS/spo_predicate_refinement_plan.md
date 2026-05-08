# PLAN: SPO predicate enum refinement (PO benchach v3 rich-JSON)

**Status:** TODO — nie ruszać dopóki bench v3 (rich JSON, freeform predicates) nie skończy.

## Kontekst

W v3 (sesja 2026-05-08) wprowadziliśmy rich JSON output dla SPO z `relation_type` jako
**freeform string** (snake_case English, hint w prompcie, BEZ enum w schemacie). Cel: zebrać
empiryczną dystrybucję predykatów na realnej próbce (1000 art × 2 pipeline'y).

Wstępna obserwacja z v2 pipe smokes (251 unikalnych predykatów na 8 artykułów):
- Eksplozja synonimów (`is in / located in / lives in / is from / headquartered in`)
- Niespójność kierunku (`available in` vs `offers`)
- Atrybuty udające relacje (`has hex code`, `has fat content`) — powinny być w
  `entity.metadata`, nie w SPO triples.
- 5-tuples (extra-pipe) — zniknie w JSON format.

## Co zrobić po benchach

### Krok 1 — Harvest

Z `final_results/<rich_json_bench_dirs>/spo.jsonl` (lub `final.jsonl`) wyciągnąć
wszystkie `relation_type` i zliczyć dystrybucję:

```bash
cat final_results/*__spo_*_bench_*/spo.jsonl \
  | jq -r '.triples[]?.relation_type' \
  | sort | uniq -c | sort -rn > /tmp/relation_type_distribution.txt
```

Sygnały dla decyzji:
- Top-30 pokrywa zazwyczaj 80-90% wszystkich triples → naturalny cut-off.
- Wariancja PL vs EN article: jeśli model używa polskich `relation_type` mimo prompt-rule
  "English snake_case" — bug w prompcie do dociśnięcia.

### Krok 2 — Mapping na standardy

Dla każdego top-30 znaleźć alias w:
- **schema.org** (priorytet — daje JSON-LD export za darmo: `<script type="application/ld+json">`).
- **ConceptNet** (np. `HasPrerequisite`, `MannerOf`, `Causes`, `UsedFor`).
- **Wikidata properties** (jako reference, NIE używamy P-numerów surowo — 2719 properties
  zabije xgrammar compile, P31 nieczytelny w few-shot).

Tabela referencyjna (z drugiej sesji Claude):

| Twojeschema | schema.org | ConceptNet | Wikidata |
|---|---|---|---|
| `instance_of` | `additionalType` | `IsA` | P31 |
| `subclass_of` | — | `IsA` (klasa) | P279 |
| `part_of` / `has_part` | `isPartOf` / `hasPart` | `PartOf` / `HasA` | P361 / P527 |
| `located_in` | `containedInPlace` / `location` | `AtLocation` | P131 |
| `headquartered_in` | — | `AtLocation` | P159 |
| `founded_in` | `foundingDate` | — | P571 |
| `produces` | `manufacturer` (inv.) | `CreatedBy` (inv.) | P176 / P1056 |
| `uses` | — | `UsedFor` (inv.) | P2283 |
| `requires` | — | `HasPrerequisite` | — |
| `treats` | — | — | P2175 |
| `causes` | — | `Causes` | P1542 |
| `has_property` | `dependentHasProperty` | `HasProperty` | P1552 |
| `created_by` | `creator` / `author` | `CreatedBy` | P170 |
| `owned_by` | — | — | P127 |
| `member_of` | `memberOf` | — | P463 |
| `employed_by` | `worksFor` | — | P108 |
| `related_to` | `isRelatedTo` | `RelatedTo` | P1659 |
| `similar_to` | `isSimilarTo` | `SimilarTo` | — |

### Krok 3 — Inverse relations / canonical direction

Pułapka: `produces(Apple, iPhone)` vs `manufacturer(iPhone, Apple)`. Wybór jeden kierunek
per relacja. **Zasada:** active voice, agent first. W prompcie reguła:
"always use active voice: producer → product, container → contained, place → thing-located-there".

### Krok 4 — Strength flag

Zachować `strength: strong | weak` (analogicznie do encji):
- **strong** (Wikidata-linkowalne, wysoka pewność): `instance_of`, `produces`, `created_by`,
  `located_in`, `founded_in`, `headquartered_in`, `member_of`, etc.
- **weak** (kontekstowo-zależne, mogą generować szum): `related_to`, `similar_to`,
  `has_property` (ostatni resort).

### Krok 5 — Atrybuty z SPO → entity.metadata

Wszystkie `has_X | value` (gdzie X = hex_code, fat_content, interest_rate, weight, height,
duration itp.) wynieść do `entity.metadata: {key, value, unit}` w schemacie encji
(już teraz step 1 ma to dla Quantity types). Z SPO znikną ~30-40 atrybutowych predykatów.

### Krok 6 — Closed enum w v4 schema

`prompts/spo_predicates.json` jako single source of truth:

```json
[
  {
    "id": "instance_of",
    "category": "taxonomic",
    "schema_org": "additionalType",
    "wikidata": "P31",
    "conceptnet": "IsA",
    "strength": "strong",
    "description": "X is an instance of class Y. Apple → instance_of → company.",
    "examples": ["Apple|instance_of|technology company"]
  },
  ...
]
```

Skrypt builder schemy (auto-generowanie `enum: [...]` w `spo_schema_v4.json` i
`spo_pipe_v4_schema.json` z tej listy).

### Krok 7 — Update prompts (v4)

`spo_entities_v1_system.md` (wersja v4) i `spo_pipe_v2_system.md` (wersja v4):
- Zastąpić sekcję `### relation_type (string, ≤50 chars, snake_case...)` listą enumów
  z opisami i przykładami.
- Dodać twardą regułę "if no matching relation_type, use `related_to` with `confidence < 0.7`".
- Dodać sekcję "WRONG vs RIGHT" dla canonical direction.

### Krok 8 — Walidacja na hold-outcie

Re-run benchu (1000 art seed=42) z v4 promptami + enum schemą. Porównanie:
- Wszystkie triples mają `relation_type` z enum (xgrammar wymusza).
- `predicate_phrase` distribution — sygnał dla potencjalnych nowych predicates do dorzucenia.
- Coverage: jaki % triples z v3 (freeform) ma sensowny mapping na v4 enum (manual sample 100).

### Krok 9 — DECISIONS

D23 (placeholder w v3): "predicate enum bootstrap freeform" → finalize.
D26 (nowy): "predicate enum closed: 28 predykatów + 6 kategorii. Odrzucone: P-numery Wikidata
(xgrammar compile cost), free-form (251 unique na 8 art smoke = brak agregacji)".

## Acceptance criteria (kiedy plan zamykamy)

- [ ] `prompts/spo_predicates.json` z ~25-30 predykatami + mappingami.
- [ ] `prompts/spo_schema_v4.json` i `prompts/spo_pipe_v4_schema.json` z enum.
- [ ] `prompts/spo_entities_v1_system.md` v4 i `prompts/spo_pipe_v2_system.md` v4.
- [ ] Re-run bench 1000 art seed=42 z v4 — `relation_type` 100% w enum, parse_errors=0.
- [ ] DECISIONS D26 wpis.
- [ ] CHANGELOG entry.
- [ ] Atrybuty (`has_hex_code` etc.) wyjęte z SPO do entity metadata.

## Nie-cele (poza zakresem tego planu)

- Mapping na Google Knowledge Graph przez `sameAs` — w przyszłej sesji.
- JSON-LD export do `<script type="application/ld+json">` w content briefach — do step 4
  pipeline'u SEO.
- Cross-article merge identyfikacja duplikatów (entity resolution, fuzzy matching) —
  osobna duża sesja.
