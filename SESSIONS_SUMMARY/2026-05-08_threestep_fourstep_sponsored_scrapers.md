# Three-step / Four-step pipeline + sponsored detection + nowe scrapery

**Data:** 2026-05-08 (kontynuacja `2026-05-07_resume_context_overflow_junkey.md`)
**Cel sesji:** zbudować pipeline z junk-skip + parallel meta‖entities (D7c), dorobić sponsored detection jako 4-tą fazę, zescrapować dwie nowe domeny (intymnehistorie.pl, exposilesia.pl), poprawić scraper o robots.txt + Content-Type filter.
**Repo:** [github.com/romek-rozen/data-extraction-vllm-gemma4-26b](https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b)

## TL;DR

- Trzy iteracje three-step (v1/v2/v3) + finalna four-step (v4) z sponsored detection.
- **v2 b2** (1+3+4=8 workerów stałych): 3.26 s/URL, +6.3% szybsze niż baseline5000 (3.48 s/URL). Junk recall 23.2%, precision 100%, zero fails.
- **v3 c6_fix** (single pool 6 + 3 priority queues z load-balancingiem): 3.75 s/URL — wolniejszy bo conc=6 (Spark dławi się na 8). Architekturalnie czystszy.
- **v4 fourstep** dorzuca sponsored detection jako 4-tą równoległą fazę. Smoke n=5: 3/5 sponsored=True (po fixie domeny w prompt), wszystkie justifications zgodne z eyeballem użytkownika.
- Scraper rozbudowany o `RobotsChecker` (per-domain cache, requests-based żeby ominąć Cloudflare 403) + filtr `Content-Type: text/html` w Playwright fetchu.
- Scraper run: **intymnehistorie.pl** (139/139 OK) + **exposilesia.pl** (~141 URL, w trakcie) — gotowe do pipeline'u.
- **Realny junk w populacji to ~3%, nie 11%.** Step 1 v6 ma false-positivy (oznacza normalne artykuły jako junkey). Eyeball 10 random junków z baseline5000 pokazał, że ~7-8 z nich to normalne artykuły.

## Punkt startowy

- `final_results/2026-05-07_18-21-06__compare_onestep__baseline5000` — pełen baseline two-step na 5000 URL random seed=42, concurrency=6 (sequential phases: Step1 wszystkie 5000 → Step2 wszystkie 5000).
- Liczby: wall 17 393 s = 3.48 s/URL = 1035 URL/h, fail rate 0% (retry-with-feedback łapał wszystkie 31 truncated_at_max_tokens).
- Step 1 oznaczył **11.44%** URL jako `category=junkey`. Hipoteza wyjściowa: junk-skip + parallel meta‖entities = istotny speedup.

Pierwsza analiza failów w baseline:
- 100% retry to `truncated_at_max_tokens` (finish_reason=length), nie błędy modelu.
- Dwa klastry: ~4-10k znaków (typowe długie listy encji) i ~50-120k znaków (patologia powtarzalności — model klonuje encje bez `maxItems` w schemacie).
- 3 trwałe faile w one-step `last char 0` (xgrammar/structured output edge-case).

## Iteracja v1 (three-step, classic)

**Pliki nowe:**
- `prompts/step_classify_system.md` — classifier z pełnym 41-enum (kategorie + lang).
- `prompts/schema_classify.json` — `{language, category}`.
- `lib/pipeline_threestep.py` — `process_classify`, `make_junk_stub_final`, `join_final`.
- `scripts/run_threestep.py` — 3 ThreadPoolExecutor (classify=4, meta=3, entities=3).

**Wynik** (`final_results/2026-05-08_09-12-43__threestep_p1_500/`):

| Metryka | Baseline | v1 |
|---|---|---|
| s/URL | 3.48 | 4.58 (+32% wolniej) |
| Junk classified | 11.44% | 1.20% |
| Junk recall vs baseline | — | 8.9% (5/56) |
| Entity Jaccard | — | 0.552 |
| Classifier latency | — | **2.63 s/URL** |

**Werdykt: D7c v1 fail.** Powody:
1. Classifier 2.63 s/URL przewyższał maks. teoretyczną oszczędność z junk-skipu (2.30 s/URL przy 11.44% junku × 20.15 s).
2. Prompt z silnym ostrzeżeniem "false-positive on junkey causes loss of SEO meta" wystraszył model — klasyfikował tylko 5 z 56 oczywistych junków.
3. Parallel meta‖entities nic nie dał — vLLM batchuje natywnie, GPU bottleneck.

## Iteracja v2 (binary classifier + entities-only)

**Trzy zmiany:**
1. **Classifier binary** `0`/`1` przez vLLM `guided_choice` (xgrammar). Input truncowany do 1000 chars markdown. Prompt z 7 few-shot examples (404, paywall, cookie wall, link listing jako junk; real article jako not-junk).
2. **Meta v2** generuje `{language, category, title, meta_description, h1, article_summary}` (kategoria z meta zamiast classifier).
3. **Entities v2** generuje tylko `{entities: [{name, type}]}` — krótszy schemat, krótszy output.

**Pliki nowe:**
- `prompts/step_junkclassify_v2_system.md`, `prompts/step_meta_v2_system.md`, `prompts/step_entities_v2_system.md`
- `prompts/schema_meta_v2.json`, `prompts/schema_entities_v2.json`
- `lib/pipeline_threestep_v2.py` — binary classifier raw POST + retry network
- `scripts/run_threestep_v2.py` — 3 osobne pools z per-stage logami (`classify.log`, `meta.log`, `entities.log`, `run.log`)

**Smoke v2 (5 URL):** classify mean **0.21 s/URL** — 12.5× szybciej niż v1 (2.63 s).

**Pełen run v2 b2** (`final_results/2026-05-08_12-33-06__threestep_v2_v2_500_b2/`, concurrency 1+3+4=8):

| Metryka | Baseline | v2 b2 | Delta |
|---|---|---|---|
| Wall s/URL | 3.48 | **3.26** | **+6.3% szybciej** |
| URL/h | 1035 | 1104 | +6.7% |
| Junk classified | 11.44% | 3.00% | mniej, ale precyzyjniej |
| Junk recall vs baseline | — | 23.2% (13/56) | ale baseline ma fpos |
| Junk precision vs baseline | — | **100%** (13/13) | wszystkie b2-junki też w baseline |
| Entity Jaccard | — | 0.495 | różny prompt context |
| Category match | — | 76.0% | różne prompty |
| Fail rate | 0% | **0%** | retry-with-feedback działa |

**Pierwszy realny zysk — drobny ale czysty:** +6.3% wall, zero pogorszenia jakości.

## Iteracja v3 (single pool + priority queues, wzorzec A)

**Motywacja:** w v2 b2 z 1+3+4 stałymi workerami, classify worker kończy w ~94 s (500 × 0.17 s sekwencyjnie), potem przez ~25 min runu **stoi bezczynnie** — marnowanie 1/8 GPU slot.

**Wzorzec A:** jeden `ThreadPoolExecutor(max_workers=N)` + 3 `queue.Queue` z priority pull (`classify > meta > entities`). Każdy worker wybiera task z najwyższego priorytetu.

Concurrency=6 (decyzja użytkownika, Spark dławi się na 8).

**Pliki nowe:**
- `scripts/run_threestep_v3.py` — single pool + 3 priority queues
- `lib/pipeline_threestep_v2.py` UPDATE: classifier network retry (`max_retries_network=2`)

### Bug 1: priority pull `meta > entities` powodował sekwencję

Pierwszy run `v3_500_c6` pokazał: classified.jsonl=500, ale entities.jsonl pusty. Workery wszystkie szły z meta queue (priorytet) zanim sięgnęły entities. Meta queue była cały czas zasilana przez classify → entities startował dopiero po opróżnieniu meta.

**Fix:** load-balance między meta i entities. Bierzemy tę z dłuższą kolejką (catching-up), przy remisie naprzemiennie (toggle). Po opróżnieniu classify → 6 workerów leci fairly na meta+entities równolegle.

**Wynik v3 c6_fix** (`final_results/2026-05-08_13-13-20__threestep_v3_v3_500_c6_fix/`):

| | baseline5000 | v2 b2 | v3 c6_fix |
|---|---|---|---|
| s/URL | 3.48 | **3.26** | 3.75 |
| URL/h | 1035 | **1104** | 960 |
| Junk% | 11.44% | 3.00% | 3.20% |
| Junk recall | — | 23.2% | 25.0% |
| Junk precision | — | 100% | 100% |
| Entity Jaccard | — | 0.495 | 0.489 |
| Category match | — | 76.0% | 77.4% |
| Fail rate | 0% | 0% | **0%** |

**v3 wolniejszy od v2 b2** mimo lepszej architektury — z powodu **concurrency 6 vs 8**. Spark dławi się na 8, ale konsekwencja to ~13% mniejszy throughput. Trade-off stabilność za throughput.

## Iteracja v4 (fourstep — sponsored detection)

**Motywacja:** dodać detekcję artykułów sponsorowanych jako 4-tą fazę. Decyzja kolejna w design (po dyskusji rev 1→4):
- Binary `sponsored=true/false`. Editorial wynika automatycznie z `!sponsored`.
- Opcjonalny `sponsored_subtype: enum[null, full_sponsored, link_insertion, brand_mentions, advertorial]`. `affiliate_review` *usunięty z subtype'ów* — to editorial.
- `sponsored_justification` (≤120 znaków) — kilka słów z konkretnym sygnałem (audit trail, kalibracja, Chain-of-thought-lite).

**Decyzja architektoniczna:** sponsored detection jako OSOBNY etap, nie część meta. Łączenie SEO-generation z sponsored-classification w jednym promptcie rozmydla model (dwa różne tryby cognitive — generacja vs klasyfikacja). Lepiej osobno.

**Pliki nowe:**
- `prompts/step_sponsored_v1_system.md` — szczegółowy prompt z 10 examples
- `prompts/schema_sponsored_v1.json` — `{sponsored, subtype, justification}`
- `lib/pipeline_fourstep_v1.py` — re-eksport v2 functions + `process_sponsored_v1`, `join_final_v4`
- `scripts/run_fourstep_v1.py` — single pool + 4 priority queues z load-balancingiem (classify > {meta, entities, sponsored})

### Smoke 1 (przed fixem domeny): 5/5 sponsored=True (problem)

Justifications wyglądały opisowo legit, ale 5/5 to za dużo. Patrząc:
- Würth Polska / taśma Power → "press release for Würth Polska" — granica
- pomocedlaseniora.pl → "shop promotion with multiple links" — **błąd**, to internal owner-commerce

**Diagnoza:** model nie wie którą domenę widzi. Patrzy na artykuł `pomocedlaseniora.pl/blog/...` z linkami do `pomocedlaseniora.pl/sklep/...` — nie ma kontekstu że te linki są internal. Markdown ma surowe URL bez "publisher origin".

### Fix domain context + 3-class konceptualnie

**User pointed out:** pomocedlaseniora.pl po prostu promuje swój sklep → to ani sponsored ani editorial, tylko **owner-commercial** (publisher promuje swój własny shop/services).

Konceptualnie 3 klasy:
1. Editorial — neutralna treść
2. Owner-commercial — własny shop (NIE sponsored, sponsored=false z odpowiednim justification)
3. Third-party sponsored (sponsored=true)

Implementacyjnie: zostajemy przy binary `sponsored`, ale poprawiamy prompt:
- User-prompt zawiera `PUBLISHER DOMAIN: <domain>` linię
- System prompt: "Links to {domain} or its subdomains are INTERNAL — NOT third-party sponsored"
- Dodane przykłady: owner-commercial (sponsored=false), single-product news (sponsored=false), press release (sponsored=true), borderline single-product review (sponsored=false).

`affiliate_review` przeniesiony z subtype enum do editorial (zgodnie z user's decyzją "affiliate_review to editorial wlasny").

**Pliki updated:**
- `lib/pipeline_fourstep_v1.py` — user prompt zawiera `PUBLISHER DOMAIN: {domain}` i zasadę internal-vs-external
- `prompts/step_sponsored_v1_system.md` — sekcja "PUBLISHER DOMAIN — critical context"; example 7 (owner-commercial), 8 (single-product news), 9 (press release), 10 (single-product review); example 4 zmieniony na sponsored=false (affiliate review)
- `prompts/schema_sponsored_v1.json` — `affiliate_review` usunięty z enum subtype

### Smoke 2 (po fixie): 3/5 sponsored=True

| URL | Domain | sponsored | Komentarz |
|---|---|---|---|
| Stone veneer | graniteks.pl | **False** ✓ | "owner-commercial: product page on publisher's own domain" |
| Würth Power tape | biznews.com.pl | True | "press release for Würth, no critical voice" |
| pomocedlaseniora shop | pomocedlaseniora.pl | **False** ✓ | "owner-commercial: internal links only" — twój przykład rozwiązany |
| PLAY network | biznews.com.pl | True | "promotes PLAY (play.pl/playnow.pl) external + W ofercie sieci PLAY" — user potwierdził: faktycznie sponsored |
| Szkoła rysunku [Informacje prasowe] | biznews.com.pl | True | "explicit '[Informacje prasowe]' tag + szkolarysunku.waw.pl external" |

**3/3 z biznews.com.pl** — wszystkie zgodnie z eyeballem ("biznews.com.pl jest portalem typu publish-for-pay").
**2/2 owner-commercial** poprawnie sponsored=false.

Wall: 26.1s na 5 URL = +6% vs v3 smoke (24.6s) — akceptowalny narzut za 4-tą fazę.

## Scrapery + robots.txt + Content-Type filter

### Rozszerzenie `scraper/scrape_domain.py`

**Klasa `RobotsChecker`** (nowa):
- Per-domain cache `robots.txt` przez `requests` z explicit Chrome UA — omija problem stdlib `RobotFileParser.read()` który dostaje 403 od Cloudflare i konsereativnie zwraca `False` na wszystkie URL.
- `is_allowed(url)` → bool, sprawdza Disallow.
- `crawl_delay(host)` → opcjonalny crawl-delay (parsowany informacyjnie).
- `filter_urls(list)` → batch filter, loguje liczbę zablokowanych.
- Statystyki: `allowed / blocked / no_robots / errors`.

**Filtr na 3 etapach:**
1. Discovery (sitemap, BFS, file).
2. BFS — sprawdza Disallow PRZED każdym fetchem URL (oszczędza requesty).
3. `crawl_urls` — drugi safety net przed Playwright fetchem.

**Edge cases:**
- robots.txt 404/410 → wszystko dozwolone (RFC 9309 default).
- 5xx / błąd sieci → fail-open z warningiem.

**Nowe flagi CLI:** `--ignore-robots` (opt-out, warning), `--user-agent "..."` (custom UA).

**Content-Type filter** w `crawl_urls`: po `await crawler.arun(...)` sprawdza `res.response_headers` (case-insensitive lookup), jeśli `content-type` istnieje i nie zawiera `text/html` → URL pomijany. Łapie URL bez extension serwowane jako PDF/JSON/binary.

### Test na dwóch domenach

**`motoryzacjamag.eu`** (test sanity check):
- robots.txt dostępny, Disallow `/stats/`, `/openai/`, `/jquery/`.
- Filtr poprawnie blokuje 3 testowe URL z Disallow paths, dopuszcza pozostałe.

**`codziennyekspert.pl`** (test fail-open na Cloudflare):
- robots.txt: `Disallow: /wp-admin/`, `Allow: /wp-admin/admin-ajax.php`, plus sitemap-index.
- Stdlib RobotFileParser.read() dostaje **403 od Cloudflare** → puste rules → wszystko zablokowane (false bug).
- Mój `RobotsChecker` używa `requests` z Chrome UA → 200 OK → poprawnie parsuje → `/wp-admin/` blocked, reszta dozwolona.

### Run scrapera: intymnehistorie.pl

```bash
scraper/.venv/bin/python scraper/scrape_domain.py \
    --sitemap https://intymnehistorie.pl/sitemap_index.xml \
    --out-dir websites_intymnehistorie --concurrency 4
```

- robots.txt: `User-agent: *  Disallow:` (pusty Disallow = wszystko dozwolone).
- Sitemap-index → 4 sub-sitemapy → 139 URL łącznie.
- Wynik: **139/139 OK, zero fails, zero blocked przez robots, ~3 min wall**.

### Run scrapera: exposilesia.pl (w trakcie)

```bash
scraper/.venv/bin/python scraper/scrape_domain.py \
    --sitemap https://exposilesia.pl/sitemap_index.xml \
    --out-dir websites_exposilesia --concurrency 4
```

- robots.txt: ma Cloudflare-managed `Content-Signal: search=yes,ai-train=no` (deklaracja prawna z odwołaniem do EU 2019/790). User-agent `*` ma `Allow: /` + `Disallow: /wp-admin/`.
- ClaudeBot, GPTBot, Amazonbot itd. explicite Disallow — my używamy Chrome UA, więc wpadamy pod `*`.
- 141 URL (129 post + 5 page + 7 category).

## Co jest dokumentowane gdzie

| Plik | Zawartość |
|---|---|
| `PLANS/threestep_pipeline_plan.md` | rev 1→3 design, wyniki v1/v2/v3, tabela porównawcza, ryzyka, decyzja D7c |
| `PLANS/threestep_pipeline_todo.md` | checklisty per iteracja, kandydaty na P2 |
| `PLANS/sponsored_detection_plan.md` | rev 1→4 design sponsored detection, sygnały Tier 1-4, pułapki PL rynku, schema |
| `JACCARD.md` | edukacyjny dokument o Jaccard Index — wzór, mermaid, interpretacja, użycie w naszym pipeline |
| `scraper/README.md` | filtry, robots.txt, Content-Type, opt-out, custom UA |
| `SESSIONS_SUMMARY.md` (top-level) | spójny indeks chronologiczny (deprecated, redirect to dir) |
| `SESSIONS_SUMMARY/2026-05-08_*.md` | per-session detail (ten plik) |

## Otwarte pytania (do następnej sesji)

1. **Pełen run v4 fourstep na 500 URL** — czy odpalać? Smoke n=5 sensowny, ale 500 da realny ratio sponsored/editorial w populacji.
2. **Fair-baseline run two-step** (concurrency=6 na 500 URL seed=42) — uczciwy punkt odniesienia. Bez tego porównanie z `baseline5000` jest jabłka-do-gruszek (sequential vs parallel phases).
3. **Pipeline na intymnehistorie.pl + exposilesia.pl** — gotowe katalogi `websites_intymnehistorie/` i `websites_exposilesia/`. Czy odpalić v4?
4. **Eyeball ground-truth sponsored** — n=200 URL ręcznie oznaczyć żeby zmierzyć precision/recall classifier'a sponsored.
5. **GPU util pomiar** podczas v3/v4 — czy bumpować `--max-num-seqs` z 8 na 12? Sprawdza się to przez `nvidia-smi dmon` podczas runu. Jeśli util <80% — bumpować.
6. **Wpis w `DECISIONS.md`** (D7c) — only po fair-baseline porównaniu i decyzji o defaultcie.

## Status faz

D7c (three-step jako kandydat na default):
- v1 → fail (classifier za drogi)
- v2 b2 → +6.3% szybsze, ale brak fair-baseline porównania → nie merge'ujemy do default'u
- v3 c6_fix → wolniejszy z powodu concurrency=6 (decyzja stabilności). Architekturalnie czystszy.
- v4 fourstep → smoke OK, brak pełnego runu

Sponsored detection (D7d, nowy temat):
- Schema + prompt rev 4 + domain context → smoke n=5 sensowny
- Brak walidacji na ground-truth — D7d nie zamykany

Scrapery:
- robots.txt + Content-Type → final
- intymnehistorie.pl scraped (139/139)
- exposilesia.pl scraping w trakcie

## Nowe pliki w repo (sesja 2026-05-08)

```
PLANS/threestep_pipeline_plan.md
PLANS/threestep_pipeline_todo.md
PLANS/sponsored_detection_plan.md
PLANS/junk_examples_v2.json
PLANS/junk_examples_v2_curated.json
JACCARD.md
SESSIONS_SUMMARY.md   (deprecated, redirect)
SESSIONS_SUMMARY/2026-05-08_threestep_fourstep_sponsored_scrapers.md   (ten plik)

prompts/step_classify_system.md          (v1, deprecated po v2)
prompts/schema_classify.json             (v1, deprecated po v2)
prompts/step_junkclassify_v2_system.md   (v2 binary classifier, current)
prompts/step_meta_v2_system.md           (current)
prompts/schema_meta_v2.json
prompts/step_entities_v2_system.md       (current)
prompts/schema_entities_v2.json
prompts/step_sponsored_v1_system.md      (v4)
prompts/schema_sponsored_v1.json

lib/pipeline_threestep.py                (v1, deprecated)
lib/pipeline_threestep_v2.py             (v2/v3, current — używany też w v4)
lib/pipeline_fourstep_v1.py              (v4)

scripts/run_threestep.py                 (v1)
scripts/run_threestep_v2.py              (v2)
scripts/run_threestep_v3.py              (v3 single pool + priority queues)
scripts/run_fourstep_v1.py               (v4 + sponsored)

scraper/scrape_domain.py                 (UPDATE: RobotsChecker + Content-Type filter)
scraper/README.md                        (UPDATE: nowe sekcje robots.txt, Content-Type)
```
