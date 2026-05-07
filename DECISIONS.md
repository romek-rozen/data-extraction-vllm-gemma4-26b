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

### D7: Two-step pipeline (entity extraction + SEO meta)
- **Co:** Step 1 (universal English prompts, language detection, entities + category) → pipe note → Step 2 (language-aware SEO meta).
- **Dlaczego:** dwa zadania o fundamentalnie różnym charakterze — Step 1 deterministyczna ekstrakcja, Step 2 kreatywna generacja. Każdy może mieć osobny optymalny config (Phase 3).
- **Wartość pipe note:** uniwersalna warstwa encji wielokrotnego użytku (knowledge graph, search, multilingual expansion).
- **Walidacja:** Phase 2 — empiryczne porównanie two-step vs one-step na 200 URL.

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
