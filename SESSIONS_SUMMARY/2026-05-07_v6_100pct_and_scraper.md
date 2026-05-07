# Phase 5 v6: 100% sprawdności + crawl4ai scraper + auto-timestamp output

**Data:** 2026-05-07 (kontynuacja sesji `2026-05-07_two_step_pipeline.md`)
**Cel sesji:** Doprowadzić pipeline do 100% reliability (zero failów), dodać scraper do pobierania nowych domen w formacie Mateusza, ujednolicić output do `final_results/<timestamp>/`.
**Repo:** [github.com/romek-rozen/data-extraction-vllm-gemma4-26b](https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b)

## TL;DR

- Pełen run v5 na 155 URL: **99.4% Step 1, 98.1% Step 2** (4 failsy: 1× truncated metadata + 3× patologiczne loopy w Step 2).
- Diagnoza: wszystkie 4 fails to `finish_reason=length` → hit `max_tokens=2000`. Step 1: model generował metadata dla wielu encji liczbowych. Step 2: xgrammar **nie egzekwuje `maxLength`** na stringach → model się zapętlał.
- Wprowadzone zmiany v6: usunięcie `metadata` ze schematu Step 1, retry-with-feedback w VLLMClient, bump `MAX_TOKENS_STEP1` 2000→4000, `MAX_TOKENS_STEP2` 2000→2000 (z testowego 600 wycofane — bufor + retry wystarczają).
- Rerun v6 na 155 URL: **100% Step 1 (155/155), 100% Step 2 (155/155), -20% czasu** (12.7 min → 10.2 min).
- Dorzucone narzędzie: `scraper/` z `crawl4ai 0.8` + playwright/chromium do pobierania całych domen w formacie Mateusza (`<md5(url)>/{html.gz, json.gz}`).
- Pobrano **2112 URL** z 3 domen: artystyczna.pl (205), folkowa.art.pl (126), webporadnik.pl (1781). Razem z istniejącymi mateuszowymi 155 → **2267 URL** w `websites/`.
- `scripts/run_full.py` przepisany — domyślny output `final_results/<YYYY-MM-DD_HH-MM-SS>/` + opcjonalny `--tag <name>`.

## Diagnoza failów v5

Po pierwszym pełnym runie (final_result/) na 155 URL:

```
Step 1: 154/155 OK (1× 90402fc7 magnez-b6, finish_reason=length, completion=2000)
Step 2: 152/155 OK (3× weganska-szarlotka, podklady-pod-tort, …, finish_reason=length)
```

Wszystkie 4 fails — `truncated_at_max_tokens`. Dla Step 1 to długi artykuł suplementacyjny z 50+ encji × ~30 tok metadata ≈ 1500 tok wyjścia + bufor → 2000 hit. Dla Step 2 (4 stringi, ~250-350 tok realnie) hit 2000 = patologiczny loop modelu w polu `article_summary` — `maxLength` w schema **nie jest** egzekwowany przez xgrammar (znane).

## Zmiany v6

### A. Schema Step 1 v6 — usunięte `metadata`

`prompts/schema_step1.json` (v6): encja teraz `{name, type}` only. Pole `metadata` (opcjonalne dla 18 typów Quantity/DateTime) wycięte. `category`, `strength` zawsze były deterministycznie mapowane po typie w `lib/pipeline.py:TYPE_TO_CATEGORY` — to nie zmiana.

`prompts/step1_system.md` (v6): wycięty cały rozdział ENTITY METADATA (~150 linii: schemas per typ, rules, IncorrectVsCorrect przykłady metadata). Zostały: type taxonomy (51 typów), category enum (41), disambiguation rules, examples bez metadata. Nowy rozdział `OUTPUT BUDGET`: max 60 encji per artykuł, quality over quantity.

Backupy: `prompts/schema_step1_v5_backup.json`, `prompts/step1_system_v5_backup.md` (do rollbacku, gdyby okazało się że metadata jednak są potrzebne dla downstream).

**Trade-off:** tracimy znormalizowaną reprezentację jednostek (`{unit: "Celsius", value: 190}` zamiast samego `"190°C"`). Gdy potrzeba — można dorobić deterministyczny `enrich_metadata.py` na regex/parsach (Step 1.5).

### B. Retry-with-feedback w `lib/vllm_client.py`

Przy `finish_reason=length` lub JSON parse error model dostaje **kolejny turn z feedbackiem**:

```
ERROR: <opis>
YOUR PREVIOUS OUTPUT (last 1500 chars, may be truncated):
```<...>```

FIX REQUIRED:
- Return ONE complete and valid JSON object matching the schema.
- You now have a budget of <new_max_tokens> output tokens — fit within it.
- If truncated: be MORE CONCISE, keep entity names short, reduce count.
- If parse error: fix escaping, brackets, no trailing text.
```

Każdy retry: `temperature ×= 0.5`, `max_tokens ×= 1.5`, max 2 retries quality. Network retries (timeout, connection) niezależne — pozostały 2.

Plus subtelność: nawet jeśli JSON się parsuje, ale `finish_reason == "length"` — traktujemy jako quality fail i robimy retry. Bo xgrammar potrafi domknąć formalnie poprawny JSON w środku tablicy encji (obcięty content, ale `]` dorzucone).

### C. `lib/config.py`: bumpy max_tokens

```python
MAX_TOKENS_STEP1 = 4000   # 2000 → 4000 (długie artykuły 50+ encji)
MAX_TOKENS_STEP2 = 2000   # zostało 2000 (bufor + retry wystarczają;
                          # eksperymentalnie próbowane 600 fail-fast,
                          # ale 2000 daje modelowi powietrze, retry łapie loopy)
```

### D. `lib/pipeline.py` — uproszczone `enrich_entity`

Bez `_clean_metadata()` (no-op w v6, schema nie zwraca metadata). Encja po enrichu: `{name, type, category, strength}`.

Record dostaje pole `attempts` (1 = pierwszy strzał OK, 2-3 = retry).

## Wyniki v6 (155 URL)

```
Step 1: 155/155 (100.0%)  fail=0   427.8s  (2.76 s/req)
Step 2: 155/155 (100.0%)  fail=0   186.9s  (1.21 s/req)
TOTAL: 614.7s = 10.2 min
```

| Metryka | v5 | **v6** | Δ |
|---|---|---|---|
| Step 1 OK | 99.4% | **100%** | +1 |
| Step 2 OK | 98.1% | **100%** | +3 |
| Step 1 czas | 547.9s | **427.8s** | **-22%** |
| Step 2 czas | 215.1s | **186.9s** | **-13%** |
| Step 1 output median | 572 tok | 465 tok | -19% |
| Step 1 output max | 1842 tok | 1000 tok | -46% |
| Step 2 output max | 236 tok | 214 tok | -9% |
| **Razem** | 12.7 min | **10.2 min** | **-20%** |

`attempts=1` dla wszystkich 310 requestów — retry-with-feedback nie aktywował się ani razu w tym runie. Działa jako safety net dla produkcji.

Wnioski:
- Wycięcie `metadata` było główną dźwignią (-19% mediany outputu, -46% max).
- Schema bez metadata + większy bufor + retry = pipeline produkcyjnie odporny na patologiczne edge cases.

## Scraper (`scraper/`)

### Cel

Pobieranie całych domen w formacie Mateusza (`websites/<md5(url)>/{html.gz, json.gz}`) z anti-bot, JS rendering, sitemap discovery.

### Stack

- **`crawl4ai 0.8.6`** + playwright/chromium (~107 MB chromium-headless-shell). Anti-detection przez `magic=True`, `simulate_user=True`, `override_navigator=True`.
- **`lxml`** do parsowania `<title>`, `<meta name="description">`, h1-h6.
- **`requests`** do fetchowania sitemap.xml.
- **lokalny venv** w `scraper/.venv/` (gitignored) — nie zatruwamy globalnego Pythona.

### Tryby discovery

```bash
# 1. Sitemap (najszybsze, idzie po znanych URL)
scraper/.venv/bin/python scraper/scrape_domain.py \
    --sitemap https://example.com/sitemap.xml \
    --out-dir websites_new --concurrency 4

# 2. BFS od homepage (gdy brak sitemapy)
scraper/.venv/bin/python scraper/scrape_domain.py \
    --domain https://example.com --max-urls 500 \
    --out-dir websites_new --concurrency 4

# 3. Plik z URL-ami (1 linia = 1 URL)
scraper/.venv/bin/python scraper/scrape_domain.py \
    --urls-file urls.txt \
    --out-dir websites_new --concurrency 4
```

### Format wyjściowy (1:1 z Mateuszem)

```json
{
  "url": "...",
  "url_finish": "...",
  "http_code": 200,
  "http_code_finish": 200,
  "headers": [{"level": 2, "text": "..."}, ...],
  "title": "...",
  "description": "..."
}
```

Idempotencja: katalog `<md5(url)>/` istniejący = skip URL.

### Wyniki sesji — pobrane domeny

3 sitemapy, 3 równoległe scrapery (concurrency=4 każdy) w tmux `benchmark`:

| Domena | URL | OK | Fail | Czas | Notatki |
|---|---|---|---|---|---|
| artystyczna.pl | 211 | 205 | 6 | ~3 min | sitemap-index, parę 404 |
| folkowa.art.pl | 126 | 126 | 0 | ~20 sek | płaska sitemap, idealnie |
| webporadnik.pl | 1781 | 1781 | 0 | ~10 min | sitemap-index, czysto |

**Razem: 2112 nowych URL pobranych.** Dodanych do `websites/` → łącznie 2267 katalogów (155 Mateusz + 205 + 126 + 1781).

## `scripts/run_full.py` — auto-timestamp output

`--out-dir` zmienione z `required` na opcjonalne. Domyślnie:

```
final_results/<YYYY-MM-DD_HH-MM-SS>/
final_results/<YYYY-MM-DD_HH-MM-SS>__<tag>/    # gdy --tag <name>
```

Powód: każdy run zapisuje się do osobnego datowanego katalogu — nic nie nadpisuje, łatwo porównywać runy. Stary tryb `--out-dir custom` wciąż działa.

`.gitignore`: dodane `final_results/`, `websites_new/`, `scraper_logs/`.

## Commity tej sesji

```
c645df8  Phase 5 v6: 100% reliability — usunąć metadata ze Step 1, retry-with-feedback, bump max_tokens
5647c3d  config: MAX_TOKENS_STEP2 600→2000 (więcej powietrza, retry-with-feedback łapie loopy)
7d6edf9  scraper/: skrypt do pobierania całych domen do formatu Mateusza
c33bb52  run_full.py: domyślny output do final_results/<timestamp>/ + opcjonalny --tag
```

## Co zostało

- Pełen pipeline run na **2267 URL** (final_results/<ts>__v6_full/) — szacowany czas ~30 min @ concurrency 8 z prefix cache cieplejszym (warto włączyć `enable_prefix_caching` w vLLM jeśli już nie jest).
- Phase 6: SQLite storage layer (insert encji + final.jsonl) → `result.db` z indeksem po `url_hash`, `domain`, `category`, `entity_type`.
- Phase 7: migracja na RTX 5090 (RunPod) — natywne FP4, ~3-5× szybciej niż Spark Marlin fallback.
- Step 3 eksperymentalny — SPO triplet extraction (Subject–Predicate–Object) z kontekstu encji + tekstu (plan w `PLANS/step3_spo_triplets.md`).

## Pułapki nauczone w tej sesji

1. **xgrammar ignoruje `maxLength` na stringach** w `response_format: json_schema`. `maxLength: 400` w schemacie Step 2 nie chroni przed patologicznymi loopami — model może wygenerować 2000+ tokenów stringa. Fix: niski `max_tokens` budget + retry-with-feedback.
2. **`finish_reason=length` z parsowalnym JSON** — bywa, że JSON dochodzi do `]` w środku tablicy bo xgrammar wymusza zamknięcie. Treat as quality fail, nie sukces.
3. **`metadata` w schema Step 1 to deadweight przy 50+ encji.** Każda encja Quantity/DateTime ~30 tok metadata. Wycięcie = -19% mediany outputu + 0 truncate.
4. **PEP 668 / `--break-system-packages`** — Ubuntu 24.04 (DGX Spark) blokuje pip globalnie. Zawsze venv lokalny per podprojekt (np. `scraper/.venv/`).
5. **crawl4ai 0.8.x** — `__version__` jest podmodułem, nie atrybutem (`from crawl4ai.__version__ import __version__`). Drobne, ale nieoczywiste.

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- crawl4ai: https://github.com/unclecode/crawl4ai
- vLLM xgrammar maxLength issue: znane od dawna, brak fix-a — workaround przez `max_tokens` + retry.
- Poprzednia sesja: [`SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md`](2026-05-07_two_step_pipeline.md)

## Statystyki

- **Czas pracy:** ~2h (kontynuacja)
- **Commitów:** 4
- **Plików zmienionych:** ~10 (lib/, prompts/, scripts/, scraper/, .gitignore)
- **Nowych komponentów:** scraper/ (3 pliki + venv)
- **URL'i pobranych:** 2112 (3 nowe domeny)
- **URL'i przetestowanych:** 155 × 2 (v5 + v6 reruny)
- **Sprawność osiągnięta:** **100%** (cel sesji ✅)
