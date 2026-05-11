# 2026-05-11 — embedding runtime fixes + pierwszy pełen embed run

## Kontekst

Po setupie z poprzedniej sesji (D30, dual-container orchestrator Gemma + Qwen embed)
przy próbie startu okazało się że kontener Qwen pada w trzech różnych miejscach.
Sesja: debugowanie, fixy, pierwszy pełen embed run 22 582 docs.

## 3 fixy w `scripts/start_vllm_llm_plus_embedding.py`

### 1. Entrypoint NVIDIA obrazu

`nvcr.io/nvidia/vllm:26.02-py3` używa `/opt/nvidia/nvidia_entrypoint.sh` jako entrypoint,
który robi `exec "$@"`. Pierwszy argument w naszej komendzie to `--model`, więc:

```
/opt/nvidia/nvidia_entrypoint.sh: line 55: exec: --: invalid option
```

Obraz `vllm/vllm-openai` ma wbudowany ENTRYPOINT na `vllm serve`, ale obraz NVIDIA nie.

**Fix:** jawnie podać `vllm serve /model ...` po nazwie obrazu.

### 2. `--task embed` → `--runner pooling`

```
vllm: error: unrecognized arguments: --task embed
```

W vLLM 0.15.1 flaga `--task` została wymieniona na `--runner`. Dla embedderów:
`--runner pooling` (auto-detekcja z config.json modelu Qwen3-Embedding też działa,
ale lepiej jawnie).

### 3. Budżet pamięci dla Qwen

```
Available KV cache memory: -4.73 GiB
ValueError: No available memory for the cache blocks
```

Założenie z poprzedniej sesji: `GPU_MEM_LLM=0.60 + GPU_MEM_EMB=0.20 = 0.80` z 121 GB.
Problem: **`gpu_memory_utilization` w vLLM 0.15 liczy się względem WOLNEJ pamięci przy
starcie kontenera, nie totalu**. Gemma startuje pierwsza, alokuje swoje 73 GB, więc gdy
Qwen startuje to widzi ~48 GB wolne i 0.20 z tego to ~10 GB — za mało po doliczeniu
torch.compile cache i activations.

**Fix krok 1:** podniosłem `GPU_MEM_EMB` do **0.30**. Available KV cache: 1.03 GiB.

**Fix krok 2:** wciąż za mało dla `max_model_len=8192` (potrzeba 1.12 GiB KV). Obniżyłem
`EMB_MAX_LEN` do **4096** — KV potrzebuje 2× mniej, mieści się z zapasem.

Artykuły rzadko mają >4k tokenów, a dla dłuższych Qwen3-Embedding i tak chunkuje
wewnętrznie.

## Smoke test: `scripts/smoke_test_embedding.sh`

Wzorowany na `smoke_test.sh` (Gemma). Sprawdza:
- `/v1/models` odpowiada
- single embed → `dim=2560` (zgodne ze spec Qwen3-Embedding-4B)
- batch embed (3 inputy jednym requestem) → manifest tokens usage
- **cross-lingual cosine sanity**: `cos("witamina D wspiera odporność" PL, "vitamin D
  supports immunity" EN) = 0.8645` vs `cos(PL, "kupiłem nowy samochód" PL) = 0.4670`.
  Asercja `sim_pl_en > sim_pl_pl` — jakby się dim/normalizacja sypała, ten test by
  to wykrył.

## `scripts/embed_articles.py` — drobny fix

Default `--model` był `Qwen/Qwen3-Embedding-0.6B`, a my startujemy 4B. vLLM ignoruje
nazwę modelu w requeście (bierze załadowany), więc requesty działały, ale `meta.json`
zapisywał błędną nazwę. Default poprawiony na `Qwen/Qwen3-Embedding-4B`.

## Pierwszy pełen embed run

```bash
python3 scripts/embed_articles.py --out runs/embed_v1_full --batch-size 64 --concurrency 8
```

- **Source:** `final_results/2026-05-09_00-21-48__spo_v1_mns32_full/final.jsonl`
- **Eligible:** 22 582 (z 25 667; junk=3085 odrzuconych)
- **Throughput:** ~56 docs/s (batch=64, conc=8) — wąskim gardłem serwer, nie klient
  (test z conc=4 dał 54 docs/s)
- **Wall:** ~7 min
- **Output:** `runs/embed_v1_full/{embeddings.npy [22582, 2560], manifest.jsonl, meta.json}`
- **doc_text format:** `{h1}\n{summary}\n{strong ∪ central entities, deduped}`

`runs/` dodany do `.gitignore` — embeddings.npy ~230 MB, nie commitujemy.

## Następne kroki

Mając wektory można odpalać HDBSCAN clustering (umap → hdbscan stack), porównać klastry
domenowe vs semantyczne, ocenić czy `doc_text` z entities pomaga vs sam summary.
Plan na kolejną sesję.

## Pliki zmienione

- `scripts/start_vllm_llm_plus_embedding.py` — `vllm serve` + `--runner pooling` +
  GPU_MEM_EMB 0.20→0.30 + EMB_MAX_LEN 8192→4096
- `scripts/embed_articles.py` — default model 0.6B → 4B
- **nowy** `scripts/smoke_test_embedding.sh`
- `.gitignore` — dorzucone `runs/`
- `CHANGELOG.md` — wpis 2026-05-11 (13:00)
