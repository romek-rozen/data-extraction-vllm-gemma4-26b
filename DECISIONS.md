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
- **Status:** final, walidacja przez 100 URL run.

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

### D11: Flat layout (`lib/` + `scripts/`) zamiast `src/`
- **Co:** Moduły importowalne w `lib/`, runnable entry points w `scripts/`. Brak `pyproject.toml`, brak `pip install -e .`.
- **Dlaczego:** `src/` layout to standard dla **bibliotek dystrybuowanych przez PyPI** — wymusza pakowanie i niesie boilerplate. Nasz projekt to research pipeline odpalany lokalnie, nie biblioteka. Flat jest spójny z siostrzanym projektem `mateusz-g-json-vs-flat/`.
- **Alternatywy odrzucone:** `src/<package>/` z `pyproject.toml` — niepotrzebny narzut bez zysku (testy, izolacja, dystrybucja PyPI nie są w scope).
- **Status:** final. Jeśli kiedyś dystrybuujemy jako pakiet, wtedy migracja.

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
