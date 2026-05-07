# Plan na następną sesję

**Stan na koniec poprzedniej sesji (2026-05-07):**
- ✅ Phase 0–4 ukończone (vLLM setup, HTML cleanup, two-step impl, A/B sampling, prompt evolution v1→v5 + Azure NER + metadata)
- ✅ Schema: Azure NER 51 typów + category + strength + metadata
- ✅ Pełen E2E orchestrator: `scripts/run_full.py`
- ⏳ Phase 5 (E2E na 155 URL) — gotowe do uruchomienia

**Repo:** https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
**Sesja podsumowanie:** [`SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md`](../SESSIONS_SUMMARY/2026-05-07_two_step_pipeline.md)

---

## Krok 0 — Onboarding (3 min)

Przed jakąkolwiek pracą:

```bash
cd /home/spark001/Spark-testy/mateusz-g-two-step-vllm
git pull
git log --oneline -10                      # zobacz ostatnie commity
cat DECISIONS.md | head -100               # przypomnij decyzje
cat PLAN.md                                # status faz
```

**Czytaj w tej kolejności:**
1. `CLAUDE.md` — wskazówki ogólne dla Claude
2. `PLAN.md` — gdzie jesteśmy w timeline
3. `DECISIONS.md` — dlaczego coś jest tak (a nie inaczej)
4. `INSTRUCTIONS_FROM_CLAUDE.md` — pełna spec (źródło prawdy)

---

## Krok 1 — Sprawdź czy vLLM działa

```bash
docker ps --filter name=vllm-gemma4
curl -sS http://localhost:8001/v1/models | python3 -m json.tool
```

**Jeśli nie działa:**
```bash
bash scripts/start_vllm.sh
docker logs -f vllm-gemma4   # czekaj na "Application startup complete" (~2 min)
bash scripts/smoke_test.sh
```

### ⚠️ GOTCHA: Port 8001, NIE 8000
Port 8000 zajęty przez `open-terminal` na Sparku. Wszystkie skrypty defaultują na 8001.

### ⚠️ GOTCHA: Ollama może blokować GPU
Jeśli `nvidia-smi` pokazuje proces `ollama`, zatrzymaj go przed startem vLLM. Inaczej `--gpu-memory-utilization 0.85` zderzy się z Ollama.

```bash
# zatrzymanie Ollama jeśli aktywne
sudo systemctl stop ollama
# lub
ollama stop <model_name>
```

### ⚠️ GOTCHA: GPU memory.used [N/A] w nvidia-smi
To znany bug Sparka (unified memory CPU+GPU). Realne KV cache usage tylko przez vLLM `/metrics`:

```bash
curl -sS http://localhost:8001/metrics | grep -E "kv_cache_usage|num_requests" | grep -v ^#
```

---

## Krok 2 — Phase 5: E2E na 155 URL (jeśli jeszcze nie zrobione)

**Sprawdź:**
```bash
ls -la final_result/ 2>&1 | head
```

**Jeśli pusto — uruchom:**
```bash
# w tmux!
python3 -u scripts/run_full.py --out-dir final_result --limit 0 --concurrency 8
```

Czas: **~10-15 min**.

**Output:**
- `final_result/entity_layer.jsonl` (155 wierszy Step 1)
- `final_result/final.jsonl` (155 wierszy Step 2)
- `final_result/summary.md` (raport + 15 sample'i)
- `final_result/metrics_delta.txt` (cache hit rate dla runa)
- `final_result/pipeline.log`

**Walidacja:**
```bash
wc -l final_result/*.jsonl
python3 -c "
import json
ok = sum(1 for l in open('final_result/entity_layer.jsonl') if json.loads(l).get('ok'))
total = sum(1 for _ in open('final_result/entity_layer.jsonl'))
print(f'Step 1: {ok}/{total} OK')
"
cat final_result/metrics_delta.txt
```

**Cel jakości:** ≥99% OK rate, cache hit rate ≥70%.

---

## Krok 3 — Phase 6: Decision Gate (przed migracją na 5090)

Wymagania z INSTRUCTIONS sekcja "Phase 6":
- [ ] Two-step pipeline udowodniony ✅
- [ ] HTML cleanup zwalidowany ✅
- [ ] Sampling dobrany empirycznie ✅
- [ ] Prompt stabilny (>3 wersje testowane) ✅ (mamy v5)
- [ ] E2E na 500-1000 URL bez crashów (mamy 155, dev/staging dataset)
- [ ] Quality "good enough" w eyeball assessment

**Action:** review `final_result/summary.md` ręcznie. Jeśli OK → ready dla 5090. Jeśli edge cases — Phase 4 v6.

---

## Krok 4 — Storage decision (przed Phase 7)

**Decyzja architekturalna na 21M URL.** Czytaj: [`docs/storage_21m_urls.md`](../docs/storage_21m_urls.md).

**Opcje:**
- A. SQLite + sqlite-vec (research/dev)
- B. PostgreSQL + pgvector (production)
- C. Parquet + DuckDB + Qdrant (hybrid, ML-friendly)

**Rekomendacja etapowa:**
- Phase 7 (RunPod 5090 dev): **SQLite + sqlite-vec** (lokalnie na pod)
- Phase 8-9 (prod): wybór po performance test (PostgreSQL lub Hybrid)

**Action items dla Phase 7:**
1. Decyzja embedding model (BGE-M3 vs jina-embeddings-v3 vs multilingual-e5)
2. Decyzja: per-article czy per-entity embeddingi (lub oba w hierarchii)
3. Implementacja `scripts/etl_load.py` — JSONL → SQLite/Postgres
4. Wybór quantization wektorów (FP16 vs FP8 vs Matryoshka)

---

## Krok 5 — Phase 7: RunPod RTX 5090 setup

Zgodnie z INSTRUCTIONS sekcja "Phase 7":

```bash
# 1. Network Volume 100 GB w DC z 5090 (Secure Cloud)
# 2. Spin up cheapest pod (3090, ~$0.20/h, 1h) tylko do setupu

# 3. Pobranie modelu prod (nvidia, NIE bg-digitalservices!)
hf auth login
mkdir -p /workspace/model
hf download nvidia/Gemma-4-26B-A4B-NVFP4 --local-dir /workspace/model

# 4. Skopiowanie kodu
git clone https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b /workspace/code
cd /workspace/code
pip install -r requirements.txt

# 5. Test 100 URL e2e (przygotuj próbkę URL z websites/)
python3 -u scripts/run_full.py --out-dir test_runpod --limit 100 --concurrency 16

# 6. Stop pod, volume zostaje
runpodctl stop pod
```

### ⚠️ GOTCHA: Inny vLLM image dla 5090
Spark używa `vllm/vllm-openai:gemma4-cu130` (custom z patchem sm_121).
**5090 użyje** `vllm/vllm-openai:latest` (sm_120, natywne FP4).

### ⚠️ GOTCHA: Wycofać `--moe-backend marlin`
Marlin to fallback dla sm_121. Na 5090 (sm_120) natywne FP4 → **usuń tę flagę**.

### ⚠️ GOTCHA: `--max-num-seqs` zwiększyć
Na 5090 z natywnym FP4: `--max-num-seqs 32` (było 8 na Sparku).

### ⚠️ GOTCHA: NIE używać `--reasoning-parser gemma4`
Wciąż obowiązuje (vLLM issue #39130 — kombinacja z `enable_thinking=false` cicho wyłącza xgrammar).

---

## Krok 6 — Phase 8: Performance test

Cel z INSTRUCTIONS: 5000 URL na 1× 5090.

**Targets:**
- Step 1 throughput >2000 t/s aggregated
- Step 2 throughput >2000 t/s aggregated
- Prefix cache hit rate >70%
- E2E <2s per URL amortized
- VRAM utilization <95%
- Quality consistency vs Spark

**Concurrency sweep:** `--concurrency` ∈ {16, 32, 64} z `--max-num-seqs` ∈ {16, 32, 64}.

---

## Krok 7 — Phase 9: Production run

Po pozytywnym Phase 8:
- 1× lub 2× 5090 (decyzja po Phase 8 perf test)
- Strategy: Option A (sequential Step 1 → Step 2) — prościej, easier resume
- Idempotent writes via `url_hash` skip (mamy już w `JsonlReporter.load_existing_hashes()`)
- Checkpoints co 1000 URL → backup do S3/GCS
- Failed queue dla URL crashujących (już mamy `ok: false` records)
- Idle timeout dla auto-stop po zakończeniu

**Estymata:** 12-15 dni / ~$200-280 dla 1× 5090. 6-8 dni / ~$200-300 dla 2× 5090.

---

## ⚠️ GOTCHAS — kompletna lista

### Kod / pipeline
- **xgrammar nie wymusza per-type metadata schema** — opcjonalne pola pozwalają mieszać. Mamy `_clean_metadata()` jako safety net w `lib/pipeline.py`.
- **`maxItems` cap nie jest dobrym pomysłem na początek** — model wyciąga median 23 encji (po zdjęciu cap 15). Trzymaj się D11.5 reguły "no premature constraints".
- **Niska temperatura NIE daje determinizmu** na Marlin sm_121 (D13). Idempotencja przez `url_hash` skip.
- **Pierwszy request po starcie vLLM** ma `prompt_tokens_details: null` (cache pusty). To bug build'u `gemma4-cu130`. Cache stats czytaj z `/metrics`, nie response.
- **`tokenizer_config.json`** w `bg-digitalservices` quancie ma bug (`'list' object has no attribute 'keys'`). Use `tokenizers` (Rust) z `tokenizer.json` bezpośrednio (D8).

### Sampling
- `repetition_penalty=1.2` **łamie** powtarzające się klucze JSON. Trzymaj na 1.0.
- Step 2 sampling `temp=1.0` ma ~1% zapętleń. Use `temp=0.8` (D12).

### vLLM
- `--reasoning-parser gemma4` + `enable_thinking=false` = xgrammar disabled (vLLM #39130). NIE łącz tych flag.
- Port 8000 zajęty na Sparku → **8001**.
- `--moe-backend marlin` tylko na sm_121 (Spark). Na 5090 wycofać.
- `--default-chat-template-kwargs '{"enable_thinking": false}'` server-side + per-request `chat_template_kwargs` w body — both for safety.

### Schema
- Azure types są **case-sensitive** (`Person`, NIE `person`).
- Każdy typ Azure ma swój metadata schema — używaj **per-type whitelist** (`METADATA_FIELDS_BY_TYPE` w `lib/pipeline.py`).
- `Currency` to państwowe pieniądze. Crypto/akcje → `Product`.
- `Information` zawiera kalorie? **NIE** — kcal idą do `Number`. Information data-size to KB/MB/GB only.
- `Temporal` w Azure NIE ma metadata (zawsze omit dla tego typu).
- `unit: "Unspecified"` to fake — jeśli brak konkretnej jednostki, omit metadata field.

### Truncate
- `MAX_ARTICLE_TOKENS=20000` jest hojny (max obserwowany 5979) — nie ma rzeczywistego ucinania na obecnych danych. Safety net dla 21M URL.
- `TEXT_TRUNCATE_LIMIT=80000` znaków to pierwszy gate (przed tokenizacją).

### Prefix caching
- System prompt 8084 tokenów (v5) → 1× pełny prefill, potem cache hit ~99%.
- Hit rate per-run dla **różnych** URL: ~70-72% (cache zachowuje system prompt + few-shot).
- Hit rate per-run dla **tych samych** URL × różne sampling configi: ~99% (Phase 3 obserwacja).

### Git / dane
- `result/`, `final_result/`, `models/`, `__pycache__/` w `.gitignore`. Output lokalnie.
- `INSTRUCTIONS_FROM_CLAUDE.md` jest źródłem prawdy — DECISIONS.md udokumentował **odchylenia** od niej (np. D3 wyłącza `--reasoning-parser gemma4` mimo że INSTRUCTIONS Phase 8 to sugeruje).

---

## Tooling cheat sheet

```bash
# Diagnostyka cache
python3 scripts/snapshot_metrics.py before > /tmp/before.txt
# ... do work ...
python3 scripts/snapshot_metrics.py after > /tmp/after.txt
python3 scripts/snapshot_metrics.py diff /tmp/before.txt /tmp/after.txt

# Wskazaniku jakości encji per typ
python3 scripts/analyze_entity_quality.py --top 30

# Porównaj wersje promptów (po zmianie)
python3 scripts/compare_prompt_versions.py --v1 result/entity_layer_v1.jsonl --v2 result/entity_layer.jsonl

# A/B sampling (jeśli kiedyś będziemy chcieli zmienić)
python3 scripts/ab_sampling.py --step 1 --limit 100 --concurrency 8

# Pojedyncze ID URL przez API
python3 -c "
from lib.data_loader import load_articles
from lib.pipeline import process_step1
from lib.vllm_client import VLLMClient
from lib.prompt_loader import load_system_prompt, load_schema
from lib.config import VLLM_BASE_URL, VLLM_MODEL, MAX_TOKENS_STEP1, SAMPLING_STEP1
arts = load_articles('websites', limit=1)
client = VLLMClient(VLLM_BASE_URL, VLLM_MODEL)
sys_prompt = load_system_prompt('step1_system')
schema = load_schema('schema_step1')
out = process_step1(client, sys_prompt, schema, arts[0], MAX_TOKENS_STEP1, SAMPLING_STEP1)
import json; print(json.dumps(out, indent=2, ensure_ascii=False))
"
```

---

## Co możemy też zrobić

(Niska priorytet, jeśli zostanie czas)

- [ ] **Streamlit dashboard** dla wizualizacji encji per artykuł, top entities globalnie, kategorie, strength/weak balance
- [ ] **Prompt v6** — eliminacja jeszcze niewykrytych edge cases po Phase 5 review
- [ ] **Few-shot iteration** — dodać przykłady dla rzadszych typów (NaturalEvent, IpAddress, SetTemporal)
- [ ] **Multilingual test** — wziąć kilka angielskich/niemieckich artykułów (jeśli mamy) żeby zwalidować language detection
- [ ] **Benchmark vs Ollama** (na tym samym sprzęcie) — czy vLLM jest mocno szybszy?
- [ ] **Model swap test** — ten sam pipeline z Qwen 3 32B jako backup (INSTRUCTIONS sekcja "Backup model")

---

## Otwarte pytania (do dyskusji w nowej sesji)

1. **Embeddingi w pipeline (Step 3) czy osobny ETL?**
   - Pro Step 3: pojedynczy run, embeddingi gotowe w pipe note
   - Pro ETL: można re-embedować z innym modelem bez re-runa LLM (10× tańsze)
   - **Sugestia:** osobny ETL.

2. **Per-article czy per-entity embeddingi?**
   - Per-article (~30 GB) — szybkie semantic search
   - Per-entity (~700 GB) — entity linking, knowledge graph
   - **Sugestia:** oba w hierarchii (article shortlist → entity rerank).

3. **Storage final dla 21M URL** — A vs B vs C (z `docs/storage_21m_urls.md`)?
   - **Sugestia po Phase 5:** SQLite dev → Postgres+pgvector prod.

4. **Czy zostawiamy backup wersji promptów w repo?**
   - 5 wersji × ~17k chars = ~85 KB tekstu. Mało. **Sugestia:** zostawić, audit trail.

5. **Czy `INSTRUCTIONS_FROM_CLAUDE.md` aktualizujemy o odchylenia (D3, D12, D14, D15)?**
   - Pro update: spójność z faktycznym kodem
   - Pro nie-update: INSTRUCTIONS to "v4 spec", DECISIONS to "actual reality"
   - **Sugestia:** zostawić INSTRUCTIONS jako spec, DECISIONS jako delta. Może dodać disclaimer w INSTRUCTIONS na początku.

---

## Punkty kontrolne

Po każdej fazie:
1. ✅ Wszystkie testy zielone
2. ✅ DECISIONS.md zaktualizowany (jeśli była nietrywialna decyzja)
3. ✅ PLAN.md i TODO.md aktualne
4. ✅ Commit + push (clean working tree przed kolejną fazą)
5. ✅ Eyeball check 5-10 sample wyników (czy nie ma regresji vs poprzednia faza)

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- vLLM Gemma 4 image: https://catalog.ngc.nvidia.com/orgs/nvidia/containers/vllm
- Azure NER taxonomy: https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/named-entity-categories
- Azure entity metadata: https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/entity-metadata
- BGE-M3 embedding: https://huggingface.co/BAAI/bge-m3
- pgvector: https://github.com/pgvector/pgvector
- sqlite-vec: https://github.com/asg017/sqlite-vec
