# Dashboard Streamlit do analizy wyników two-step pipeline

**Data:** 2026-05-07
**Cel:** Postawić interaktywny front (Streamlit) do eksploracji wyników pipeline'u w `final_results/`, replikując strukturę dashboardu z `mateusz-g-json-vs-flat/app/`, ale dopasowaną do schematu two-step (Step 1 + Step 2).
**Stack:** Streamlit 1.56 + pandas 3.0 + plotly 6.7.

## TL;DR

- Migracja wyników: `final_result_v6/`, `final_result_v6_b/` → `final_results/{v6,v6_b}/` (l.mn. — runy jako podkatalogi).
- Domyślny `--out-dir` w `scripts/run_full.py` przeniesiony na `final_results/<timestamp>__<tag>/` (zmiana po stronie usera, dodaje auto-timestamp + `--tag`).
- 4 widoki: **Run Summary**, **Eksplorator artykułów**, **Encje** (typy/kategorie/strength/off-list), **Porównanie runów**.
- Dashboard działa pod `http://localhost:8501` (HTTP 200, replaced stary `json-vs-flat` na tym porcie).
- Smoke test: 3 runy (`20260507151000`, `v6`, `v6_b`), 459 wierszy Step1, 310 Step2, 11636 encji, 0 off-list, 458/459 OK.

## Co powstało

```
dashboard/
├── main.py                         ← entrypoint, nawigacja przez st.query_params
├── data_loader.py                  ← skan final_results/, JSONL → DataFrame, @st.cache_data(ttl=10)
├── components/
│   └── filters.py                  ← sidebar: run, only-OK, kategoria, język, domena
└── views/
    ├── run_summary.py              ← KPI per run + plotly histogram latencji + top kategorie + rozkład typów + długości pól SEO vs limity 70/160/100/400 + raw summary.md
    ├── articles.py                 ← tabela URLi → szczegóły: meta SEO z licznikiem znaków, encje, raw JSON Step1+Step2
    ├── entities.py                 ← top typy, off-list (poza 51 Azure), strength strong/weak, top encje (hallucinacje), per kategoria high-level
    └── compare_runs.py             ← KPI side-by-side, top 25 typów per run (bar chart), diff URLi obecnych w >1 runie, metrics_delta.txt
```

## Reuse

- `lib.pipeline.TYPE_TO_CATEGORY` — importowane bezpośrednio do `data_loader.py`. `KNOWN_TYPES = set(TYPE_TO_CATEGORY)` używane do wykrywania off-list (typu spoza 51 Azure NER → sygnał, że xgrammar nie zadziałał lub schema wycieka).
- `data_loader.explode_entities()` — DataFrame długi (1 wiersz = 1 encja, z kolumną `off_list`), używany przez `entities.py` i `compare_runs.py`.
- `data_loader.merge_steps()` — merge Step1+Step2 po `(run, url_hash)` z suffixami `_s1/_s2`.

## Pułapki napotkane

| Problem | Rozwiązanie |
|---|---|
| Port 8501 zajęty przez stary `mateusz-g-json-vs-flat` dashboard | Najpierw uruchomienie na 8502 dla testów; po `kill` starego procesu — przeniesienie na 8501 (target final). |
| `pd.json_normalize` rozbija `usage` na `usage.prompt_tokens` itd. (kropki w nazwach kolumn) | Akceptowalne — wystawiamy aliasy `prompt_tokens`/`completion_tokens` w loaderze. |
| `df.style.apply(...)` w `entities.py` (kolorowanie off-list na czerwono) → `AttributeError: '.style' accessor requires jinja2` | Wycięte. Kolumna `off_list` (bool) widoczna w tabeli — filtr po niej działa bez stylowania. (Alternatywa: `pip install jinja2` — nie zrobione, bo nie warto dla cosmetic.) |
| `final_result_v6_b/` ma tylko Step 1 (brak `final.jsonl`) | Loader: graceful — Step 2 frame puste dla tego runu, widoki sprawdzają `if not s2.empty`. |
| Default `--out-dir` w `run_full.py` był wymagany — utrudniało automatyzację | User dodał auto-timestamp (`final_results/<YYYY-MM-DD_HH-MM-SS>/`) + opcjonalny `--tag` → `final_results/<ts>__<tag>/`. |

## Konwencje

- **`final_results/` (l.mn.)** — root katalogu wynikowego, każdy podkatalog = jeden run. Replikuje konwencję `benchmark_results/<run-name>/` z `mateusz-g-json-vs-flat`.
- **Auto-skan**: dashboard skanuje cały `final_results/`, każdy podkatalog z `entity_layer.jsonl` lub `final.jsonl` traktuje jako run. Filtr "Run" w sidebarze multi-select (default = wszystkie).
- **Filtr only-OK ON** by default — w analizie zwykle interesują nas tylko zwalidowane wiersze.
- **Limity SEO** w jednym miejscu (`SEO_LIMITS = {"title": 70, "meta_description": 160, "h1": 100, "article_summary": 400}`) — używane w `run_summary.py` i `articles.py`.

## Polecenia

```bash
# Dashboard (target port 8501)
streamlit run dashboard/main.py --server.address 0.0.0.0 --server.port 8501

# Dostęp przez WireGuard
# http://10.13.13.5:8501  (wg0)
# http://10.10.0.3:8501   (wg1)

# Pipeline z auto-timestamp (po zmianie usera w run_full.py)
python3 -u scripts/run_full.py --limit 0 --concurrency 8                  # final_results/<ts>/
python3 -u scripts/run_full.py --limit 0 --concurrency 8 --tag v6_baseline # final_results/<ts>__v6_baseline/

# Smoke test loadera (bez Streamlit)
python3 -c "from dashboard.data_loader import load_results, explode_entities; \
  d=load_results(); print('runs:', d['runs'], 'step1:', len(d['step1']), 'step2:', len(d['step2'])); \
  e=explode_entities(d['step1']); print('entities:', len(e), 'off-list:', e['off_list'].sum())"
```

## Pliki zmienione/utworzone

| Plik | Akcja |
|---|---|
| `final_results/{v6,v6_b}/` | NEW (mv z `final_result_v6*/`) |
| `dashboard/main.py` | NEW |
| `dashboard/data_loader.py` | NEW |
| `dashboard/components/{__init__,filters}.py` | NEW |
| `dashboard/views/{__init__,run_summary,articles,entities,compare_runs}.py` | NEW |
| `requirements.txt` | + streamlit, pandas, plotly |
| `scripts/run_full.py` | przykłady → `final_results/baseline`; user dorzucił auto-timestamp + `--tag` |
| `CLAUDE.md` | + sekcja Dashboard w "Polecenia dev" |

## Następne kroki (sugestie, nie zrobione)

- Dodać `jinja2` do `requirements.txt` jeśli chcemy kolorowanie off-list w tabelach (`df.style.apply`).
- Cache `pipeline.log` (na żądanie, expander) — obecnie loader nie czyta logów per run.
- Diff Step 2 (title/meta/h1/summary) między runami w `compare_runs.py` — render side-by-side jest, ale brak markdownowego diff (`difflib.unified_diff`).
- Filtr po `entities_count` (range slider) — wykrywanie pustych ekstrakcji albo over-extraction.
- Eksport do CSV przefiltrowanych wierszy (st.download_button).
