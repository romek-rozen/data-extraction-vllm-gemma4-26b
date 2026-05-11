# Embedding throughput ceiling — Qwen3-Embedding-4B na DGX Spark

**Timestamp:** 2026-05-11 13:30
**Setup:** vllm-qwen3-embed na :8002, GPU_MEM=0.30, max_model_len=4096,
max_num_batched_tokens=8192, max_num_seqs=256 (default)
**Server image:** `nvcr.io/nvidia/vllm:26.02-py3` (vLLM 0.15.1)
**Hardware:** DGX Spark GB10 (sm_121), 121 GB unified memory

## Cel

Znaleźć rzeczywisty sufit wydolności załadowanego modelu embeddingowego.
Wcześniejsze testy z conc=4..8, batch=32..64 dawały konsystentnie ~54 docs/s —
hipotezy: scheduler, max_num_seqs, GPU compute.

## Pomiary

**Source:** `final_results/2026-05-09_00-21-48__spo_v1_mns32_full/final.jsonl`
(22 582 docs po filtrze junk, doc_text avg ~164 tokenów = h1 + summary + entities)

### Sweep ustawień klienta — bf16 (Qwen/Qwen3-Embedding-4B)

| Konfiguracja | docs/s | Wall (1000 docs) |
|---|---:|---:|
| batch=32 conc=4 | 54.0 | 18.5s |
| batch=32 conc=8 | 56.0 | 17.9s |
| batch=64 conc=8 | 56.0 | 17.9s |
| batch=128 conc=8 | 55.0 | 18.2s |
| batch=256 conc=32 | 54.2 | 18.5s |
| **Full run 22582 docs (batch=256, conc=32)** | **53.7** | **420.5s** |

Sufit potwierdzony: **~54 docs/s**, niezależnie od conc i batch.

### Pomiar server-side z logów vLLM

```
Avg prompt throughput: 9001-9837 tokens/s
Avg generation throughput: 0.0 tokens/s   (embedding, no decode)
Running: 47 reqs            ← scheduler wpycha ~47 seq/step
Waiting: 7988-8047 reqs     ← klient zalewa kolejkę (conc=32 × batch=256 = 8192)
GPU KV cache usage: 15.5%   ← KV cache niewykorzystany, 84% wolne
Prefix cache hit rate: 0.0% ← embed docs unikalne, prefix nieprzydatny
```

## Analiza wąskiego gardła

**Co NIE jest ścianą:**
- ❌ Scheduler (`max_num_seqs`): default ≥256, używamy 47
- ❌ KV cache: 15% wykorzystania, 84% wolne
- ❌ Klient (network/concurrency): conc=32 vs conc=4 → ten sam throughput
- ❌ `max_num_batched_tokens=8192`: zwiększenie z default 2048 nie zmieniło rate
  (sprawdzone osobno — restart kontenera z nową wartością, identyczne 55 docs/s)

**Liczbowo gdzie idzie czas:**
- Prompt throughput: **~9 400 tok/s** (peak)
- 9400 tok/s × 8 GFLOPs/token (forward 4B params) = **~75 TFLOPS**
- Spark GB10 peak bf16 ≈ 250 TFLOPS → utylizacja **~30%**
- 47 reqs × 164 tok ≈ 7700 tok/step (blisko `max_num_batched_tokens=8192`)

**Wniosek:** ściana to NIE compute (30%), NIE memory KV cache, NIE scheduler.
Najprawdopodobniej **memory bandwidth + overhead launch kernel dla małych sekwencji**
w pooling forward bf16. Pooling vLLM pipeline jest mniej zoptymalizowany niż
generation (brak full cudagraphs — `Pooling models do not support full cudagraphs.
Overriding cudagraph_mode to PIECEWISE.`).

## Test FP8 (chroma-core/Qwen3-Embedding-4B-FP8-Dynamic)

Hipoteza: jeśli memory-bandwidth-bound, fp8 → 2× memory bandwidth → ~2× throughput.

**Wynik:** 49.4 docs/s — **9% WOLNIEJSZE** niż bf16.

Logi pokazują:
- `Selected CutlassFP8ScaledMMLinearKernel for CompressedTensorsW8A8Fp8` — kernel
  dostępny
- `dtype=torch.bfloat16, quantization=compressed-tensors` — wagi fp8, activations bf16
- `Avg prompt throughput: 9826 tokens/s` — **identyczne** jak bf16

**Wniosek:** na sm_121 fp8 kernel nie daje speedupu. Możliwe przyczyny:
- Dequant overhead fp8 → bf16 dla matmul zżera teoretyczny zysk z mniejszej
  pamięci
- GB10 fp8 tensor cores są obecne ale słabiej zoptymalizowane niż na H100/B200
- CompressedTensorsW8A8Fp8 path nie jest tak szybki jak natywny ModelOpt NVFP4
  (jak w Gemmie)

Semantyka nie ucierpiała — cross-lingual cosine PL↔EN: bf16 = 0.8645, fp8 = 0.8623
(delta <0.3%). Czyli fp8 nie psuje jakości, po prostu nie daje speedupu na tym
sprzęcie.

**Decyzja → D31:** zostać przy bf16.

## Implikacje praktyczne

- **Aktualny dataset (22 582 docs):** 7 min na embed. Akceptowalne.
- **Pełna skala 21M URL:** 21e6 / 54 / 3600 ≈ **108 godzin = 4.5 dnia** tylko na
  embedding @ bf16. Warto rozważyć:
  - Qwen3-Embedding-0.6B (~6× szybsze) — D30 odrzucił z powodu jakości na PL
    nazwach własnych, ale dla 21M scale trzeba rewizji
  - Inny embedder o mniejszym rozmiarze (e5-small, BGE-base)
  - Sharded embedding na wielu Spark/RTX 5090 instancjach równolegle
- **Embedding to forward-only memory-bandwidth-bound workload** — większe GPU
  z HBM (H100, B200) byłoby znacznie szybsze. Spark to nie jest optymalna
  platforma dla high-throughput embedding.

## Output

- `runs/embed_v1_full/` — pierwszy bf16 run, 22582 vec × 2560, 51.67 docs/s
- `runs/embed_v1_full_b8k/` — bf16 ceiling config, 22582 × 2560, 53.7 docs/s
- `runs/embed_fp8_bench/` — fp8 test 1000 docs, 49.4 docs/s
- `runs/embed_ceiling_b256_c32/` — bf16 ceiling 2000 docs

Wszystkie gitignored (`runs/` w `.gitignore`).
