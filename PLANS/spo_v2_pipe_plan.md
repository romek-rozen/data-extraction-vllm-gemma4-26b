# SPO v2 — Pipe-format three-step pipeline

## Cel

Baseline porównawczy do SPO v1 (single-call JSON entities+triples). Hipoteza: rozdzielenie ekstrakcji encji od ekstrakcji trójek, plus pipe-format dla SPO, da lepszą jakość przy mniejszym budżecie tokenów output.

## Motywacja (vs v1)

v1 (single-call):
- Jeden LLM call: `{entities, triples}` JSON.
- Output ~3-4k tok (50 ent × ~30 + 30 trip × ~50 + JSON overhead).
- Schema mocno wiążąca xgrammar — całość musi się zmieścić w `max_tokens=4000`.
- Triples i entities konkurują o ten sam budget.

v2 (three-step):
- Step 2 entities_only: tylko encje, ~2.5k tok output.
- Step 3 spo_pipe: pipe-format `s|p|o` per linia, ~1.2-2k tok (40 linii × ~30 tok). **~60% mniej output tokens niż JSON triples** (brak `{"s": "...", "p": "...", "o": "..."},` overhead — ~15 znaków na trójkę vs ~50).
- Każdy step ma osobny prompt (focused, krótszy, lepsza atencja modelu).
- Każdy step ma osobny budget tokenów (no contention).
- Spo step dostaje listę canonical names z entities_only — nie musi sam dedupować/canonicalizować.

## Architektura

```
[website] → classify (junk?)
              │
   junk       │   non-junk
   ↓          │   ↓
  stub       entities_only (JSON: {entities: [{name, type, is_central}]})
              │
              ↓ (entity names list)
            spo_pipe (raw text: subject|predicate|object\n × N)
              │
              ↓
            join_final_spo_v2 → final.jsonl
```

3 kolejki priorytetowe w jednym ThreadPoolExecutor:
- `q_classify` (NOW)
- `q_entities` (po classify OK + non-junk)
- `q_spo` (po entities_only OK)

## Format pipe — przykłady

PL article + EN predicates (HARD RULE):
```
Apple|released|iPhone 15
iPhone 15|uses|USB-C
iPhone 15|released in|wrzesień 2023
```

Reguły:
- Dokładnie 3 segmenty oddzielone `|`. Brak escape'owania.
- Subject — exact match z listy canonical entity names.
- Predicate — 1-3 słowa, lowercase, **EN regardless of article language**.
- Object — preferowany z entities, dopuszczalny literal (number/date).
- Max 40 linii.

## Schema entities_only_v2

```json
{
  "type": "object",
  "properties": {
    "entities": {
      "type": "array", "maxItems": 60,
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string", "maxLength": 100},
          "type": {"type": "string", "enum": [...51 Azure NER...]},
          "is_central": {"type": "boolean"}
        },
        "required": ["name", "type", "is_central"]
      }
    }
  },
  "required": ["entities"]
}
```

## Trade-offs

### Pros (v2 vs v1)
- Mniej tokenów output dla triples (~60% redukcja).
- Każdy step focused → potencjalnie lepsza jakość per step.
- Łatwiej zmodyfikować/zamienić jeden step bez ruszania drugiego.
- Spo prompt może mieć więcej miejsca na examples / hard rules.
- Lista canonical names jako input do spo step → mniej "wymyślania" entities w triples.

### Cons (v2 vs v1)
- 2× LLM calls dla non-junk URL (entities + spo) zamiast 1× → wall-time prawdopodobnie wyższy mimo mniejszego per-call output.
- Brak guided_json dla spo step → model może czasem złamać format (parse_errors). Mitigacja: clear prompt + parser tolerancyjny (skip bad lines, count, sample bad).
- Dwa miejsca, gdzie model widzi tekst artykułu → 2× input tokens (mitigowane prefix caching dla sys promptów, ale nie dla user/article).
- Więcej state do śledzenia w orchestratorze (entities w state przed spo step).

## Verification steps

1. **Smoke (5 URL):** `python3 scripts/run_spo_v2.py --limit 5 --concurrency 4 --tag spo_v2_smoke`
   - sprawdź `final.jsonl`: każdy rec ma `entities` + `triples`
   - sprawdź `triples` mają s/p/o, predicate jest EN, format 3-segmentowy
   - `parse_errors ≤ 2/5` akceptowalne
   - `triples_s_unmatched < 20%`
2. **A/B 100 URL vs v1** (random sample, ten sam seed):
   - speedup wall (v2 vs v1)
   - mediana tokenów output per article
   - liczba triples / liczba entities / cent_count
   - subjective eyeball: 10 URL — czy triples sensowne, predicates EN
3. **Otwarte:** comparator script (`scripts/compare_spo_v1_vs_v2.py`) — TODO follow-up.

## Otwarte pytania

- Czy worth użyć `guided_regex` dla spo step (`^[^|]+\|[a-z][a-z ]*\|[^\n]+$`)? Pro: gwarancja formatu, no parse_errors. Con: regex constraints w xgrammar są wolniejsze od json_schema, możliwa degradacja jakości. Decyzja: na razie raw, mierzymy parse_errors. Jeśli > 5% — przejdź na guided_regex.
- `temperature=1.0` vs niższa dla spo step? Google defaults dyktują 1.0, ale niższa może dać czystszy format. A/B w iteracji 2.
- Czy preserve `category` + `strength` z entities w spo step? Obecnie tak (enrich_entity), ale spo step nie używa — tylko `name`. Może uprościć rec.
- `spo_summary_v2.py` — TODO follow-up (parser format pipe + analiza predicate distribution analogicznie do v1).
- Jeśli model wyrzuci CoT przed pipe-output (wbrew prompt) — parser je odrzuca jako bad lines. Czy warto dodać heurystykę "find first valid `|` line" przed parserem? Na razie trzymamy strict — sygnał, że prompt potrzebuje wzmocnienia.

## Pliki

- `prompts/spo_entities_only_v2_system.md` — entities prompt (fork v1 bez triples)
- `prompts/spo_entities_only_v2_schema.json` — schema bez triples
- `prompts/spo_pipe_v2_system.md` — spo pipe prompt (HARD RULE EN predicates)
- `lib/spo_pipeline_v2.py` — process_entities_only_v2 + process_spo_pipe_v2 + join_final_spo_v2
- `scripts/run_spo_v2.py` — orchestrator 3-kolejkowy
- `PLANS/spo_v2_pipe_plan.md` — ten dokument
