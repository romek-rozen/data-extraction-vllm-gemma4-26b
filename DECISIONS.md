# DECISIONS.md

Log kluczowych decyzji technicznych projektu. Każdy wpis: **co, dlaczego, kiedy, na czym oparte**. Spec referencyjna: `INSTRUCTIONS_FROM_CLAUDE.md`.

---

## 2026-05-07 — Phase 0 + Phase 1 zamknięte

### D1: Model finalny — `nvidia/Gemma-4-26B-A4B-NVFP4`
- **Co:** Gemma 4 26B A4B w kwantyzacji NVFP4 (MoE 25,2B total / 3,8B active).
- **Wariant na Spark (sm_121):** `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` (zawiera patch `gemma4_patched.py` dla sm_121 → vLLM `gemma4-cu130` image).
- **Wariant prod (RTX 5090, sm_120):** `nvidia/Gemma-4-26B-A4B-NVFP4` (natywne FP4).
- **Dlaczego:** MMMLU 86,3%, native PL + 140 języków, Apache 2.0, MoE → szybkość 4B przy jakości 26B.
- **Oparte na:** `INSTRUCTIONS_FROM_CLAUDE.md` sekcja "Model: Gemma 4 26B A4B (MoE)".

### D2: Stack inference — vLLM + xgrammar + Marlin
- **Co:** vLLM `vllm/vllm-openai:gemma4-cu130`, `--quantization modelopt`, `--kv-cache-dtype fp8`, `--moe-backend marlin`, `--enable-prefix-caching`.
- **Dlaczego Marlin:** Spark sm_121 nie ma natywnego FP4 — Marlin to ~30% wolniejszy fallback, ale działa. Na 5090 zostanie zdjęte (natywne FP4).
- **Dlaczego FP8 KV cache:** 2× większy batch względem BF16 bez znaczącej utraty jakości.
- **Dlaczego prefix caching:** system prompt Step 1 ma 2 929 tokenów — bez cachingu liczyłby się 21M razy.

### D3: Thinking OFF + brak `--reasoning-parser gemma4`
- **Co:** `--default-chat-template-kwargs '{"enable_thinking": false}'` na serwerze, plus per-request `chat_template_kwargs: {enable_thinking: false}` w body. **NIE** używamy `--reasoning-parser gemma4`.
- **Dlaczego:** Gemma 4 ma natywny chain-of-thought reasoning, niepotrzebny dla strukturalnej ekstrakcji. Smoke test (Phase 0) potwierdził `reasoning: null` w odpowiedziach.
- **Dlaczego bez `--reasoning-parser`:** kombinacja `--reasoning-parser gemma4` + `enable_thinking=false` cicho **wyłącza xgrammar** (vLLM issue #39130). My potrzebujemy `guided_json` dla Step 1/2 → reasoning-parser nie wchodzi w grę.
- **Konsekwencja dla prod:** wycofujemy sugestię `--reasoning-parser gemma4` z `INSTRUCTIONS_FROM_CLAUDE.md` Phase 8 (RTX 5090).

### D4: `--max-model-len 24576`
- **Co:** Twardy limit kontekstu per request — 24 576 tokenów.
- **Dlaczego:** Phase 1 pomiar pokazał:
  - Step 1 max input: 8 925 tokenów (system 2 929 + user 17 + max artykuł 5 979) + output 400 = **9 325 tokenów**.
  - Headroom: 62% (15 251 tokenów rezerwy).
- **Alternatywy odrzucone:**
  - 16 384 — za ciasno przy outlierach (niepewność co przy 21M URL).
  - 32 768 — niepotrzebne, marnuje VRAM na KV cache.
- **Status:** może zostać obniżone do 12 288 w Phase 5 dla 2× większego batcha — decyzja po pomiarze przepustowości.

### D5: HTML cleanup MANDATORY przez trafilatura markdown
- **Co:** `trafilatura.extract(..., output_format="markdown", include_links=True, include_formatting=True, include_comments=False, include_tables=True)`.
- **Pomiar (Phase 1, 100 URL):**
  - HTML → markdown: **98,45% redukcja** znaków (median 273k → 4 225).
  - Markdown vs plain text: **+2,86% mediana** tokenów (max +109% — outlier z gęstymi linkami).
  - Median: 1 247 tokenów, p95: 4 856, max: 5 979.
- **Dlaczego markdown a nie plain:** `# nagłówki`, `[anchor](url)`, `**bold**` to bezpośrednie sygnały dla `entities[]`, `title`, `h1`, `article_summary`. Koszt 2,86% jest minimalny.
- **Dlaczego `include_tables=True`:** tabele to wartościowy sygnał dla artykułów porównawczych/specyfikacji. Koszt p95: +2,5% tokenów (4 735 → 4 856).
- **Pułapka:** outlier 109% to artykuł z dużą liczbą linków — do walidacji w Phase 2 czy linki rzeczywiście pomagają w `entities[]`.

### D6: Sampling — Google defaults (1.0 / 0.95 / 64)
- **Co:** `temperature=1.0, top_p=0.95, top_k=64, repetition_penalty=1.0` dla obu Step 1 i Step 2.
- **Dlaczego:** Gemma 4 jest kalibrowana przez Google na te wartości. Niższe temperatury bez empirycznego dowodu degradują jakość. `guided_json` chroni przed halucynacją struktury — niska temperatura nie jest potrzebna do "bezpieczeństwa".
- **Dlaczego `repetition_penalty=1.0` a nie 1.2:** 1.2 łamie powtarzające się klucze JSON (np. `"name"`, `"type"` × 15 encji).
- **A/B test Phase 3:** porównanie z 0.7/0.3 (Step 1) i 0.8/0.5 (Step 2). Domyślnie zostajemy przy A.

### D7: Two-step pipeline (entity extraction + SEO meta) — bez baseline one-step
- **Co:** Step 1 (universal English prompts, language detection, entities + category) → pipe note → Step 2 (language-aware SEO meta).
- **Dlaczego:** dwa zadania o fundamentalnie różnym charakterze — Step 1 deterministyczna ekstrakcja, Step 2 kreatywna generacja. Każdy może mieć osobny optymalny config (Phase 3).
- **Wartość pipe note:** uniwersalna warstwa encji wielokrotnego użytku (knowledge graph, search, multilingual expansion).
- **Walidacja (Phase 2):** smoke 3 URL pokazał wysoką jakość outputu (sensowne kategorie, idiomatyczne polskie meta, encje z Step 1 wykorzystane w Step 2). Decyzja: **pomijamy one-step baseline** — koszt implementacji + runa nie uzasadniony. Pełen run two-step na 100 URL jako finalne potwierdzenie skali. Jeśli pojawią się problemy jakościowe w Phase 4, wracamy do baseline.
- **Revisit (Phase 5b, 2026-05-07):** dorzucamy realną ścieżkę one-step (`prompts/step_onestep_system.md`, `prompts/schema_onestep.json`, `lib/pipeline_onestep.py`, `scripts/run_onestep.py`) + harness `scripts/compare_onestep_vs_twostep.py` (oba pipeline'y na tym samym `--random --seed` sample, raport speed+quality, widok dashboard `compare_onestep`). Two-step zostaje produkcyjnym defaultem dopóki one-step nie spełni: speedup wall ≥1.5× ∧ category match ≥90% ∧ Jaccard encji ≥0.5 ∧ fail rate one-step ≤ two-step. Po runie 20→100 URL: liczby + decyzja w osobnym wpisie (D7b).
- **Status:** final do czasu kontr-dowodu z Phase 5b; walidacja przez 100 URL run two-step zaliczona.

### D8: Tokenizer lokalny (Rust `tokenizers`) zamiast vLLM `/tokenize`
- **Co:** `lib/tokenizer.py` używa `tokenizers.Tokenizer.from_file(tokenizer.json)` bezpośrednio z katalogu modelu.
- **Dlaczego nie HF `transformers.AutoTokenizer`:** `tokenizer_config.json` w `bg-digitalservices` quancie ma bug (`'list' object has no attribute 'keys'`).
- **Dlaczego nie vLLM HTTP `/tokenize`:** ~50 ms per request → dla 21M URL = ~12 dni samej tokenizacji.
- **Wynik:** ~2 ms per artykuł, ~5 godzin dla 21M URL.
- **Konsekwencja:** truncate na bazie tokenów (`MAX_ARTICLE_TOKENS=20000`) jest tani enough by włączyć go w loaderze.

### D9: Truncate dwustopniowy (znaki + tokeny)
- **Co:** Najpierw `TEXT_TRUNCATE_LIMIT=80000` znaków (szybki cap dla outlierów), potem dokładny `truncate_to_tokens(MAX_ARTICLE_TOKENS=20000)`.
- **Dlaczego dwustopniowo:** znakowy cap to safety net przed kosztem tokenizacji patologicznych stron (np. 1MB HTML po cleanup). Tokenowy cap to faktyczny budżet względem `max_model_len`.
- **Status:** w 100-URL próbce żaden artykuł nie wymagał truncate. Zostawiamy "na wszelki wypadek" dla 21M URL.

### D10: URL info z `json.gz` — tylko url + domain + path
- **Co:** Loader wyciąga `url_finish` (URL po redirectach), z niego `domain` (netloc) i `path`.
- **Dlaczego nie `headers[]` z JSON:** czasem strony mają błędnie porobione headingi w HTML — black box, nie ufamy.
- **Konsekwencja:** model wszystko ekstrahuje sam z markdownu. Model jest groundtruth, nie metadata strony.

### D11.5: Najpierw obserwuj model, potem ograniczaj — reguła "no premature constraints"
- **Co:** w pierwszych runach (Phase 2 walidacja) wszystkie parametry, które mogą ciąć/ograniczać output modelu, ustawiamy **hojnie lub na off**. Mierzymy realne zachowanie. **Dopiero potem** decydujemy o ograniczeniach na podstawie danych.
- **Konkretnie odnotowane:**
  - `MAX_TOKENS_STEP1 = 2000`, `MAX_TOKENS_STEP2 = 2000` (start; po pomiarze ewentualnie tnij).
  - `entities`: zdjęte `minItems: 0` i `maxItems: 15` ze schemy. Niech model sam zdecyduje ile encji wyciągnie.
  - `name.maxLength: 100` zostaje (cap na zewnętrzny outlier, nie ograniczenie liczby).
  - System prompt nadal sugeruje "Maximum 15 most important entities" — to **soft hint** dla modelu, nie hard constraint. Model może go zignorować jeśli artykuł wymaga inaczej.
- **Dlaczego:**
  - Szacunki tokenów per pole są nieprecyzyjne (PL ma więcej tokenów per znak niż EN, struktura JSON dorzuca 50+).
  - Pierwszy run na 400/300 max_tokens uciął odpowiedzi przy `finish_reason: length` — model nie zwraca błędu HTTP, wykrywamy dopiero po `json.JSONDecodeError`.
  - `maxItems: 15` mogło dla niektórych artykułów obciąć wartościowe encje. Bez pomiaru "ile model normalnie wyciąga" nie wiemy czy 15 to dobry cap.
- **Reguła ogólna:** każdy parametr który ma efekt twardego ograniczenia (`max_tokens`, `max_model_len`, `maxItems`, `maxLength`, truncation) — zaczynamy od ustawienia które **NIE jest aktywne** dla typowego runa. Mierzymy. Tnijemy tylko tam, gdzie dane uzasadniają.
- **Status:** final reguła. **Faktyczne wartości z Phase 2 (100 URL, 100/100 OK):**
  - **Step 1 output:** median 301 tok, p95 435, max **763**. → `MAX_TOKENS_STEP1=2000` znacząco zapasowe; bezpieczne `1000` (max+30%).
  - **Step 2 output:** median 189 tok, p95 215, max **224**. → `MAX_TOKENS_STEP2=2000` mocno przesadzone; bezpieczne `400` (max+80%).
  - **Liczba encji:** median **15**, p95 24, max **33**. Cap `maxItems: 15` ze schemy zdjęty słusznie — model normalnie wyciąga > 15.
  - **Długości pól (znaki):** title median 60 (limit 70), meta 156 (target 140-160), h1 58 (limit 100), summary 261 (limit 400). Model trzyma się limitów naturalnie.
  - **Throughput:** 172 s total / 100 URL @ concurrency 4 = **1,73 s/req amortized** (Step 1 sequential 9,7 s + Step 2 sequential 6,9 s).
  - **Prefix cache hit rate:** 72,2% (1,49M tokenów queries, 1,08M hits) — bardzo blisko teoretycznego maks dla naszej dystrybucji.

### D11.7: Diagnostyka prefix cache przez `/metrics`, nie przez response
- **Problem:** vLLM build `gemma4-cu130` (0.19.1.dev6) zwraca `prompt_tokens_details: null` w `usage` response, niezależnie od cache hit. Per-request `cached_tokens` niedostępny.
- **Workaround:** Prometheus endpoint `http://localhost:8001/metrics` zawsze raportuje agregowany `vllm:prefix_cache_queries_total` i `vllm:prefix_cache_hits_total`.
- **Skrypt:** `scripts/snapshot_metrics.py before/after` + `diff` — liczy delta dla per-run hit rate.
- **Stan na 2026-05-07 (Phase 2 + smoke testy):** hit rate **76,1%** (1 020 544 / 1 341 177 tokenów). System prompt Step 1 (2 929 tokenów) skutecznie cachowany.
- **Status:** monitoring działa. Jeśli wgramy nowszy vLLM kiedyś, per-request `cached_tokens` może wrócić — ale `/metrics` zawsze pozostanie source-of-truth dla agregatów.

### D12: Sampling — Step 1 zostaje 1.0, Step 2 obniżamy do 0.8 (Phase 3 walidacja)
- **Step 1 (entity extraction):** zostaje **A=(1.0, 0.95, 64)** Google default. Empiryczny test 100 URL × 3 configi (A=1.0, B=0.7, C=0.3): wszystkie 100/100 OK; encje median 14-15 (różnica trywialna); unikalne nazwy encji 784/793/787 (różnica <1,2%); 94/100 URL dostaje tę samą kategorię w wszystkich configach. Schema enforcement (xgrammar) dominuje nad temperaturą — token-level constraint wycina 99% przestrzeni decyzyjnej.
- **Step 2 (SEO meta):** zmiana z A=(1.0, 0.95, 64) na **B=(0.8, 0.9, 50)**. Empiryczny test: A 99/100 OK (1 zapętlenie z `finish_reason=length`, 2000 tokenów), B i C 100/100 OK. Diversity tytułów identyczna (99-100/100 unikalnych). Wizualnie jakość outputów taka sama. Decyzja motywowana eliminacją rzadkich (~1%) zapętleń, nie jakością.
- **Status:** final. `lib/config.py` ma `SAMPLING_STEP1`, `SAMPLING_STEP2` jako osobne configi.

### D13: Niska temperatura NIE wystarczy dla determinizmu na Spark/Marlin
- **Co:** Phase 3 consistency test (5 URL × 3 reruns × 2 configi, sequential concurrency=1):
  - Config A (temp 1.0): 0/5 pełna identyczność, 0/5 te same nazwy, 0/5 sama liczba encji.
  - Config C (temp 0.3): 0/5 pełna identyczność, 0/5 te same nazwy, **1/5 sama liczba encji**.
- **Przykład:** "piłki gimnastyczne" vs "piłka gimnastyczna" (singular/plural) między runami; obecność/brak "plan treningowy".
- **Dlaczego nie ma determinizmu:** trzy źródła niedeterminizmu w sumie:
  1. Sampling losowy (temp > 0).
  2. FP arytmetyka — różne rozmiary batchu w vLLM dają minimalnie różne logits.
  3. Non-deterministic CUDA reductions w Marlin kernel (sm_121 fallback).
- **Konsekwencja dla prod 21M URL:** **NIE polegamy na deterministycznym rerun** dla idempotencji. Mamy `url_hash` skip w `JsonlReporter.load_existing_hashes()` (D9) — rerun po crash nie nadpisuje OK rekordów. Jeśli ktoś chce wymusić rerun, używa `--no-skip` i akceptuje że output będzie **semantycznie ekwiwalentny ale nie bit-identyczny**.
- **Co by dało prawdziwy determinizm:** `temperature=0.0` (greedy) + `seed=N` + concurrency=1 + jeden sprzętowy run. Niepraktyczne dla 21M URL na 1× 5090. Pomijamy.
- **Status:** final. Architektura idempotencji opiera się na `url_hash` filter, nie na deterministycznym output.

### D14: Prompt Step 1 v2 — refinement na podstawie Phase 4 analizy 100 URL
- **Co:** wzbogacony `prompts/step1_system.md` o:
  - Wzmocniony opis `structure`: "NOT anatomical body parts (spine, liver, immune system → use 'other')"
  - Wzmocniony opis `discipline`: "ONLY sports/fitness — NOT academic disciplines (dietetics, anatomy, biomechanics → use 'other')"
  - Rozbudowany opis `other`: jasne wskazania na anatomię, akademic fields, abstract concepts
  - Nowe disambiguation rules: `structure vs other (anatomy)`, `discipline vs other (academic)`
  - 9 nowych negatywnych przykładów (kręgosłup, biomechanika, BMI, tortownica, FSC, chusteczki, stres oksydacyjny...)
- **Empiryczna walidacja (50 URL wspólnych):**
  - Wszystkie zidentyfikowane problemy zniknęły: **6 → 0**
  - Stabilność typowania: 570 encji ten sam typ w v1 i v2, tylko 27 zmieniło typ (głównie te problematyczne)
  - Brak nowych problemów: `other` w v2 zawiera tylko sensowne wpisy (anatomia, akademic, abstrakty)
- **Koszt:** prompt urósł z 11 654 → 14 121 znaków, **2929 → 3628 tokenów (+24%)**. Cache amortyzuje to przy >1 requeście.
- **Backups:** `prompts/step1_system_v1.md` (oryginał) i `prompts/step1_system_v2.md` (= obecny aktywny).
- **Status:** v2 aktywne. Future iteracje: nazwa pliku stays `step1_system.md`, kolejne wersje w `_vN.md`.

### D15: Pełna migracja na Azure NER taxonomy + category + strength + metadata
- **Co:** porzucamy własną 23-typową taksonomię domain-specific. Adoptujemy [Microsoft Azure AI Language Service NER schema](https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/named-entity-categories) — 51 typów, production-grade, language-agnostic.
- **Pola w encji:**
  - `name` (string) — nazwa encji w języku artykułu
  - `type` (enum 51) — Azure type (exact case, np. `Person`, `Organization`, `City`, `Temperature`)
  - `category` (deterministic mapping) — 11 high-level Azure kategorii (Person, Organization, Location, Event, Product, Quantity, DateTime, Skill, Information, PersonType, Address, Email, IpAddress, PhoneNumber, URL)
  - `strength` (deterministic) — `strong` (linkable do Wikidata/KB, ma stabilny ID) lub `weak` (kontekstowo-zależna). Inspired DBMS strong/weak entity (Hotel/Room analogy).
  - `metadata` (optional) — structured resolution dla 18 typów Quantity/DateTime: `unit` (Celsius, Gram, Liter, Hour...), `value` (number), `ISO4217` (Currency), `timex` (Date/Time/Set ISO 8601), `numberKind`, `rangeKind`, `minimum`, `maximum`, `offset`, `relativeTo`.
- **Dlaczego Azure zamiast naszej:**
  - Production-grade taksonomia od Microsoft (lat doświadczenia, miliony zapytań)
  - Hierarchia 2-poziomowa (category → type) ułatwia querying
  - Metadata daje structured normalization: "180°C" → `{Celsius, 180}`, "5 maja 2025" → `{timex: "2025-05-05"}`. Gotowe do agregacji/filtrowania bez parsowania tekstu.
  - Eliminuje subiektywne wybory między custom typami (substance vs ingredient vs Product)
- **Co straciliśmy:** granularność dla domain-specific (witamina C i iPhone — oba `Product`). Trade-off zaakceptowany — dla 21M URL na różnych domenach Azure jest spójniejsze niż domain-specific.
- **Mapping starych typów:**
  - substance, ingredient, dish, species, asset, work → `Product` (broad)
  - disease, therapy (jeśli koncept), law, anatomy, abstract → `Information`
  - therapy (jako procedure), discipline, activity → `Skill`
  - brand → `Organization`
  - technology → `ComputingProduct`
  - nationality → `PersonType`
- **Empiryczna walidacja (50 URL, concurrency 8, 172s = 3,44 s/req):**
  - 50/50 OK
  - 1 355 encji, median 23 per artykuł (wzrost z ~15 w v2 — model wyciąga więcej dzięki Quantity/DateTime jednostkom)
  - 106/1355 (7,8%) encji z metadata
  - **100% metadata fill rate** dla: Duration (22/22), NumberRange (20/20), Volume (16/16), Number (10/10), Weight (9/9), Temperature (9/9), Percentage (8/8), Length (4/4), Date (2/2)
  - Strong/Weak ratio: 77% / 23%
  - Top categories: Product 74%, Information 10%, Skill 3%, Quantity 6%, DateTime 2%
- **Koszt promptu:** 3 628 → 6 940 tokenów (+91%). Cache prefix caching amortyzuje. Output per Step 1 ~ 200-400 tokenów (zależnie od liczby quantity entities).
- **Backups:** `prompts/step1_system_v{1,2,3,3_no_meta,4}.md`, `prompts/schema_step1_v{2,3,4}.json`. Aktualne aktywne — bez sufiksu (= v4).
- **Status:** final. Drobne błędy w metadata (`"jesień"` → Temporal z surowym timex; `"2-składnikowe"` → Number z niepotrzebnym offset) do iteracji w Phase 5 jeśli będzie potrzeba.

### D11: Flat layout (`lib/` + `scripts/`) zamiast `src/`
- **Co:** Moduły importowalne w `lib/`, runnable entry points w `scripts/`. Brak `pyproject.toml`, brak `pip install -e .`.
- **Dlaczego:** `src/` layout to standard dla **bibliotek dystrybuowanych przez PyPI** — wymusza pakowanie i niesie boilerplate. Nasz projekt to research pipeline odpalany lokalnie, nie biblioteka. Flat jest spójny z siostrzanym projektem `mateusz-g-json-vs-flat/`.
- **Alternatywy odrzucone:** `src/<package>/` z `pyproject.toml` — niepotrzebny narzut bez zysku (testy, izolacja, dystrybucja PyPI nie są w scope).
- **Status:** final. Jeśli kiedyś dystrybuujemy jako pakiet, wtedy migracja.

### D16: SPO v1 — entities + canonical names + is_central + free-form SPO triples (bootstrap discovery)
- **Co:** Nowy pipeline `spo_v1` (two-step: classify + entities_spo). Step entities rozszerzony o:
  (a) `name` jako forma kanoniczna (Wikidata/Wikipedia label, instrukcja w prompcie, brak osobnego pola `canonical_name`),
  (b) `is_central` boolean (max 5 per artykuł, cap promptem + post-processingiem `_cap_central`),
  (c) array `triples: [{s, p, o}]` z free-form predicates (1-3 słowa, lowercase, English verb phrase preferowane).
  Subject MUST matchować `entity.name` (canonical). Object: entity.name LUB literal value.
- **Dlaczego:** Fundament knowledge graph. **Bottom-up discovery** dla closed vocab v2 — zamiast cherry-pickować predicates z literatury (EDC/schema.org/ConceptNet), zbieramy free-form distribution na pełnej próbce ~25k URL (multi-domain: biznews.com.pl, intymnehistorie, praktycznyekspert.pl). Top-N po runie + clustering Levenshtein → kandydaci do enum v2.
  Single LLM call (rozszerzenie entities, nie osobny step) — model widzi tekst + entities razem, spójność wymuszona kontekstem. Zero dodatkowego wall time (większy output ale jeden call).
- **Alternatywy odrzucone:**
  - **Closed vocab z literatury** — bias (English research papers, brak pokrycia polskiego SEO/blogowego rejestru). Decyzja po danych.
  - **Osobny piąty step `spo`** — wymaga wysłania text+entities ponownie do modelu (+25% wall, +tokeny), ryzyko cross-step inconsistency.
  - **Hybrid: indeksy entities w triplets** — model słabo liczy indeksy, dodatkowy boilerplate.
  - **Post-hoc embedding canonicalizer (EDC)** — deferred (osobny krok offline po danych).
- **Oparte na:** Smoke test 5 URL conc=4 — 0 fails, 0% triples_s_unmatched (wszystkie subjekty matchują entities), avg 12 triples/article, central entities sensowne (trufla/Polska/Puszcza Białowieska dla artykułu o truflach), canonical names trzymają język artykułu (PL: "trufla", "Podkarpackie"). Predicates mieszają PL/EN — to oczekiwany sygnał dla bootstrap (analiza pokaże skalę problemu).
- **Pliki:** `prompts/spo_entities_v1_system.md`, `prompts/spo_schema_v1.json`, `lib/spo_pipeline_v1.py`, `scripts/run_spo_v1.py`, `scripts/spo_summary_v1.py`, `dashboard/views/spo.py`. Plan: `PLANS/spo_v1_bootstrap_plan.md`. TODO: `PLANS/spo_v1_todo.md`. Sesja: `SESSIONS_SUMMARY/2026-05-08_spo_v1_design.md`.
- **Status:** tentative — pełen run 25k URL w tmux benchmark (conc=8) w trakcie. Decyzja o closed vocab v2 (D17) po analizie SUMMARY.md i dashboardu.
- **Update 18:59 (po smoke)**: Smoke pokazał że "Predicates SHOULD be English" było za słabe — model defaultował do języka artykułu (PL: `rośnie w`, `preferuje`, `jest w`). Wymusiliśmy hard rule "**Predicate `p` MUST ALWAYS be in ENGLISH**" + dodatkowy przykład PL→EN (trufle: `grows in` NIE `rośnie w`) + WRONG/RIGHT note w przykładach. Subjects/objects nadal w języku artykułu (canonical entity names — to jest niezbędne dla string-match grounding). Pełen run #1 zatrzymany po ~15 min, restart #2 z poprawionym promptem (`final_results/2026-05-08_18-59-07__spo_v1_full_bootstrap_en/`). Full log redirectowany do `<out_dir>/full_stdout.log` (a nie /tmp).
- **Update 19:19 (po multi-agent)**: Run #2 zatrzymany po obserwacji: load_articles() blokuje GPU (5-15 min sekwencyjny trafilatura). Wprowadzono D17 (streaming loader) i D18 (v2 pipe SPO jako alternatywna architektura). v1 pozostaje single-call JSON, v2 to three-step pipe. Smoke v1+streaming + v2+streaming OK (predicates EN, 0 parse_errors, 0 s_unmatched). Uruchomiono **A/B równolegle** (oba conc=4, total 8 = max-num-seqs vLLM): v1 w `tmux benchmark`, v2 w `tmux benchmark2`. Out_dirs: `final_results/<ts>__spo_v{1,2}_AB_full/`.

### D17: Streaming loader z disk cache markdown (`websites_cache/`)
- **Co:** Nowy moduł `lib/streaming_loader.py:stream_articles_async(...)` — generator yieludujący dicty zgodnie z `load_articles`, ale: (a) producer ThreadPoolExecutor (n=4) parsuje trafilatura w równoległych wątkach (gzip + trafilatura zwalniają GIL), (b) bounded queue (`maxsize=200`) ogranicza pamięć, (c) per-hash cache `<websites_cache>/<hash>.md` dla zerowego trafilatura kosztu w kolejnych runach. Versioning cache `<cache_dir>/_version.txt = "v1"`.
- **Dlaczego:** Sekwencyjny `load_articles()` blokuje GPU przez 5-15 min na 25k URL, dla 1M URL → godziny GPU idle. Streaming wpuszcza pierwsze artykuły do queue w <1s, GPU pracuje od t=0. Cache: drugi run tych samych URL = 5.6× szybszy loader (smoke 1.91s cold → 0.34s hot, dla 1M proporcjonalnie).
- **Alternatywy odrzucone:**
  - **Multiprocessing zamiast ThreadPool** — niepotrzebne, gzip + trafilatura zwalniają GIL.
  - **Lazy `os.scandir()` także dla `random_sample=True`** — niemożliwe, random wymaga pełnej listy paths dla determinizmu seedu.
  - **Pre-process + commit cache do repo** — websites/ zewnętrzny dataset, pipeline ma robić cache w runtime.
  - **Compress cache (.md.gz)** — premature optimization. Decyzja po pomiarze GB na 1M.
- **Oparte na:** Smoke `scripts/test_streaming_loader.py` (20 URL): cold 1.91s, hot 0.34s, 100% identyczny tekst. Integrate test w `run_spo_v1.py` + `run_spo_v2.py` z flagami `--no-streaming`, `--loader-workers`, `--cache-dir`. Default streaming ON.
- **Pliki:** `lib/streaming_loader.py`, `scripts/test_streaming_loader.py`, `PLANS/streaming_loader_plan.md`. Backward compat: `lib/data_loader.load_articles()` nietknięty.
- **Status:** final dla scale 25k–1M. Re-evaluate przy 10M+.

### D18: v2 SPO pipeline — three-step (entities_only + spo_pipe), pipe format dla SPO
- **Co:** Alternatywna architektura v2: rozbicie single-call JSON (v1) na trzy stepy:
  (1) `classify` (jak w v1, reuse `process_classify_v2`),
  (2) `entities_only` — JSON, tylko `{name, type, is_central}` (bez triples),
  (3) `spo_pipe` — non-JSON, raw text format `subject|predicate|object\n` per linia. Predicates **HARD RULE** ENGLISH only.
- **Dlaczego:**
  - Output tokens: pipe ~13 tok/triple vs JSON ~50+ tok/triple (zmierzone na smoke). 60-70% redukcja.
  - Każdy step ma osobny budget tokenów — single-call v1 czasem łapał `MAX_TOKENS_STEP1=4000` przy 60 ent + 40 trip dla długich artykułów.
  - Prompty bardziej focused (entities prompt nie tłumaczy SPO rules → krótszy → tańszy prefix cache).
  - Smoke wynik: v2 wall 18.2s vs v1 26.2s (5 URL conc=4) — **~44% szybszy** mimo +1 step, dzięki krótszemu output. Plus więcej triples (49 vs 32) — model ma więcej miejsca w spo step.
- **Alternatywy odrzucone:**
  - **Indeksy entities w schemie JSON** (`subject_idx: int`) — model słabo liczy indeksy.
  - **Closed enum predicates** w pipe — premature, robimy bottom-up discovery na pełnej próbce. Po analizie top-N predicates → ewentualnie v3 z closed vocab.
  - **Single-step JSON dla SPO ale bez entities** — tracimy grounding (entity name match).
- **Oparte na:** Smoke `scripts/run_spo_v2.py --limit 5 --concurrency 4`: 0 parse_errors, 0 s_unmatched, 100% predicates EN, finish_reason=stop dla wszystkich (brak truncate). vLLM raw POST z guided_choice/text-mode (bez xgrammar — pipe format walidowany przez parser w post-processing).
- **Pliki:** `prompts/spo_entities_only_v2_system.md`, `prompts/spo_entities_only_v2_schema.json`, `prompts/spo_pipe_v2_system.md`, `lib/spo_pipeline_v2.py`, `scripts/run_spo_v2.py`, `PLANS/spo_v2_pipe_plan.md`.
- **A/B vs v1**: Uruchomiono równolegle 19:19 (oba conc=4 = total 8 = max vLLM). Decyzja final v1 vs v2 vs hybrid po pełnym runie z metrykami: wall, throughput, parse error rate, predicate distribution overlap, central entity precision, % EN predicates.
- **Status:** A/B running. Decyzja D20 po wynikach.

### D19: v3 classifier — pre-filter URL regex + wzmocniony prompt OVERRIDE signals
- **Co:** Dwustopniowy fix recall na junk listing pages:
  (1) **Deterministyczny pre-classifier** w `lib/junk_pre_filter.py:is_definite_url_junk(url, path, query)`. Regex match na 100% pewnych patternach junk: `/tag/`, `/tags/`, `/tagi/`, `/author/`, `/autor/`, `/archive[s]?/`, `/archiwum/`, `/search/`, `/szukaj/`, `?s=...`, `?paged=N`, `?start=N`, `/topic/`, `/temat/`, `/label/`, `/etykieta/`. Match → skip LLM, zapisz stub z `ml_skipped=True, junk_reason=<reason>`. Oszczędza ~0.2-0.6s/URL na takich, +deterministycznie nie myli.
  (2) **`prompts/step_junkclassify_v3_system.md`** — sekcja `OVERRIDE URL signals` zastępuje wcześniejszy `Strong URL signals`. Reguła: tag/category/author/page/search URL → JUNK regardless of content (chyba że tail ma 200+ chars prose description tematu). Krytyczny: `/tag/` z 1-2 wpisami pozostaje JUNK (rule 3+ snippets NIE applies tutaj). Dwa nowe przykłady: K (tag z 1 wpisem) + L (single-entry author archive).
- **Dlaczego:** v2 classifier prompt miał recall 73% na tag pages (22/81 false negatives na pomocedlaseniora.pl). Tag pages to dominująca klasa junku w niektórych domenach (62/80 = 78% pomocedlaseniora). Pre-filter łapie tag/author/search deterministycznie (bez LLM cost), prompt v3 łapie pozostałe URL-pattern junki (np. `/category/X/` które mogą mieć description) gdzie pre-filter jest za agresywny.
- **Alternatywy odrzucone:**
  - **Pre-filter dla `/category/`** — false positives na e-commerce description pages.
  - **Pre-filter dla `/page/N/`** — niejednoznaczne (może być paginacja artykułu).
  - **Tylko prompt update bez pre-filtra** — wciąż trace miss na edge cases, plus marnujemy LLM na deterministyczne junky.
  - **Globalny ban listy URL patterns w preprocessing** — to robi pre-filter. Tylko zmiana lokalizacji.
- **Oparte na:** Analiza 1516 v1 + 1817 v2 records z aborted run 19:19 — 22 tag false negatives w v1, 27 w v2. Smoke v3 (5 URL conc=2): działa, pre-filter łapie 0/2 junków bo seed=42 nie miał tag pages. Smoke v3+lw2 (30 URL random seed=999): 2 junk z 30, 28 entities_spo OK, 0 fails.
- **Pliki:** `lib/junk_pre_filter.py`, `prompts/step_junkclassify_v3_system.md`, integracja w `scripts/run_spo_v1.py` + `scripts/run_spo_v2.py` (load v3 prompt + pre-filter check przed q_classify put).
- **Status:** A/B running na pełnych 25k URL (v1 + v2 oba z v3 classifier).

---

### D20: Sponsored v2 — zlanie `full_sponsored` + `link_insertion` w `paid_placement`
- **Co:** Nowy prompt `prompts/step_sponsored_v2_system.md` + schema `prompts/schema_sponsored_v2.json`. Enum subtypes: `[null, paid_placement, brand_mentions, advertorial]` (było: `[null, full_sponsored, link_insertion, brand_mentions, advertorial]`). Decision tree: disclaimer → `advertorial`; external link(s) z promo context (krótka wstawka lub cały artykuł) → `paid_placement`; brak linków + ≥2 wzmianki → `brand_mentions`. `scripts/run_fourstep_v1.py` przełączony na v2.
- **Dlaczego:** Pomiar na 500-URL run (`final_results/2026-05-08_17-32-56__fourstep_v1_v2_1_500_seed123`): z 267 sponsored 159 (59.5%) klasyfikowane jako `link_insertion`, 101 (37.8%) `full_sponsored`. Inspekcja justifikacji ujawniła że wiele `link_insertion` to faktycznie `full_sponsored` (cały artykuł poświęcony promocji marki, np. `biznews.com.pl/.../artykuly-biurowe-gdansk-gdynia` z czterema wzmiankami flowoffice.pl + CTA). Prompt v1 mówił `link_insertion = "possibly seamlessly (semantic-fitting paragraph)"` — każdy artykuł z linkiem zewn. + krótką notką wokół niego trafiał do `link_insertion`.
- **Alternatywy odrzucone:**
  - **Topic-match test** (artykuł o niszy = full_sponsored, niedopasowany = link_insertion). W realnym rynku PL link insertion jest **właśnie** sprzedawany jako tematycznie dopasowany (wydawcy oferują 3-8 zdań notki kontekstowej wokół linku). Topic match nie jest discriminatorem.
  - **Aggressive thresholds full_sponsored** (≥5 mentions LUB ≥50% artykułu o marce). Generuje błędy w drugą stronę bez rzetelnej granicy.
  - **Trzeci subtype `promotional_article`** dla strefy granicznej. Dodaje kolejną fuzzy granicę.
- **Oparte na:** 500-URL run analiza confusion w `sponsored.jsonl`; feedback użytkownika o realiach rynku PL link building (link insertion zawsze tematycznie dopasowany + krótka notka, granica z artykułem sponsorowanym fuzzy z definicji).
- **Status:** do walidacji — następny run (te same 500 URL, seed=123) na v2 → porównanie z v1.

---

### D21: Drain-first worker priority + bounded `q_classify` (SPO v1/v2)
- **Co:** Worker w `scripts/run_spo_v1.py` + `scripts/run_spo_v2.py` priorytetuje **późniejsze etapy pipeline'u**:
  - v1: `q_entities (get_nowait)` > `q_classify (get timeout=0.1)`.
  - v2: `q_spo (get_nowait)` > `q_entities (get_nowait)` > `q_classify (get timeout=0.1)`.
  - Dodatkowo `q_classify = queue.Queue(maxsize=concurrency*8)` — bounded, żeby producer streaming loadera throttlował się gdy workery nie nadążają.
- **Dlaczego:** Run 19:47 z poprzednim priorytetem (`classify > entities > spo`) — po 17 minutach `spo_pipe.log` był pusty (0 B), `classified.jsonl` 6.1 MB rosło, `entities.jsonl` zamarł na 54 KB o 19:50. Worker starvation: producer zalewał `q_classify` szybciej niż 4 workery konsumowały, `get_nowait()` na classify zawsze trafiał, etapy entities/spo nigdy nie dostawały slotu. Spo_pipe miał czekać aż producer skończy 21M URL i `q_classify` się opróżni.
- **Konsekwencje:**
  - **GPU saturation:** vLLM zawsze ma 4 inflight requesty — gdy `q_spo`/`q_entities` puste, worker fallbackuje na classify (nowy materiał). Etap nie ma znaczenia dla throughputu (token to token na tym samym serwerze).
  - **Memory:** szybszy drain `state[url_hash]` (drop po `try_finalize` dla każdego artykułu), peak RAM stabilniejszy.
  - **Pipeline visibility:** spo_pipe.log/entities_only.log nabijają linie od pierwszych sekund runa, nie po skończeniu wszystkich classify.
- **Alternatywy odrzucone:**
  - Tylko bounded queue bez zmiany priorytetu — rozwiązuje RAM, nie rozwiązuje starvation (workery nadal najpierw obsługują classify gdy queue jest pełna).
  - Tylko zmiana priorytetu bez bounded — przy 21M URL `q_classify` rośnie unbounded (każdy artykuł = dict z markdown text, peak GB+).
- **Oparte na:** Diagnoza runa `final_results/2026-05-08_19-47-43__spo_v{1,2}_AB_v3` — `spo_pipe.log = 0 B` po 17 min, ratio classified.jsonl / entities.jsonl = 6.1 MB / 54 KB.
- **Status:** zaimplementowane, A/B running pełna próbka v1 + v2 z tagiem `full_drainfix`.

---

### D22: SPO v2 entity context — `name [type, central]` zamiast samych `name`
- **Co:** `lib/spo_pipeline_v2.py:process_spo_pipe_v2` formatuje encje jako bullet list z tagami: `* {name} [{type}, central]` dla `is_central=True` lub `* {name} [{type}]` dla pozostałych. Central first. System prompt `prompts/spo_pipe_v2_system.md` rozszerzony o sekcję `## ENTITY METADATA — how to use the tags` (priors: `central` → preferowany jako `s`, type → role priors Org/Person → subject vs Number/Currency/Temperature → object, type → predicate priors Temperature → `cooked at`, Currency → `costs`, Date → `released in`).
- **Dlaczego:** Po enrich każda encja ma 4 deterministyczne pola (`type`, `category`, `strength`, `is_central`) — przed zmianą do user prompta szła tylko `name`. `is_central` (max 5 głównych bohaterów artykułu) to mocny sygnał priorytetyzacji subject; `type` (51 Azure NER) różnicuje role w triplecie (Quantity zwykle = object, Person/Org zwykle = subject) i pozwala dobrać faithful predicate.
- **Konsekwencje:**
  - **Koszt tokenów:** ~+150 tok per article (5 tok per encja × ~30 encji średnio). Akceptowalne — sygnał > koszt.
  - **Pominięte:** `category` (deterministyczna agregacja typów do 11 grup — duplikat sygnału `type`), `strength` (skorelowany z typem: Person/Org=strong, Number/Quantity=weak — duplikat).
  - **Sortowanie central-first:** model widzi `[central]` na górze listy, naturalnie priorytetuje je.
- **Alternatywy odrzucone:**
  - Pełny dump enriched encji jako JSON w prompcie — generuje noise, łamie pipe-only output (model może chcieć echo'ować JSON).
  - Tylko `is_central` bez `type` — traci ważny sygnał role (Quantity vs Product).
  - Wszystkie 4 pola — `category`+`strength` to ~200 dodatkowych tok/article × 21M URL bez proporcjonalnego zysku.
- **Oparte na:** Inspekcja `lib/pipeline.py:enrich_entity` — schema v6 dodaje `type, category, strength` deterministycznie; CLAUDE.md mapping `TYPE_TO_CATEGORY`. User feedback: "tyle danych o encjach i nie przekazujemy".
- **Status:** zaimplementowane w `lib/spo_pipeline_v2.py` + `prompts/spo_pipe_v2_system.md`, A/B running na pełnej próbce z tagiem `full_drainfix` (porównanie quality vs poprzedni run 19:47 na nazwy-only).

---

## Format dla nowych decyzji

```markdown
### D{N}: krótki tytuł
- **Co:** technicznie co.
- **Dlaczego:** powód (problem/opcja/empiryczny pomiar).
- **Alternatywy odrzucone:** opcjonalne — co rozważano i czemu nie.
- **Oparte na:** spec/pomiar/issue.
- **Status:** final / do walidacji w Phase X / tentative.
```
