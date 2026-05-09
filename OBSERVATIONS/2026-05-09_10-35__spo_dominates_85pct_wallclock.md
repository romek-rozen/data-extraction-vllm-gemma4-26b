# Obserwacja: SPO triples dominują 85% wallclock — kandydat do usunięcia

**Timestamp:** 2026-05-09 10:35:33 CEST
**Źródło danych:** `final_results/2026-05-09_00-21-48__spo_v1_mns32_full/` (10,12 h runa, 6 779 ok artykułów)
**Konfiguracja vLLM:** mns=32, max-model-len=24576, max-num-batched-tokens=16384, chunked-prefill ON, fp8 KV, marlin MoE

## Rozkład czasu obliczeń per stage

| Stage | n | mean lat | p99 lat | total req-h | % wallclock |
|---|---|---|---|---|---|
| classified (junk filter) | 7 774 | 0,40 s | 1,00 s | 0,86 h | **0,3%** |
| sponsored | 6 787 | 4,25 s | 9,60 s | 8,01 h | **2,5%** |
| meta | 6 785 | 20,14 s | 30,14 s | 37,97 h | **11,8%** |
| **entities+SPO** | 6 759 | **146 s** | **258 s** | **274,4 h** | **85,4%** |

Effective concurrency: 31,7× / 32 max = **99% nasycenia vLLM**. Brak idle slots — pipeline pakowany perfekcyjnie.

## Co składa się na entities+SPO output

Per artykuł (n=6 779, JSON output):

| Komponent | Średnio | Tokens (estymata) |
|---|---|---|
| entities (mean=12,4, p95=22) | 12 entities | ~300-400 tok |
| central_entities (mean=2,3, p95=5) | 2-3 entities | ~30 tok |
| primary_topic | 1 string | ~10 tok |
| **triples (mean=9,3, p95=14)** | **9-14 trójek** | **~750 tok** ⬅️ dominanta |
| evidence_span (mean=63 chars × 9,3 triples) | ~590 chars/article | ~150 tok |
| JSON overhead | – | ~50 tok |

**Łącznie 6 779 artykułów = 63 159 trójek + 4 mln znaków evidence (~1 mln tokenów dedykowanych samym evidence_span).**

## Estymata zysku z usunięcia triples

Output decode-bound (memory bandwidth), więc redukcja output ≈ liniowa redukcja latency tej fazy.

```
entities+SPO mean obecnie:    146 s (output ~1500 tok)
entities only po usunięciu:   ~75 s (output ~750 tok, -50%)

GPU time entities+SPO step:
  146s × 25668 URL ÷ 32 conc = ~33 h
po cięciu triples:
  75s × 25668 ÷ 32 = ~17 h
oszczędność: ~16 h na samym entities step

Wallclock całego v1 (gdzie entities+SPO to 85,4%):
  obecnie: 38h ETA
  po cięciu: ~24h ETA  (-35-37%)

v1 + v2 sequential:
  obecnie: ~77h
  po cięciu: ~48h  (oszczędność ~29h, prawie 2 dni)
```

## Sygnały że SPO można usunąć bez utraty wartości produktowej

1. **Specyfikacja `CLAUDE.md`** definiuje Step 1 jako: "ekstrakcja encji + wykrycie języka + kategoria". **Brak SPO w oryginalnym Step 1.**
2. **Triples zostały dodane później** jako rozszerzenie (v1/v2 architektura, D18 w `DECISIONS.md` jako "alternatywna architektura").
3. **SEO meta produkcyjne** (title, meta_description, h1, article_summary) generuje stage `meta` — nie używa `triples[]`.
4. **Konsumenci `triples[]` w repo** to głównie:
   - `dashboard/views/*` (analiza wyników, nie produkcja)
   - `lib/spo_pipeline_v{1,2,3}.py` (testowane warianty pipeline'u, samego siebie)
   - Brak komponentu produkcyjnego SEO który **wymaga** triples.

## Lewary alternatywne (gdyby SPO zostało)

| Lever | Realny zysk | Ryzyko |
|---|---|---|
| Truncate `n_triples` cap=8 (z mean=9,3, p95=14) | ~12-18% | Niski — trójki 9-14 prawdopodobnie mają niższe confidence |
| Usunąć `evidence_span` ze schematu | ~10-15% (sama evidence to ~150 tok/article) | Niski — evidence rzadko konsumowane |
| Skrócenie input markdown do 4k tok | ~10-15% (krótszy prefill) | Średni — sygnał z dolnej części artykułu? |
| **Usunięcie całego SPO bloku** | **~30-37%** | Niski jeśli triples nie są w produkcji SEO |
| mns=48 (po runie) | ~3-5% | Niski |
| Drugi vLLM dla meta+sponsored | max 14% (cały meta+sponsored) | Wysoki — refactor + jakość PL |

## Rekomendacja

**Najwyższy ROI:** A/B test "entities-only" vs "entities+SPO" na 200 URL z bieżącego cache.
- Modyfikacja `prompts/schema_step1.json` (usunąć `triples`, `primary_topic` opcjonalnie zostawić)
- Modyfikacja `lib/pipeline.py` / `lib/spo_pipeline_v1.py` (parser bez triples)
- Pomiar: wall_s / req, jakość encji (Jaccard vs full-SPO baseline)
- Decision threshold: jeśli speedup ≥1,5× wall + entity Jaccard ≥0,9 → migracja produkcyjna

**Decyzja architektoniczna powinna trafić do `DECISIONS.md`** jako D29 z dowodami z A/B (przed pełnym 21M URL run prod).

## Kontekst — bieżący run

- Start: 2026-05-09 00:21:48
- Aktualna ETA: ~38h v1 + ~38h v2 = 77h total (koniec ~12 maja 04:00)
- Throughput: 663 entities_spo req/h (mns=32)
- Po cięciu SPO: ETA ~48h (koniec ~11 maja 00:30, przyspieszenie 29h)

## Źródła decyzyjne (powiązane)

- `CLAUDE.md` — spec Step 1 nie zawiera SPO
- `DECISIONS.md` D18 — wprowadzenie SPO jako "alternatywnej architektury"
- `INSTRUCTIONS_FROM_CLAUDE.md` — źródło prawdy dla pipeline'u; sprawdzić czy SPO jest tam wymagane czy opcjonalne
- Bieżący run `final_results/2026-05-09_00-21-48__spo_v1_mns32_full/` — twarde dane
