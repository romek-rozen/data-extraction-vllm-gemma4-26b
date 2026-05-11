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

## Throughput ceiling hunt — 54 docs/s sufit

Po pierwszym pełnym runie (51.67 docs/s) zaczęliśmy szukać prawdziwego sufitu.
Sweep ustawień klienta:

| batch | conc | docs/s |
|---|---:|---:|
| 32 | 4 | 54.0 |
| 32 | 8 | 56.0 |
| 64 | 8 | 56.0 |
| 128 | 8 | 55.0 |
| 256 | 32 | 54.2 |
| **full 22582** (256/32) | | **53.7** |

**Sufit potwierdzony: ~54 docs/s**, niezależnie od ustawień klienta. Dodatkowe
zmiany serwera, które okazały się NIE-bottleneckami:
- `--max-num-batched-tokens 2048 → 8192` (default → custom): rate bez zmian
- `--max-num-seqs 256` (jawnie, default = 256): bez zmian

**Logi serwera ujawniają obraz:**
```
Avg prompt throughput: 9001-9837 tokens/s
Running: 47 reqs (z max_num_seqs=256)
Waiting: ~8000 reqs (klient zalewa kolejkę — i tak nic to nie daje)
GPU KV cache usage: 15.5%
```

- GPU compute: ~75 TFLOPS z 250 peak bf16 = **30% utilization**
- KV cache: 15% z dostępnego = **niewykorzystany**
- Scheduler: 47 z 256 max_num_seqs = **niewykorzystany**

**Wąskie gardło to memory bandwidth + brak full cudagraphs dla pooling** (vLLM
forced PIECEWISE — "Pooling models do not support full cudagraphs").

## FP8 test — chroma-core/Qwen3-Embedding-4B-FP8-Dynamic

Hipoteza: skoro memory-bandwidth-bound, fp8 → ~2× throughput.

Sprawdziłem listę dostępnych fp8 quantów na HF — Qwen oficjalnie nie publikuje
fp8 embeddera, są tylko community quants. Wybrałem `chroma-core/Qwen3-Embedding-4B-FP8-Dynamic`
(compressed-tensors format, vLLM 0.15 wspiera).

**Wymagało:**
- Override `EMB_HF_ID` i `EMB_MODEL_DIR` w starter
- `--quantization compressed-tensors` zamiast `--dtype bfloat16`
- Default model w `embed_articles.py` i `smoke_test_embedding.sh`

**Wynik:** 49.4 docs/s — **9% wolniej** niż bf16. Semantyka zachowana:
cross-lingual cosine PL↔EN 0.8623 (fp8) vs 0.8645 (bf16), delta < 0.3%.

**Logi serwera fp8:**
```
dtype=torch.bfloat16, quantization=compressed-tensors
Selected CutlassFP8ScaledMMLinearKernel for CompressedTensorsW8A8Fp8
Avg prompt throughput: 9826 tokens/s   ← identyczne jak bf16
```

Wagi fp8, ale **activations bf16** → runtime dequantuje fp8→bf16 dla każdego
matmul. Na sm_121 ten overhead zżera teoretyczny zysk z mniejszych wag.
Kernel CUTLASS fp8 istnieje, ale nie jest tak zoptymalizowany jak natywny
NVFP4 ModelOpt (jak w Gemmie). Wyjaśnia dlaczego oficjalne benchmarki fp8
embedderów pokazują speedup na H100/B200, a nie u nas.

**Decyzja → DECISIONS.md D31:** zostać przy bf16. Defaulty cofnięte we wszystkich
3 skryptach do `Qwen/Qwen3-Embedding-4B`. Komentarz w `start_vllm_llm_plus_embedding.py`
zachowuje notatkę o teście, żeby nie wracać do tego bez nowego powodu (np. inna
quant method, inny embedder).

## Implikacje na pełną skalę

54 docs/s × 22 582 docs = **7 minut**. Akceptowalne dla bieżącego datasetu.

21 milionów URL × 1/54 = 388 000 sekund = **4.5 dnia** tylko embedding @ bf16
na pojedynczej Spark. Do rewizji przy faktycznym scale-up:
- `Qwen3-Embedding-0.6B` (~6× szybsze, D30 odrzucił dla jakości PL — wymaga
  ponownej oceny przy 21M scale)
- Sharded inference na wielu GPU (Spark + RTX 5090 + ...)
- Inny embedder (e5-small, BGE-base — szybsze, niższa jakość)

## Następne kroki

Mając wektory można odpalać HDBSCAN clustering (umap → hdbscan stack), porównać klastry
domenowe vs semantyczne, ocenić czy `doc_text` z entities pomaga vs sam summary.
Plan na kolejną sesję.

## Pliki zmienione

- `scripts/start_vllm_llm_plus_embedding.py` — `vllm serve` + `--runner pooling` +
  GPU_MEM_EMB 0.20→0.30 + EMB_MAX_LEN 8192→4096 + `--max-num-batched-tokens 8192`
  + `--max-num-seqs 256` + healthcheck skip dla działających kontenerów
- `scripts/embed_articles.py` — default model 0.6B → 4B, timeout usunięty
- **nowy** `scripts/smoke_test_embedding.sh`
- **nowy** `OBSERVATIONS/2026-05-11_13-30__embedding_throughput_ceiling.md`
- `DECISIONS.md` — amendment D30 (runtime fixes + ceiling) + nowy D31 (FP8 reject)
- `.gitignore` — dorzucone `runs/`
- `CHANGELOG.md` — wpisy 2026-05-11 (13:00) + (14:30)
