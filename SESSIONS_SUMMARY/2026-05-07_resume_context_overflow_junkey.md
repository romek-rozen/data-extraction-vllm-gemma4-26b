# Resume + context overflow + junkey extension + DEPLOYMENT doc

**Data:** 2026-05-07 (kontynuacja `2026-05-07_v6_100pct_and_scraper.md`)
**Cel sesji:** Skalowanie do większego datasetu (~4400 URL z 5 domen), naprawa context overflow po bumpie max_tokens, mechanizm `--resume`, dokument deployment, rozszerzenie kategorii junkey.

## TL;DR

- Po pierwszym pełnym runie na 2267 URL pojawił się klasyczny overflow: bumpnęliśmy `MAX_TOKENS_STEP1` 2000→4000 dla v6, ale zostawiliśmy `MAX_ARTICLE_TOKENS=20000` → łączny prompt > 24576 max-model-len → http_400. Kalibracja przez kilka iteracji: 20k → 17k → 25k (po bumpie max-model-len do 32k) → 23k → ostatecznie **15000** (agresywne ścinanie w p99 region, dotyka ~1% artykułów).
- Bumpnęliśmy `--max-model-len` w vLLM z 24576 do **32768** (większy headroom + zapas pod Step 3).
- Dodaliśmy **`--resume`** do `run_full.py` (auto-timestamp tworzył nowy katalog → resume nie miał punktu zaczepienia). Plus `load_existing_hashes(only_ok=True)` żeby ponawiać failsy przy kolejnym `--resume`.
- Pobraliśmy 3 nowe domeny: **pomocedlaseniora.pl** (1886), **graniteks.pl**, **biznews.com.pl** (oba w trakcie). Razem `websites/` ma teraz **~4400 URL**.
- Stworzyliśmy **`DEPLOYMENT.md`** z pełnym budżetem pamięci pod RTX 5090 (32 GB). External review skorygował KV cache math (30 layers nie 62, realnie ~5 GB KV @ 12 seq) — RTX 5090 zmieści model spokojnie z `--max-num-seqs 12`.
- **`DEFAULT_CONCURRENCY`** przeniesione do `lib/config.py` jako single source of truth (zamiast hardcoded w 4 skryptach argparse).
- Rozszerzyliśmy **kategorię `junkey`** w prompt step1 v6.1: teraz obejmuje strony tagów/kategorii/taksonomii/archiwów/error pages — model sam je oznacza i zwraca `entities: []`. LLM-side filter zamiast URL pattern w `data_loader`.
- vllm_client timeout 180s → **300s** (długie artykuły + retry-with-feedback potrafią >180s na Spark Marlin fallback).

## Zmiany konfiguracji

| Parametr | Było | Jest | Powód |
|---|---|---|---|
| `--max-model-len` (vLLM) | 24576 | **32768** | bumpować pod długie prompty + Step 3 |
| `MAX_ARTICLE_TOKENS` | 20000 | **15000** | agresywne ścinanie, p99 region (~1% artykułów) |
| `MAX_TOKENS_STEP1` | 2000 | 4000 | (z poprzedniej sesji v6) |
| `MAX_TOKENS_STEP2` | 600 | 2000 | (cofnięte z testowego 600) |
| vllm_client timeout | 180s | **300s** | retry-with-feedback + długie prompty |
| `DEFAULT_CONCURRENCY` | hardcoded 8 | **lib/config.py** | single source of truth |
| junkey definition | "ads only / pusty template" | **+ taxonomy / archive / index / error pages** | filtrowanie LLM-side aggregate pages |

## Diagnoza overflow context

Pierwsze fails na pipeline run (15:31, 4153 URL):
```
http_400: maximum context length is 32768 tokens.
However, you requested 4000 output tokens and your prompt contains at least 28769 input tokens.
```

Stary komentarz w `lib/config.py` mówił że system prompt = 2929 tok, więc liczyłem `32768 - 4000 - 2929 - 37 - bufor ≈ 25000`.

Zmierzyłem tokenizerem faktycznie: **system prompt v6 = 4708 tok**, plus chat-template wrappery: `+301 tok`. Stały overhead = **5009 tok**.

Empirycznie z 469 ok runów:
```
text_tokens p50=645, p75=1671, p90=6611, p95=10048, p99=13472, max=25000
prompt_tokens p50=5654, p75=6680, p90=11618, p95=14818, p99=18456, max=22505
```

Tylko **0.6% artykułów > 15000 tok, 0.2% > 20000**. Decyzja: ścinać do 15000 — bezpieczne, nie traci jakości (p99=13472), zostawia ~8 GB safety margin pod zmiany prompta/schemy.

## `--resume` workflow

`run_full.py:`
```bash
python3 scripts/run_full.py --resume                              # najnowszy z final_results/
python3 scripts/run_full.py --resume final_results/<ts>__<tag>    # konkretny
```

`scripts/run_step1.py` i `run_step2.py` od dawna miały idempotencję po `url_hash` (`reporter.load_existing_hashes()` → skip), ale auto-timestamp w `run_full.py` tworzył nowy katalog za każdym razem → resume nie miał punktu zaczepienia. Fix:

1. `--resume [DIR]` w `run_full.py` — bez argumentu = najnowszy z `final_results/`
2. `load_existing_hashes(only_ok=True)` (default) — pomija OK records, **ponawia failsy** przy kolejnym resume

Workflow gdy coś się posypie (timeout, http_400, Ctrl+C):
1. Fix configu (np. `MAX_ARTICLE_TOKENS`, restart vLLM)
2. `python3 scripts/run_full.py --resume`
3. Pomija OK, ponawia 1-2 fails z nowym configiem, idzie dalej

## DEPLOYMENT.md

Nowy plik w roocie repo z pełną analizą:
- Konfiguracja vLLM (wszystkie flagi z uzasadnieniem)
- Budżet pamięci: wagi (16 GB NVFP4) + KV cache (FP8) + activations
- Spark vs RTX 5090: różnice hardware/software FP4
- Scenariusz "czy zmieści się na RTX 5090 32 GB?" — TAK z zapasem (po korekcie math)

External review wskazał błędy w pierwszej wersji:
- ❌ Założenie 62 layers — faktycznie **30** (25 sliding + 5 full attention)
- ❌ KV cache 8-10 GB — faktycznie **~3.4 GB @ 8 seq, ~5 GB @ 12 seq**
- ❌ Throughput 3-5× — dla MoE NVFP4 batched realnie **5-10×**

Po korekcie estymata 21M URL na RTX 5090: **6-10 dni / ~$100** (1 GPU spot RunPod).

⚠️ Plus eksplicytne ostrzeżenie: **NIE dodawać `--reasoning-parser gemma4`** — łączenie z `enable_thinking=false` cicho wyłącza xgrammar (vLLM issue #39130), a nasz pipeline wymaga `guided_json`.

## Junkey extension (prompt v6.1)

Mateusz nie filtrował krótkich artykułów ani stron taksonomii — przetwarzał wszystko. Po pobraniu domen z dużą liczbą stron tagów (webporadnik 1781 URL, pomocedlaseniora 1886 URL — wiele to `/blog/tag/...`) postanowiliśmy że strony zbiorcze BEZ oryginalnej treści też powinny lecieć do junkey.

Rozszerzona definicja:
```
- junkey: junk / non-article pages — use this category for ANY of:
  (a) ads only, no real content, empty WordPress template
  (b) taxonomy / archive / index pages — /tag/, /category/, /author/, /page/N/, search
  (c) sitemap-like pages (lista nagłówków bez narracji)
  (d) error pages (404, login, contact)
  (e) strony zdominowane (>80%) przez nawigację/sidebar/teasery
```

Plus deterministyczne sygnały dla modelu (URL pattern, brak narracji, powtarzające się fragmenty).

**Filtrowanie LLM-side > URL pattern w kodzie** — niektóre strony `/tag/abc/` MAJĄ landing page tematyczny z opisem, wtedy są wartościowe. Model decyduje per-stronie.

Trade-off: koszt 1 zapytania LLM per junkey (~3 s) zamiast skip w loaderze. Dla ~1000 stron tagów = ~50 min dodatkowych — akceptowalne.

Prompt size: 4708 → 5022 tok (+314).

## Stan websites/ (po sesji)

| Domena | URL | Pobrane przez |
|---|---|---|
| naturanatalerzu.pl | 155 | Mateusz (snapshot) |
| artystyczna.pl | 205 | scraper (poprzednia sesja) |
| folkowa.art.pl | 126 | scraper (poprzednia sesja) |
| webporadnik.pl | 1781 | scraper (poprzednia sesja) |
| pomocedlaseniora.pl | 1886 | scraper (ta sesja) |
| graniteks.pl | ~? | scraper (w trakcie) |
| biznews.com.pl | ~? | scraper (w trakcie) |
| **TOTAL** | **~4400+** | (rośnie) |

## Commity tej sesji (od `b6c6b8d`)

```
9d970ae  run_full.py: --resume flag
3c01588  fix: MAX_ARTICLE_TOKENS 20000→17000 (overflow z output=4000) + resume ponawia failsy
e9a440e  vLLM: --max-model-len 24576→32768 + MAX_ARTICLE_TOKENS 17000→25000
162d62a  DEPLOYMENT.md: konfiguracja vLLM + budżet pamięci dla DGX Spark vs RTX 5090
4d7ce2f  DEPLOYMENT.md: korekta KV cache math (30 layers nie 62) + MAX_ARTICLE_TOKENS 25000→23000
2a818c7  config: MAX_ARTICLE_TOKENS 23000→15000 (agresywne ścinanie, p99 region)
8562d48  vllm_client: timeout 180→300s
2d71c9e  config: DEFAULT_CONCURRENCY w lib/config.py — single source of truth
37c86d7  prompt step1 v6.1: rozszerzona definicja junkey o strony taksonomii / indeksów
1b1b8c3  SESSIONS_SUMMARY: ten plik (zapis stanu)
4336015  config: DEFAULT_CONCURRENCY 8→4 + dashboard logs view + .claude/ w gitignore
```

## Stan końcowy sesji

- Pipeline run aktywny w tmux `benchmark:0` — `final_results/2026-05-07_16-37-26/` na 4530 URL @ concurrency=4 (DEFAULT_CONCURRENCY zmienione na 4 dla zmniejszenia presji RAM/swap przy równoległym scraperze + vLLM).
- 2 scrapery równolegle (`benchmark:1` graniteks, `benchmark:2` biznews 8605 URL ~33%). Output bezpośrednio do `websites/`.
- vLLM stable (max_model_len 32768, GPU 93% util, 67°C, RAM 114/121 GB z swap 7 GB — głównie unified memory model+KV cache).
- Streamlit dashboard z nowym widokiem "Pipeline log" (live podgląd aktywnego runa) — odpalany w osobnym oknie tmux po zakończeniu sesji.
- Plan migracji na RTX PRO 6000 (96 GB) zapisany w `PLANS/rtx_pro_6000_optimization.md` — batch 64-80 realny, znacznie szybciej niż RTX 5090.

## Co dalej

- Pełen run na ~4400 URL z websites/ → `final_results/<ts>__v6_full_4400/`
- Po zakończeniu scrape graniteks+biznews → kolejny `--resume` zaciągnie nowe URL
- Analiza wyników: ile stron poszło do junkey (oczekiwana znaczna część stron tagów)
- Phase 6: SQLite storage layer
- Phase 7: migracja na RTX 5090 (DEPLOYMENT.md ma checklist)

## Pułapki nauczone w tej sesji

1. **Stale komentarze w configu** — komentarz mówił "system prompt 2929 tok", faktycznie było 4708. Zawsze mierz tokenizerem przed bumpaniem max_tokens.
2. **Idempotencja musi rozróżniać OK od fails** — `load_existing_hashes()` domyślnie traktuje wszystkie rekordy jako "zrobione" → fails nigdy nie są ponawiane. Fix: `only_ok=True`.
3. **Auto-timestamp + resume to dwa wymiary** — auto-timestamp robi nowy katalog, resume potrzebuje istniejącego. `--resume [DIR]` rozwiązuje konflikt.
4. **vLLM overhead chat-template jest deterministyczny** — empiryczny pomiar `prompt_tokens - text_tokens` daje stałą wartość per prompt config (5009 tok dla naszego v6). Można go bezpiecznie odjąć od max-model-len budżetu.
5. **External review opłaca się** — bez niego mielibyśmy w DEPLOYMENT.md błędną estymatę KV cache (8-10 GB zamiast 3.4 GB) → niepotrzebna defensywność na RTX 5090.

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- DEPLOYMENT.md — pełna konfiguracja + budżet pamięci dla różnych GPU
- Poprzednia sesja: [`SESSIONS_SUMMARY/2026-05-07_v6_100pct_and_scraper.md`](2026-05-07_v6_100pct_and_scraper.md)
- vLLM issue #39130 (reasoning-parser bypass): https://github.com/vllm-project/vllm/issues/39130
