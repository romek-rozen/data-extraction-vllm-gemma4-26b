# Project: Meta and Entity Extraction from 21M URLs

**Version 4 — full English prompts + universal entity types + disambiguation rules**

## TL;DR

Two-step pipeline:

1. **Step 1: Entity extraction** (universal, language-agnostic) — entities + category, deterministic structure enforcement via `guided_json`. Output becomes a **pipe note** (entity layer reusable long-term).
2. **Step 2: SEO meta generation** (language-aware, creative) — title, meta_description, h1, summary in article's language

**Sampling: Google defaults (temp 1.0, top_p 0.95, top_k 64) for both steps** — change only with empirical evidence.

**All prompts in English** for token efficiency (~30% fewer tokens than Polish system prompts) and universal entity typology.

Stack: **Gemma 4 26B A4B NVFP4** + **vLLM** + **guided_json (xgrammar)** on **RunPod RTX 5090**, dev/staging on **DGX Spark**. Estimated: 1×5090 ≈ 12-15 days, 2×5090 ≈ 6-8 days, cost ~$200-300.

## Project context

- **Scale:** 21 million URLs to process
- **Per article:** ~12k input tokens (to be reduced via HTML cleanup), ~225-375 output tokens total
- **Language:** mostly Polish, but **prompts must be language-agnostic** (future expansion to other languages)
- **Output:** meta data (title, meta_description, h1, category, article_summary) + entities (name + type)
- **Hardware dev:** DGX Spark (GB10, sm_121, 128GB unified, 273 GB/s bandwidth)
- **Hardware prod:** RunPod (RTX 5090, sm_120, 32GB VRAM, 1792 GB/s bandwidth)

---

# 🚦 WORKFLOW: Spark first, RTX later

## Stage A: Everything on DGX Spark (development + validation)

**Goal:** refine pipeline on available hardware before paying for RunPod.

What we do on Spark:
1. ✅ Setup vLLM + NVFP4 model (with Marlin fallback for sm_121)
2. ✅ Validate two-step architecture vs one-step
3. ✅ HTML cleanup pipeline (trafilatura)
4. ✅ A/B test sampling parameters (Google defaults vs lower temperatures)
5. ✅ Quality validation on 100-500 URL samples
6. ✅ Iterate on prompts and JSON schemas
7. ✅ Test idempotence, checkpoints, error handling

**Spark is sufficient for testing** — 50-100 URLs/hour is enough to validate quality. Throughput doesn't matter here, output quality does.

**What we skip on Spark:** prod run for 21M URLs (too slow, ~year of work).

## Stage B: Migration to RunPod RTX 5090 (production)

**Goal:** prod run for 21M URLs with refined pipeline.

What transfers from Spark:
- All application code (Python scripts)
- Prompts and JSON schemas
- Empirically chosen sampling parameters
- HTML cleanup logic
- Idempotence and checkpoint configuration

**What changes:**
- vLLM image: `vllm/vllm-openai:latest` instead of `vllm/vllm-openai:gemma4-cu130`
- No `--moe-backend marlin` (5090 has native FP4)
- Higher `--max-num-seqs` (32-64 instead of 8-16)
- Network Volume with model
- Idle timeout, monitoring, alerts

---

# Stage A: Test plan on DGX Spark

## Phase 0: vLLM setup on Spark (1 day)

```bash
# 1. Download Spark-tested quant
huggingface-cli download bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4 \
  --local-dir ~/models/gemma4-26b-nvfp4

# 2. Run vLLM via NVIDIA NGC docker (sm_121 support)
docker run -d --gpus all --ipc=host \
  -v ~/models/gemma4-26b-nvfp4:/model \
  -v ~/models/gemma4-26b-nvfp4/gemma4_patched.py:/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/gemma4.py \
  -p 8000:8000 \
  vllm/vllm-openai:gemma4-cu130 \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.85 \
  --moe-backend marlin \
  --enable-prefix-caching

# 3. Basic test: 1 URL via API, verify it works
curl http://localhost:8000/v1/chat/completions ...
```

**Goal:** vLLM works, model loads, responds to requests. Quality not yet important.

## Phase 1: HTML cleanup pipeline (1 day)

**Goal:** check input shortening possibilities.

```python
import trafilatura

def clean_article(html_or_url, is_url=False):
    if is_url:
        downloaded = trafilatura.fetch_url(html_or_url)
    else:
        downloaded = html_or_url
    
    clean = trafilatura.extract(
        downloaded,
        output_format='markdown',
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    return clean or ""
```

**Test:**
1. Take 100 URLs from various domains
2. Measure token length distribution BEFORE cleanup vs AFTER
3. Eyeball: does cleanup truncate meaningful content?

**Decision:** if median drops >40% without quality loss → cleanup MANDATORY in prod.

**What to save:** length statistics (median, p95, max) before and after, ~10 examples for comparison.

## Phase 2: Two-step architecture validation (1-2 days)

**Goal:** prove that two-step gives better results than one-step.

1. Implement **one-step** baseline (refactored to English, universal)
2. Implement **two-step** (Step 1 + Step 2)
3. Run 200 URLs in both modes
4. Eyeball quality assessment:
   - Are entities consistent and correct?
   - Are meta descriptions more idiomatic in two-step?
   - Does two-step handle tricky entity types better (substance vs therapy, discipline vs activity)?

**Decision:**
- Two-step clearly better (>15% quality difference) → continue with two-step
- Marginal difference (<10%) → stay with one-step (cheaper, simpler)
- Mixed results → eyeball in detail, decide per use case

## Phase 3: A/B test sampling parameters (half day)

**Goal:** empirically verify whether Google defaults (temp 1.0) are optimal for **your** data, or lower temperatures give better results.

### ⚠️ Important rationale

Gemma 4 was **calibrated by Google** for sampling temp 1.0, top_p 0.95, top_k 64. By default we should use these values. Lower temperatures (e.g., 0.3) are **deviations from calibration** — may work for other models, but for Gemma it's **quality degradation** unless you have empirical evidence otherwise.

With guided_json enforcing schema at the token level, **low temperature is not needed** for safety — grammar already protects against type hallucination and loops.

### Tests to perform

**Step 1 (Entity extraction):**

Run the same 100 URLs with different sampling configs:

| Config | Temperature | top_p | top_k | Rationale |
|---|---|---|---|---|
| **A: Google default** | 1.0 | 0.95 | 64 | Calibrated baseline |
| B: Conservative | 0.7 | 0.9 | 50 | Common practice for extraction |
| C: Aggressive low | 0.3 | 0.9 | 40 | "Deterministic extraction" theory |

**What to measure:**
- Number of entities per article (mean, median)
- Entity diversity (catches rare/nuanced entities vs only obvious ones?)
- Type consistency (does enum hold every time? — with guided_json always yes)
- Consistency on 3x rerun of same URL (low temp = higher consistency)
- Eyeball: does any config give "stupider" outputs?

**Step 2 (SEO meta generation):**

Run the same 100 URLs with different sampling configs:

| Config | Temperature | top_p | top_k | Rationale |
|---|---|---|---|---|
| **A: Google default** | 1.0 | 0.95 | 64 | Calibrated baseline |
| B: Slightly lower | 0.8 | 0.9 | 50 | Mild dampening |
| C: Conservative | 0.5 | 0.9 | 40 | Lower variance, mechanical meta |

**What to measure:**
- Eyeball quality of meta (does it sound natural/idiomatic?)
- Style diversity (are meta different for different articles, or template-like?)
- Length adequacy (uses full character budget or truncates?)
- Contains anti-patterns ("Learn more!", "Article describes...")

### Final decision

**By default we choose Google defaults (config A, temp 1.0).** We go below **only if:**
- Config B or C gives **clearly** better results in eyeball assessment
- Quality outweighs theoretical "should be higher because Google says so"

## Phase 4: Prompt iteration (1-2 days)

**Goal:** refine prompts based on observations from Phases 2-3.

Typical iterations:
- Add/remove few-shot examples
- Refine entity type descriptions (which types confuse most often)
- Tuning long descriptions vs short descriptions in categories
- Adjusting max_tokens for output budget
- Adding categorization guidance for ambiguous cases

**Test after each change:** 50 URLs, eyeball, decide if change helps.

## Phase 5: End-to-end validation on 500-1000 URLs (1 day)

**Goal:** final validation before migrating to RunPod.

1. Setup full pipeline: HTML cleanup → Step 1 → entity layer → Step 2 → final output
2. Run 500-1000 URLs end-to-end
3. Verify:
   - Idempotence (rerun should give same Step 1 outputs with deterministic temperature)
   - Error handling (which URLs fail? Why?)
   - Quality consistency
   - Performance baseline (time/URL on Spark — sanity check)

## Phase 6: Decision gate — migration to RTX 5090

**Readiness check:**

✅ Two-step pipeline proven
✅ HTML cleanup validated (if helpful)
✅ Sampling parameters chosen empirically (Google defaults or lower with proof)
✅ Prompts and schemas stable (>3 versions tested)
✅ End-to-end pipeline works on 500-1000 URLs without crashes
✅ Quality is "good enough" in eyeball assessment

**If all ✅ → migrate to RunPod RTX 5090.**

---

# Stage B: Migration to RunPod RTX 5090

## Phase 7: RunPod setup (half day)

```bash
# 1. Create Network Volume 100GB in DC with 5090 (Secure Cloud)
# 2. Spin up cheapest pod (3090, ~$0.20/h, 1h) only for setup:

pip install vllm trafilatura
huggingface-cli download nvidia/Gemma-4-26B-A4B-NVFP4 \
  --local-dir /workspace/model

# 3. Copy app code + schemas + prompts from Spark (runpodctl/scp)
# 4. Test 100 URLs end-to-end

# 5. Stop pod — volume stays
runpodctl stop pod
```

## Phase 8: Performance test (1× 5090, 2h, ~$2)

**Sample:** 5000 representative URLs.

**Measurements:**

| Metric | Target | How to measure |
|---|---|---|
| Step 1 throughput (t/s aggregated) | >2000 | vLLM metrics |
| Step 2 throughput (t/s aggregated) | >2000 | vLLM metrics |
| Step 1 prefix cache hit rate | >70% | vLLM metrics |
| Step 2 prefix cache hit rate | >70% | vLLM metrics |
| End-to-end time per URL | <2s amortized | App logs |
| VRAM utilization | <95% steady | nvidia-smi |
| Quality consistency | Zero drift vs Spark | Eyeball 50 outputs |

**vLLM config for 5090:**

```bash
docker run --gpus all -v /workspace:/workspace \
  vllm/vllm-openai:latest \
  --model /workspace/model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 16384 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4
```

**Differences vs Spark:**
- ✅ No `--moe-backend marlin` (native FP4)
- ✅ Higher `--max-num-seqs 32` (vs 8-16 on Spark)
- ✅ Higher `--gpu-memory-utilization 0.92` (vs 0.85)
- ✅ Image `vllm/vllm-openai:latest` (sm_120 native)

**Decision after Phase 8:** 1× or 2× 5090 for prod run.

## Phase 9: Production run

**Configuration:**
- Number of cards: 1 or 2× 5090
- Secure Cloud (NOT Community)
- Strategy: Option A (sequential) or B (pipelined)

**Operational MUST-HAVEs:**
- Idempotent writes (key = URL hash)
- Checkpoints every 1000 URLs
- Failed queue for crashing URLs
- Backup outside Network Volume (S3/GCS)
- Live sanity check every 10k URLs
- Idle timeout for automatic stop after completion

**Time estimate:**
- 1× 5090: 12-15 days → ~$200-280
- 2× 5090: 6-8 days → ~$200-300

---

# ⚠️ Key architectural decision: TWO-STEP PIPELINE

**Reason:** two tasks have fundamentally different nature and require different model parameters.

### Step 1 vs Step 2 — comparison

| Aspect | Step 1: Entity extraction | Step 2: SEO meta generation |
|---|---|---|
| Character | Deterministic extraction | Creative generation |
| Temperature | **1.0 (Google default)** — A/B test | **1.0 (Google default)** — A/B test |
| Output language | Universal (preserved from article) | Language-aware (same as article) |
| Schema enforcement | guided_json + enum (hard) | guided_json + maxLength (soft) |
| Few-shot needed | Less (with guided_json) | **YES** (style transfer) |

**Note on temperatures:** Gemma 4 is calibrated by Google for temp 1.0. Plan starts from Google defaults and goes lower only with empirical evidence (Phase 3 tests on Spark).

### Two-step benefits

✅ Each step has optimal sampling parameters (if A/B test shows different)
✅ Step 1 → pipe note (universal entity layer reusable)
✅ Independent retry — bad meta doesn't force re-running entity extraction
✅ Better cache hit rate in vLLM (shorter, more stable system prompts)
✅ Universal entity layer = long-term value

### Trade-off

⚠️ 2x prefill 12k input = higher total compute per URL
⚠️ Project time extends ~1.5-1.8x (not 2x because step 2 has smaller output)

---

# 📚 How guided_json + enum actually works

**Important for understanding why prompt design matters:**

`enum` in JSON schema is **NOT passed to the model as instruction**. It's a constraint at **token decoding** level:

```
Model tries to generate "type": "made_up_type"
                                    ↓
              Grammar engine (xgrammar) blocks
                                    ↓
              Model MUST pick token from allowed list
```

**Implication:** model **physically cannot** generate type outside the list — regardless of whether it knows its meaning. Grammar enforces syntactic correctness, not semantic.

**Problem:** model picks **randomly** from allowed types if it doesn't know what they mean. Without descriptions in system prompt, model would guess between "substance" and "therapy" for "vitamin C".

**Solution:** entity type descriptions live in **system prompt** (cached via prefix caching), enum in schema only enforces selection.

**This is why:**
- ✅ Descriptions stay in system prompt (cached, no per-request cost)
- ❌ Don't move descriptions to user prompt (loses caching, +30-40B tokens for 21M URLs)
- ❌ JSON Schema `description` field is dead weight for xgrammar (not passed to model)

---

# STEP 1: Entity Extraction (Universal, Language-Agnostic)

## Goal

Extract entities and classify category from article. **Output language-agnostic** — entities preserve original article language. Auto-detect language (ISO 639-1).

## Sampling parameters (Step 1)

**Default (Google defaults):**

```python
SamplingParams(
    temperature=1.0,         # Google default for Gemma 4
    top_p=0.95,              # Google default
    top_k=64,                # Google default
    repetition_penalty=1.0,  # NOT 1.2 — breaks repeating JSON keys
    max_tokens=400,
    guided_decoding=GuidedDecodingParams(json=schema_step1),
)
```

**After Phase 3 (A/B test):** empirically chosen best config.

## JSON Schema (Step 1)

```json
{
  "type": "object",
  "properties": {
    "category": {
      "type": "string",
      "enum": ["Automotive", "Beauty", "Business", "Computer games", "Construction",
               "Cooking", "Culture, Art", "Diet, Weight loss", "Ecology",
               "Economy, Industry", "Education, Science", "Entertainment",
               "Family, Child, Pregnancy", "Fashion", "Finance, Banking and Insurance",
               "Gastronomy", "Gossip, Celebrity Life, Lifestyle", "Health, Medicine",
               "History", "House, Garden, Interiors",
               "Household appliances and consumer electronics",
               "IT, New technologies, Computers", "Law", "Marketing, Advertising, Media",
               "Music", "News", "Other themes", "Photography and video-filming",
               "Politics", "Power engineering", "Psychology, Personal development",
               "Purchases, Opinions", "Real estate", "Religion", "Sex and eroticism",
               "Sport, Fitness, Bodybuilding", "Tourism, Travel",
               "Transport & Logistics", "Wedding", "Work",
               "Zoology, agriculture and forestry", "junkey"]
    },
    "language": {
      "type": "string",
      "description": "ISO 639-1 code of detected article language",
      "pattern": "^[a-z]{2}$"
    },
    "entities": {
      "type": "array",
      "minItems": 0,
      "maxItems": 15,
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string", "maxLength": 100},
          "type": {
            "type": "string",
            "enum": ["person", "organization", "location", "brand", "product",
                     "technology", "event", "work", "date", "money",
                     "asset", "law", "nationality", "structure", "substance",
                     "disease", "therapy", "species", "dish", "ingredient",
                     "discipline", "activity", "other"]
          }
        },
        "required": ["name", "type"]
      }
    }
  },
  "required": ["category", "language", "entities"]
}
```

**Entity types — English universal:**

| Old (PL) | New (EN) | Notes |
|---|---|---|
| osoba | person | |
| organizacja | organization | |
| lokalizacja | location | |
| marka | brand | |
| produkt | product | |
| technologia | technology | |
| wydarzenie | event | |
| dzieło | work | (creative work, not employment) |
| data | date | |
| pieniądze | money | |
| aktywo | asset | (financial asset) |
| prawo | law | |
| narodowość | nationality | |
| obiekt | structure | (built structure, not abstract object) |
| substancja | substance | |
| choroba | disease | |
| terapia | therapy | |
| gatunek | species | |
| danie | dish | |
| składnik | ingredient | |
| dyscyplina | discipline | (sports discipline) |
| działanie | activity | |
| inne | other | |

## System prompt (Step 1) — full English

```markdown
## ROLE
Expert content analyst extracting structured data from articles.

## TASK
Extract entities and classify the article into one category.
Detect the article's language and return ISO 639-1 code (pl, en, de, es, fr, it, ru, etc.).
Entity names MUST preserve the original language of the article (do not translate).
Return ONLY a valid JSON object matching the schema. No markdown, no extra text.

## CATEGORIES (choose exactly ONE)

- Automotive: cars, motorization, car brands, parts, repairs, fuel
- Beauty: cosmetics, skincare, makeup, beauty salons, hairdressing
- Business: company management, e-commerce, startups, B2B sales
- Computer games: video games, esports, game reviews, streaming
- Construction: building materials, renovations, construction crews
- Cooking: cooking, recipes, kitchen techniques, ingredients, home cuisine
- Culture, Art: culture, art, painting, sculpture, theater, museums, literature
- Diet, Weight loss: diets, weight loss, meal plans, calories, healthy eating
- Ecology: ecology, environment, climate change, recycling, renewables
- Economy, Industry: economy, industry, manufacturing, macroeconomics
- Education, Science: education, schools, universities, science, research
- Entertainment: entertainment, films, series, TV shows, cinema
- Family, Child, Pregnancy: family, children, pregnancy, parenting
- Fashion: fashion, clothing, accessories, trends, designers
- Finance, Banking and Insurance: personal finance, banks, loans, insurance, investments, taxes
- Gastronomy: restaurants, cafes, restaurant reviews, world cuisines
- Gossip, Celebrity Life, Lifestyle: gossip, celebrities, lifestyle
- Health, Medicine: health, medicine, diseases, treatment, drugs, prevention
- History: history, historical events, historical figures, archaeology
- House, Garden, Interiors: home, garden, interiors, decorations, furniture, plants
- Household appliances and consumer electronics: home appliances, consumer electronics
- IT, New technologies, Computers: IT, new technologies, software, AI, programming
- Law: law, statutes, regulations, legal advice
- Marketing, Advertising, Media: marketing, advertising, media, PR, social media
- Music: music, concerts, albums, artists, music genres, instruments
- News: current news, daily politics, daily economy (when not fitting other categories)
- Other themes: topics not fitting any other category
- Photography and video-filming: photography, filming, photo equipment, editing
- Politics: politics, parties, elections, parliaments, politicians
- Power engineering: power engineering, power plants, energy grids, fuels
- Psychology, Personal development: psychology, personal development, therapy, motivation
- Purchases, Opinions: shopping, product reviews, comparisons, rankings
- Real estate: real estate, apartments, houses, buying/selling, rental
- Religion: religion, churches, denominations, spirituality
- Sex and eroticism: sex, eroticism, sexual education, intimate relationships
- Sport, Fitness, Bodybuilding: sport, fitness, sports disciplines, training, supplementation
- Tourism, Travel: tourism, travel, attractions, hotels, sightseeing
- Transport & Logistics: transport, logistics, shipping, rail, aviation
- Wedding: weddings, wedding preparations, dresses, organization, venues
- Work: work, career, recruitment, CVs, job interviews, labor law
- Zoology, agriculture and forestry: zoology, agriculture, forestry, animal breeding, crops
- junkey: junk content (ads only, no real content, empty WordPress template)

For "junkey" category: return entities: []

## ENTITY TYPES (use ONLY these values, lowercase)

- person: specific human, historical figure, author, athlete, politician, celebrity
- organization: company, party, office, institution, club, foundation, agency
- location: country, city, region, district, street, continent, river, mountain. NOT for single buildings — that's "structure"
- brand: specific brand or trademark (Nike, Apple, Coca-Cola) — distinguished from organization for SEO context
- product: specific physical product or consumer digital service (iPhone 15, Tesla Model S, Photoshop, WordPress, Netflix). For programming language, framework, protocol or AI model use "technology"
- technology: programming language, framework, library, standard, protocol, AI model, IT or industrial technology (React, TypeScript, GPT-4, Bluetooth, HTTP/3, LoRa, blockchain)
- event: conference, concert, festival, sports competition, holiday, marketing campaign, specific historical event
- work: title of film, book, game, TV show, song, album, journal, magazine
- date: specific date, year, period, era or season ("summer 2024", "1990s", "September 21, 2023")
- money: amount, price, monetary value or state currency ("500 PLN", "1 million euros", "US dollar"). For cryptocurrency or financial instrument use "asset"
- asset: cryptocurrency, stock, ETF, bond, investment commodity, fund, financial instrument (bitcoin, Tesla stock, gold, S&P 500, NFT BAYC)
- law: statute, legal act, regulation, EU directive, code (GDPR, labor code, VAT act)
- nationality: nationality, ethnic, religious or political group (Pole, Catholic, liberal)
- structure: single human-made object: building, bridge, stadium, airport, highway, monument (Eiffel Tower, A4 highway, Stadion Narodowy, Chopin Airport, Palace of Culture)
- substance: vitamin, mineral, drug, active ingredient, supplement, chemical, hormone, enzyme — generic substance name without brand (vitamin C, magnesium, omega-3, creatine, paracetamol, catalase)
- disease: disease, health condition, disorder (diabetes, COVID-19, depression, ADHD, hypertension, insomnia)
- therapy: named therapy, diet, medical procedure, diagnostic test, treatment protocol (chemotherapy, CBT psychotherapy, MRI, ketogenic diet, Mediterranean diet, acupuncture, cataract surgery)
- species: animal/plant species, dog/cat breed, tree (Labrador, oak, honeybee, rose, carp)
- dish: dish, meal, drink, recipe or regional cuisine (rosół, pizza margherita, sushi, latte coffee, Italian cuisine)
- ingredient: culinary ingredient, raw food product, spice, dish component (wheat flour, cream, basil, horseradish, onion, dill)
- discipline: sports discipline, martial art, training type or exercise (football, MMA, crossfit, yoga, squat)
- activity: named practice, process or human activity that is NOT a sports discipline or medical therapy (meditation, intermittent fasting, bread baking, SEO optimization, magnesium supplementation). Yoga/MMA/crossfit → discipline. Psychotherapy/keto diet → therapy
- other: ONLY when entity is semantically important but no type fits clearly. NOT a fallback for uncertainty — if two types fit, pick better one

DO NOT INVENT NEW TYPES. Use "other" only when nothing else fits.

## DISAMBIGUATION RULES

When entity could fit multiple types, follow these tests:

### substance vs therapy
- Generic biochemical compound → substance (vitamin C, magnesium, paracetamol)
- Named treatment/diet/procedure → therapy (chemotherapy, ketogenic diet, MRI)
- Test: "Is it a thing you can hold/measure?" → substance
- Test: "Is it a process/protocol?" → therapy

### discipline vs activity
- Sport/martial art/named training method → discipline (yoga, MMA, crossfit)
- Other human activity → activity (meditation, fasting, baking, SEO)
- Test: "Could you find it in 'Sports' section?" → discipline

### product vs technology
- Consumer-facing product or service → product (iPhone, Netflix, Tesla Model S)
- Programming/protocol/standard/AI model → technology (React, Bluetooth, GPT-4)
- Test: "Could a non-technical person buy/use it directly?" → product

### brand vs organization
- Marketing/branding context, product line → brand (Nike, Apple iPhone line)
- Corporate entity, employer, legal entity → organization (Apple Inc., Nike Corporation)
- Same name can be both depending on context — pick what fits article focus

### location vs structure
- Geographic/administrative area → location (Warsaw, Mazowsze, Vistula)
- Single built object → structure (Stadion Narodowy, A4 highway)
- Test: "Can you draw it on a map as area, or is it a single point?"

### work vs event
- Created creative content → work (book "Lalka", film "Inception", album "Thriller")
- Time-bound happening → event (Cannes Festival, Olympics 2024, Super Bowl)

## INCORRECT VS CORRECT EXAMPLES

❌ Wrong: "vitamin C" → "therapy"
✅ Correct: "vitamin C" → "substance"
(vitamin is a biochemical compound, not a procedure)

❌ Wrong: "yoga" → "activity"
✅ Correct: "yoga" → "discipline"
(yoga is a named physical practice in sports/fitness category)

❌ Wrong: "React" → "product"
✅ Correct: "React" → "technology"
(JavaScript framework, not consumer product)

❌ Wrong: "Eiffel Tower" → "location"
✅ Correct: "Eiffel Tower" → "structure"
(specific built object, not geographic area)

❌ Wrong: "ketogenic diet" → "activity"
✅ Correct: "ketogenic diet" → "therapy"
(named medical/dietary protocol, not generic activity)

❌ Wrong: "bitcoin" → "money"
✅ Correct: "bitcoin" → "asset"
(cryptocurrency is a financial instrument, not state currency)

❌ Wrong: "meditation" → "discipline"
✅ Correct: "meditation" → "activity"
(not a sports discipline; it's a contemplative practice)

## RULES
- Maximum 15 most important entities (not exhaustive list — most relevant to article topic)
- Entity name in nominative singular (lemma) when possible
- Preserve original language of entity names (Polish article → Polish names, English article → English names)
- Detect article language via ISO 639-1 (pl, en, de, es, fr, it, ru, cs, sk, ua, etc.)

## EXAMPLES

### Example 1: Polish cooking article
Input: "Klasyczny rosół na niedzielę. Potrzebujesz kurczaka, marchewki, pietruszki, selera i lubczyku..."
Output:
{
  "category": "Cooking",
  "language": "pl",
  "entities": [
    {"name": "rosół", "type": "dish"},
    {"name": "kurczak", "type": "ingredient"},
    {"name": "marchewka", "type": "ingredient"},
    {"name": "pietruszka", "type": "ingredient"},
    {"name": "seler", "type": "ingredient"},
    {"name": "lubczyk", "type": "ingredient"}
  ]
}

### Example 2: English tech article
Input: "Apple released iPhone 15 with USB-C support, replacing the proprietary Lightning connector after a decade..."
Output:
{
  "category": "IT, New technologies, Computers",
  "language": "en",
  "entities": [
    {"name": "Apple", "type": "organization"},
    {"name": "iPhone 15", "type": "product"},
    {"name": "USB-C", "type": "technology"},
    {"name": "Lightning", "type": "technology"}
  ]
}

### Example 3: Polish health article (tricky entity types)
Input: "Witamina D wspiera odporność. Suplementacja D3 jest zalecana jesienią. W ciężkich niedoborach lekarz może zalecić dietę bogatą w tłuszcze..."
Output:
{
  "category": "Health, Medicine",
  "language": "pl",
  "entities": [
    {"name": "witamina D", "type": "substance"},
    {"name": "witamina D3", "type": "substance"},
    {"name": "suplementacja witaminą D", "type": "activity"}
  ]
}

### Example 4: German finance article
Input: "Die Europäische Zentralbank hat die Zinsen erhöht. Bitcoin reagierte mit einem Kursrückgang..."
Output:
{
  "category": "Finance, Banking and Insurance",
  "language": "de",
  "entities": [
    {"name": "Europäische Zentralbank", "type": "organization"},
    {"name": "bitcoin", "type": "asset"}
  ]
}

### Example 5: Polish sports article (discipline vs activity test)
Input: "Joga to nie tylko ćwiczenia. Medytacja jest częścią praktyki..."
Output:
{
  "category": "Sport, Fitness, Bodybuilding",
  "language": "pl",
  "entities": [
    {"name": "joga", "type": "discipline"},
    {"name": "medytacja", "type": "activity"}
  ]
}
```

## User prompt (Step 1)

```
Analyze the article below and extract structured data:

<article>
{article_text}
</article>
```

## Output (Step 1) — entity layer / pipe note

Save to table/JSONL with `url_hash` key:
```json
{
  "url_hash": "abc123...",
  "category": "Cooking",
  "language": "pl",
  "entities": [...]
}
```

**This becomes pipe note** — universal entity layer reusable for:
- Step 2 (SEO meta generation)
- Knowledge graphs (linking entities across articles)
- Search/recommendation systems
- Future language pipelines (DE, ES, FR, etc.)
- Content analytics

---

# STEP 2: SEO Meta Generation (Language-Aware)

## Goal

Generate SEO meta data in **article's language** (detected in Step 1). Model is **author** of SEO content, not extractor.

## Sampling parameters (Step 2)

**Default (Google defaults):**

```python
SamplingParams(
    temperature=1.0,         # Google default for Gemma 4
    top_p=0.95,              # Google default
    top_k=64,                # Google default
    repetition_penalty=1.0,
    max_tokens=300,
    guided_decoding=GuidedDecodingParams(json=schema_step2),
)
```

**After Phase 3 (A/B test):** empirically chosen best config.

## JSON Schema (Step 2)

```json
{
  "type": "object",
  "properties": {
    "title": {"type": "string", "maxLength": 70},
    "meta_description": {"type": "string", "maxLength": 160},
    "h1": {"type": "string", "maxLength": 100},
    "article_summary": {"type": "string", "maxLength": 400}
  },
  "required": ["title", "meta_description", "h1", "article_summary"]
}
```

## System prompt (Step 2) — full English

```markdown
## ROLE
SEO copywriter creating compelling meta data for articles.

## TASK
Generate SEO meta data in the SAME language as the article.
Output language is specified in the user message — strictly follow it.
Use category and entities provided as context to focus on key topics.
Return ONLY a valid JSON object. No markdown, no extra text.

## STYLE GUIDELINES

### title (max 70 characters)
- Specific, describing article content
- Contains main keyword naturally
- Avoid clickbait ("You won't believe!"), avoid boring ("Article about...")
- Natural phrasing in target language, not literal translation from English SEO templates

### meta_description (140-160 characters)
- 1-2 complete sentences
- Keywords naturally integrated
- Encourages clicking but doesn't promise more than article delivers
- Active voice, informative tone
- AVOID generic CTAs: "Learn more", "Click here", "Find out", "Read more"
- AVOID meta-references: "This article describes...", "In this text you'll find..."

### h1 (max 100 characters)
- Often similar to title, can be stylistically looser
- If article has obvious h1 in text — use it (with minor improvements)
- If not — generate naturally sounding heading
- Same language as article

### article_summary (2-3 sentences, max 400 chars)
- Summary of CONTENT, not marketing
- Concrete: what article discusses, key conclusions
- Don't start with "Article describes..." — start with concrete information
- No bullet points, no lists

## ANTI-PATTERNS

### In title:
- ❌ "Article about X" / "Everything about X" / "X — what to know"
- ❌ Excessive exclamation marks, emojis, special characters
- ❌ Repeating same words

### In meta_description:
- ❌ Generic CTAs: "Learn more!", "Check now!", "Click to see"
- ❌ Repeating title verbatim
- ❌ "This article describes..." / "In this text you'll find..."

### In article_summary:
- ❌ "Article contains...", "Text discusses..." — start with concrete info
- ❌ Lists ("First... second... third...")
- ❌ Repeating meta_description

## EXAMPLES

### Example 1: Polish cooking article (language=pl)
Context: category=Cooking, entities=[rosół, kurczak, marchewka...]
Output:
{
  "title": "Klasyczny rosół na niedzielę – przepis z idealnymi proporcjami",
  "meta_description": "Tradycyjny rosół z kurczaka i włoszczyzny. Sprawdzone proporcje, czas gotowania i wskazówki na klarowny, aromatyczny bulion na niedzielny obiad.",
  "h1": "Klasyczny rosół na niedzielę",
  "article_summary": "Przepis na tradycyjny polski rosół z kurczaka i włoszczyzny. Autor podaje proporcje składników, technikę gotowania na małym ogniu oraz wskazówki na uzyskanie klarownego bulionu."
}

### Example 2: English tech article (language=en)
Context: category=IT, entities=[Apple, iPhone 15, USB-C, Lightning]
Output:
{
  "title": "iPhone 15 Goes USB-C: What Changes and Why It Matters",
  "meta_description": "Apple ditches Lightning for USB-C in iPhone 15. Compatibility with existing accessories, charging speeds, and what users should know about the transition.",
  "h1": "iPhone 15 with USB-C: A Decade of Lightning Ends",
  "article_summary": "Apple replaces Lightning with USB-C in iPhone 15 series after a decade. The change affects accessory compatibility and aligns with EU regulations on charging standards."
}

### Example 3: Polish health article (language=pl)
Context: category=Health, entities=[witamina D, suplementacja]
Output:
{
  "title": "Witamina D a odporność – jak uniknąć niedoboru jesienią",
  "meta_description": "Niedobór witaminy D dotyka 70% Polaków jesienią. Poznaj objawy, sposoby suplementacji i naturalne źródła – kompletny poradnik od dietetyka.",
  "h1": "Witamina D i odporność: kompletny przewodnik",
  "article_summary": "Niedobór witaminy D dotyka większość Polaków w okresie jesiennym i zimowym. Tekst omawia zalecane dawki suplementacji oraz objawy, na które warto zwrócić uwagę."
}

### Example 4: German finance article (language=de)
Context: category=Finance, entities=[ECB, bitcoin]
Output:
{
  "title": "EZB erhöht Zinsen: Auswirkungen auf Bitcoin und Märkte",
  "meta_description": "Die Europäische Zentralbank verschärft ihre Geldpolitik. Wie Bitcoin reagiert hat und was Anleger jetzt über die neuen Zinsen wissen müssen.",
  "h1": "Zinserhöhung der EZB drückt Bitcoin-Kurs",
  "article_summary": "Die Europäische Zentralbank hat die Zinsen erneut erhöht, woraufhin Bitcoin einen deutlichen Kursrückgang verzeichnete. Die Analyse betrachtet die Korrelation zwischen Geldpolitik und Krypto-Märkten."
}
```

## User prompt (Step 2)

```
Generate SEO meta data in language: {detected_language}

Category: {category}
Key entities: {entities_summary}

<article>
{article_text}
</article>
```

`{entities_summary}` is comma-separated list of entity names, e.g., `"witamina D, witamina D3, suplementacja witaminą D"`.

---

# Pipeline Implementation

## Architecture

```
┌─────────────────────────────────────┐
│  21M URLs queue (Redis/SQS/SQLite)  │
└──────────────┬──────────────────────┘
               │
               ▼
       ┌───────────────┐
       │  HTML cleanup │  trafilatura/boilerpy3
       │  (12k → ~6k)  │
       └───────┬───────┘
               │
               ▼
    ┌──────────────────────┐
    │  Step 1: Entity      │  vLLM, Google defaults, guided_json
    │  + language detect   │  output → pipe note
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Entity Layer (DB)   │  url_hash, language, category, entities
    │  REUSABLE            │  pipe note — long-term value
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Step 2: SEO Meta    │  vLLM, Google defaults, guided_json
    │  (language-aware)    │  contextualized from entity layer
    └──────────┬───────────┘
               │
               ▼
       ┌───────────────┐
       │  Final output │  Full record {meta + entities}
       └───────────────┘
```

## Execution strategy

### Option A: Sequential (simpler, recommended)

1. Full Step 1 run for all 21M URLs (~6-8 days on 1× 5090)
2. After Step 1 completes, full Step 2 run (~6-8 days on 1× 5090)
3. Total: ~12-15 days

**Pros:** simple pipeline, easy resume after crashes, entity layer "complete" before step 2

### Option B: Pipelined (faster, more complex)

1. Step 1 and Step 2 run in parallel on **different GPUs**
2. Step 1 leads Step 2 by batch buffer (e.g., 10k URLs)
3. Step 2 reads from queue of ready entity records

**Pros:** total time ~7-10 days
**Cons:** two GPUs, more orchestration, harder to debug

**Recommendation:** Option A.

---

# Code references

## vLLM offline batch (Step 1)

```python
from vllm import LLM, SamplingParams
from vllm.sampling_params import GuidedDecodingParams
import json
import hashlib

with open("schema_step1.json") as f:
    schema_step1 = json.load(f)

llm = LLM(
    model="/workspace/model",
    quantization="modelopt",
    kv_cache_dtype="fp8",
    max_model_len=16384,
    max_num_seqs=16,
    gpu_memory_utilization=0.92,
    enable_prefix_caching=True,
    dtype="auto",
)

guided_step1 = GuidedDecodingParams(json=schema_step1)
params_step1 = SamplingParams(
    temperature=1.0, top_p=0.95, top_k=64,  # Google defaults
    repetition_penalty=1.0, max_tokens=400,
    guided_decoding=guided_step1,
)

prompts = [build_step1_prompt(clean_article) for clean_article in batch]
outputs = llm.generate(prompts, params_step1)

for url, output in zip(batch_urls, outputs):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    entity_data = json.loads(output.outputs[0].text)
    save_to_entity_layer(url_hash, entity_data)
```

## vLLM offline batch (Step 2)

```python
with open("schema_step2.json") as f:
    schema_step2 = json.load(f)

guided_step2 = GuidedDecodingParams(json=schema_step2)
params_step2 = SamplingParams(
    temperature=1.0, top_p=0.95, top_k=64,  # Google defaults
    repetition_penalty=1.0, max_tokens=300,
    guided_decoding=guided_step2,
)

for batch in batches:
    entity_records = [load_entity_record(url_hash) for url_hash in batch]
    clean_articles = [load_clean_article(url_hash) for url_hash in batch]
    
    prompts = [
        build_step2_prompt(
            article=clean_articles[i],
            language=entity_records[i]["language"],
            category=entity_records[i]["category"],
            entities=entity_records[i]["entities"]
        )
        for i in range(len(batch))
    ]
    
    outputs = llm.generate(prompts, params_step2)
    
    for url_hash, output in zip(batch, outputs):
        meta_data = json.loads(output.outputs[0].text)
        save_final_record(url_hash, meta_data)
```

## HTML cleanup helper

```python
import trafilatura

def clean_article(html_or_url, is_url=False):
    if is_url:
        downloaded = trafilatura.fetch_url(html_or_url)
    else:
        downloaded = html_or_url
    
    clean = trafilatura.extract(
        downloaded,
        output_format='markdown',
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    
    return clean or ""
```

## Build prompts helpers

```python
def build_step1_prompt(article_text):
    return f"""Analyze the article below and extract structured data:

<article>
{article_text}
</article>"""


def build_step2_prompt(article, language, category, entities):
    entities_summary = ", ".join([e["name"] for e in entities[:10]])
    return f"""Generate SEO meta data in language: {language}

Category: {category}
Key entities: {entities_summary}

<article>
{article}
</article>"""
```

---

# Hardware and infrastructure

## Model: Gemma 4 26B A4B (MoE)

**Specifications:**
- 25.2B total params, 3.8B active per token (MoE)
- 256K context window, sliding window 1024
- Pretraining: 140+ languages, instruction tuning: 35+
- Apache 2.0 license

**Selection rationale:**
- MMMLU 86.3% (vs E4B 76.6%) — important for copywriting in non-EN languages
- MoE 3.8B active = inference speed of 4B model with much higher quality
- Native Polish + 140 other languages = future-proof for multilingual expansion
- Native function calling

**Backup model:** Qwen 3 32B (if Gemma 4 fails quality-wise)

## Quantization: NVFP4 + KV cache FP8

**Configuration:**
- MoE experts (~91% params): NVFP4 (hardware accelerated on Blackwell)
- Self-attention: BF16 (stays in full precision)
- KV cache: FP8 (2x more batch vs BF16)
- Activations (compute): BF16

**Ready-made quants:**
- **`nvidia/Gemma-4-26B-A4B-NVFP4`** — preferred for 5090 prod
- **`bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4`** — tested on DGX Spark

## GPU

**Spark (dev/staging):** GB10, sm_121, no native FP4 → Marlin fallback (~30% slower)
**5090 (prod):** sm_120, native FP4 (full speed)

**VRAM math:**
- Spark 128GB unified: fits everything with reserve
- 5090 32GB: model ~16.5GB + KV cache ~15.5GB → batch 12-16 sequences of 12k input

---

# Pitfalls to avoid

## Architecture
1. **One-step extraction + generation** — compromise that degrades both functions
2. **No language detection** — Step 2 needs to know what language to write in
3. **Polish-specific prompts** — exclude future expansion to other languages
4. **No entity layer as pipe note** — wasting long-term reusability value

## Sampling and prompting
5. **Low temperature for Gemma 4 without proof** — model is calibrated for 1.0, going lower usually DEGRADES quality. With guided_json low temp is unnecessary.
6. **rep_penalty 1.2 for JSON** — breaks repeating keys
7. **No few-shot** — especially for Step 2 (style transfer needs examples)
8. **Generic entity types without enum** — model hallucinates ad-hoc types
9. **Thinking mode enabled** — Gemma 4 generates reasoning trace, unnecessary
10. **Retry threats in prompt** — fiction, model doesn't enforce them, wastes tokens
11. **Descriptions in user prompt instead of system** — loses prefix caching, +30-40B tokens for 21M URLs
12. **Relying on JSON Schema `description` field** — xgrammar doesn't pass it to model, dead weight

## Hardware and quantization
13. **3090/4090/L40S for 26B A4B** — no native FP4, batch too small for 12k input
14. **Downloading `google/gemma-4-26B-A4B-it`** — that's BF16 50GB, download NVFP4 quant
15. **Extrapolating Spark → 5090** — Spark sm_121 (Marlin) ≠ 5090 sm_120 (native FP4)

## Pipeline and input
16. **Raw HTML in `{article_text}`** — boilerplate eats tokens and degrades entity quality
17. **No idempotence at 21M scale** — crashes will happen, without hash-based dedupe = duplicates/loss
18. **No prefix caching** — without that flag system prompt counts 21M times

## Models and languages
19. **Nemotron 3 for Polish** — no Polish in supported languages
20. **GPT-OSS for Polish** — EN-first, weak non-English
21. **Reasoning models for extraction** — even with thinking off they have problems with structured output

---

# Decision status

| Element | Decision | Status |
|---|---|---|
| **Architecture** | **Two-step (extraction + generation)** | **Final, validate in Phase 2** |
| **Step 1 language** | **Universal (English system prompt)** | **Final** |
| **Step 2 language** | **Language-aware (with language detection from Step 1)** | **Final** |
| **Entity layer as pipe note** | **YES (reusable)** | **Final** |
| **Entity types** | **English universal (person, organization, ...)** | **Final** |
| Model | Gemma 4 26B A4B NVFP4 | Final |
| **Sampling baseline** | **Google defaults: temp 1.0, top_p 0.95, top_k 64** | **Final, A/B test in Phase 3** |
| Step 1 temperature | 1.0 (Google default) | A/B test 0.3/0.7/1.0 |
| Step 2 temperature | 1.0 (Google default) | A/B test 0.5/0.8/1.0 |
| Quantization weights | NVFP4 | Final |
| Quantization KV cache | FP8 | Final |
| GPU dev/staging | DGX Spark (sm_121) | Final |
| GPU prod | RTX 5090 (1× or 2×) | Decision after Phase 8 |
| Decoding | guided_json (xgrammar) | Final |
| Thinking mode | OFF | Final |
| **HTML cleanup** | **trafilatura/boilerpy3** | **Final, validate in Phase 1** |
| Execution strategy | Option A (sequential) | Tentative |
| Custom calibration | After first 100k if quality < target | Optional |
| Fine-tuning | NO — guided_json + few-shot suffices | Final |

---

# Plan summary

## Stage A — DGX Spark (development, ~1 week)

| Phase | Time | Goal |
|---|---|---|
| 0: vLLM setup | 1 day | vLLM with Gemma 4 NVFP4 works on Spark |
| 1: HTML cleanup | 1 day | trafilatura validated, token reduction measured |
| 2: Two-step vs one-step | 1-2 days | Empirical quality comparison |
| 3: A/B sampling parameters | half day | Empirical temperature choice (Google default vs lower) |
| 4: Prompt iteration | 1-2 days | Refine prompts based on observations |
| 5: End-to-end on 500-1000 URLs | 1 day | Full validation before RTX |
| 6: Decision gate | — | "Ready for production" decision |

## Stage B — RunPod RTX 5090 (production, ~1-2 weeks)

| Phase | Time | Goal |
|---|---|---|
| 7: RunPod setup | half day | Network Volume + model + code ready |
| 8: Performance test | 1-2h | Measure throughput, decide 1× or 2× 5090 |
| 9: Production run | 6-15 days | Full prod run for 21M URLs |

---

# References

**Models:**
- `nvidia/Gemma-4-26B-A4B-NVFP4` — preferred for 5090 prod
- `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` — tested on Spark
- `google/gemma-4-26B-A4B-it` — BF16 original (for custom quantization)

**Tools:**
- vLLM: https://docs.vllm.ai
- trafilatura: https://trafilatura.readthedocs.io
- xgrammar: https://github.com/mlc-ai/xgrammar
- guided decoding in vLLM: https://docs.vllm.ai/en/latest/features/structured_outputs.html

**Benchmarks:**
- DGX Spark + Gemma 4 26B A4B NVFP4: ~52 tok/s single stream (Marlin fallback)
- 5090 + 4B model: ~6400 t/s aggregated (Phi-3-mini, batch 1024)

---

# Changelog

**v4 (current):**
- ✨ All prompts converted to English (~30% fewer tokens than Polish)
- ✨ Universal English entity types (person, organization, technology, etc.)
- ✨ Disambiguation rules section in Step 1 prompt
- ✨ Negative examples ("incorrect vs correct") for boundary cases
- ✨ Explanation of how guided_json + enum actually works (descriptions stay in system prompt)
- 📝 Entity type mapping table (PL → EN) for migration reference
- 📝 Additional disambiguation rules: brand vs organization, location vs structure, work vs event

**v3:**
- ✨ Sampling: Google defaults (temp 1.0, top_p 0.95, top_k 64) as baseline for both steps
- ✨ Workflow split into **Stage A (Spark dev)** and **Stage B (RTX prod)**
- ✨ Phase 3 dedicated to A/B sampling parameters test with empirical decision
- ✨ Decision gate after Phase 6 — clear readiness criteria for prod

**v2:**
- ✨ Two-step architecture (universal entities + language-aware meta)
- ✨ Entity layer as pipe note (long-term reusable)
- ✨ Language detection in Step 1 → forwarded to Step 2
- ✨ Universal English prompts (language-agnostic)
- ✨ Few-shot examples for both steps (multi-language)
- ✨ Anti-patterns for SEO copywriting
- ✨ HTML cleanup as mandatory preprocessing

**v1:**
- One-step pipeline
- Polish-specific prompts
- Single sampling configuration