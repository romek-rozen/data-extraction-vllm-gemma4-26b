# DEPLOYMENT

Konfiguracja vLLM + budżet pamięci dla różnych targetów GPU.

**Stan na:** 2026-05-07 (po external review — KV cache math poprawione na podstawie `config.json`).

---

## Model

| Parametr | Wartość |
|---|---|
| Model | `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` |
| Architektura | Gemma 4, **MoE** 25.2B total / 3.8B active |
| Quantization weights | **NVFP4** (4-bit) |
| KV cache | **FP8** (1 byte/value) |
| Rozmiar na dysku | **~16 GB** (3 safetensors: 7.5 + 7.5 + 0.4 GB) |
| Native context | 131k (my używamy 32k) |

### Architektura warstw (z `config.json`)

| Pole | Wartość |
|---|---|
| `num_hidden_layers` | **30** |
| `layer_types` | 25× `sliding_attention` + 5× `full_attention` (pattern 5-local / 1-global) |
| `sliding_window` | 1024 tokens |
| Sliding KV: `num_key_value_heads × head_dim` | 8 × 256 = 2048 |
| Full KV: `num_global_key_value_heads × global_head_dim` | 2 × 512 = 1024 |
| MoE | 128 experts, top-k routing |

## Aktualne flagi vLLM (`scripts/start_vllm.sh`)

```
--model /model
--quantization modelopt
--kv-cache-dtype fp8
--max-model-len 32768
--max-num-seqs 8
--gpu-memory-utilization 0.85
--moe-backend marlin                       # tylko Spark sm_121 software fallback
--enable-prefix-caching
--default-chat-template-kwargs '{"enable_thinking": false}'
```

Image: `vllm/vllm-openai:gemma4-cu130` (custom Gemma 4 build).

> **UWAGA — NIE dodawać `--reasoning-parser gemma4`.** Łączenie z `enable_thinking=false` cicho wyłącza xgrammar (vLLM issue #39130), a my potrzebujemy `guided_json` dla structured output. Trzymaj się obecnego setupu.

## Budżet pamięci — przeliczony na podstawie `config.json`

KV cache per token per layer (FP8 = 1 byte/value):
- Sliding attention layers (cap = 1024 tok): `8 KV heads × 256 head_dim × 2 (K+V) × 1 B = 4096 B/tok/layer`
- Full attention layers (full seq_len): `2 KV heads × 512 global_head_dim × 2 × 1 B = 2048 B/tok/layer`

Per seq @ 32k context:
- Sliding (25 layers, cap 1024): `25 × 1024 × 4096 = 100 MB`
- Full (5 layers, 32768): `5 × 32768 × 2048 = 320 MB`
- **Razem: ~420 MB / seq**

| Komponent | VRAM | Komentarz |
|---|---|---|
| Wagi modelu (NVFP4) | **~16 GB** | 25.2B params × ~4 bity (+ scaling) |
| KV cache (FP8, 32k × 8 seq) | **~3.4 GB** | 8 × 420 MB |
| KV cache (32k × 12 seq) | **~5.0 GB** | 12 × 420 MB |
| KV cache (32k × 16 seq) | **~6.7 GB** | 16 × 420 MB |
| Activations + workspace | **~2–4 GB** | forward pass, attention buffers, prefill chunk |
| **Razem (8 seq)** | **~21–23 GB** | |
| **Razem (12 seq)** | **~23–25 GB** | |
| **Razem (16 seq)** | **~25–27 GB** | |

Wcześniejsza estymata (8–10 GB KV cache, 26–30 GB total) była **przeszacowana** — opierała się na założeniu 62 layers full-attention. Faktycznie 30 layers + 25/30 z cap 1024 = znacznie mniej.

---

## DGX Spark GB10 (dev) — obecny

- **VRAM:** 128 GB unified (CPU+GPU shared)
- **Compute capability:** sm_121
- **Hardware FP4:** ✅ (5th-gen Tensor Cores, 1 PFLOPS peak — Blackwell architecture)
- **Software FP4 w vLLM:** ❌ → CUTLASS pre-compiled kernels nie obejmują sm_121 → Marlin fallback (`--moe-backend marlin`)
- **Throughput Gemma 4 26B:** 2.76 s/req (Step 1, concurrency=8) na ~5979-tok artykule

**Status:** stabilnie pracuje, kontener używa ~3 GB / 121 GB w idle. Marlin fallback jest software-level, nie hardware — Spark *teoretycznie* mógłby native FP4, ale wymaga rebuilda CUTLASS dla sm_121 (poza scope).

## RTX 5090 (target prod, RunPod) — czy się zmieści?

- **VRAM:** 32 GB GDDR7 dedicated
- **Compute capability:** sm_120 (Blackwell)
- **Hardware FP4:** ✅
- **Software FP4 w vLLM:** ✅ (CUTLASS pre-compiled wspiera sm_120)
- **Throughput estymata:** 0.3–0.5 s/req batched (5–10× szybciej niż Spark dla MoE NVFP4)

**Werdykt:** **TAK, z dużym zapasem.** Przy `--gpu-memory-utilization 0.92` masz 29.4 GB dostępne, budżet 23–27 GB → **marża 2–6 GB.** Można podbić `--max-num-seqs` z 8 → 12 (a nawet 16) bez obawy o OOM.

### Rekomendowane zmiany dla RTX 5090

```bash
docker run -d --gpus all --ipc=host \
  -v /workspace/model:/model \
  -p 8001:8000 \
  vllm/vllm-openai:latest \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 32768 \
  --max-num-seqs 12 \                     # 8 → 12 (KV cache mniejsze niż wcześniej zakładano)
  --gpu-memory-utilization 0.92 \         # 0.85 → 0.92 (RTX dedicated VRAM)
  --enable-prefix-caching \
  --enable-chunked-prefill \              # DODANE — szybszy prefill dla 12k+ input
  --default-chat-template-kwargs '{"enable_thinking": false}'
  # USUNIĘTE: --moe-backend marlin (sm_120 ma natywne FP4 kernels)
  # NIE dodawać --reasoning-parser gemma4 — łamie xgrammar (issue #39130)
```

Zmiany vs Spark:
1. **Usunięty `--moe-backend marlin`** — natywny FP4 jest kluczowy dla MoE speedup
2. **`--max-num-seqs 12`** zamiast 8 — KV cache realnie ~5 GB (nie 8-10), starcza miejsca
3. **`--enable-chunked-prefill`** — dla długich input (do 25k tok) prefill 600ms → ~100ms

### Strategia OOM-recovery (jeśli mimo wszystko zabraknie pamięci)

1. `--max-num-seqs 12 → 10 → 8` (oszczędność ~840 MB / 1.7 GB)
2. `--max-model-len 32768 → 24576` (oszczędność ~25% KV cache full layers)
3. Kombinacja jeśli nadal mało — to coś nie tak z image vLLM (regression?)

---

## Sanity testy po każdym starcie

### 1. Health check + max_model_len

```bash
curl -s http://localhost:8001/v1/models | python3 -c \
  "import sys,json; d=json.load(sys.stdin); m=d['data'][0]; \
   print('max_model_len:', m['max_model_len'])"
# Spark/RTX: 32768

bash scripts/smoke_test.sh   # math + JSON guided_json sanity
```

### 2. Throughput stress test (~100 short requests batched)

Cel: zweryfikować że RTX faktycznie daje <0.5 s/req avg dla krótkich generacji. Jeśli >1 s — coś źle z konfiguracją (Marlin nie usunięty? `--enable-chunked-prefill` nie wszedł?).

```python
import asyncio, time
from openai import AsyncOpenAI

async def req(client):
    t = time.time()
    await client.chat.completions.create(
        model="/model",
        messages=[{"role": "user", "content": "Say hi in 5 words."}],
        max_tokens=20,
    )
    return time.time() - t

async def main():
    client = AsyncOpenAI(base_url="http://localhost:8001/v1", api_key="EMPTY")
    start = time.time()
    times = await asyncio.gather(*[req(client) for _ in range(100)])
    print(f"total={time.time()-start:.2f}s  avg={sum(times)/len(times):.3f}s  max={max(times):.3f}s")

asyncio.run(main())
```

Acceptance:
- Spark: avg ~0.5–1.0 s, total 100 req @ conc 100 ≈ 8–12 s
- RTX 5090: avg ~0.1–0.3 s, total ≈ 2–4 s

---

## Spark vs RTX 5090 — porównanie

|                              | DGX Spark GB10                | RTX 5090                              |
|------------------------------|-------------------------------|---------------------------------------|
| VRAM                         | 128 GB **unified** (CPU+GPU)  | 32 GB **dedicated** GDDR7             |
| Memory bandwidth             | 273 GB/s                      | **1792 GB/s** (6.6× Spark)            |
| Compute capability           | sm_121                        | sm_120                                |
| Hardware FP4                 | ✅ (5gen Tensor Cores)        | ✅ Blackwell                          |
| Software FP4 w vLLM          | ❌ → Marlin                   | ✅ natywne CUTLASS                    |
| Throughput Gemma 4 26B (b=8) | 2.76 s/req                    | est. **0.3–0.5 s/req** (5–10× szybciej) |
| Koszt                        | dev hardware (jeden raz)      | ~$0.4–0.6/h RunPod                    |
| Use case                     | dev / staging / debug         | prod 21M URL                          |

## 21M URL — estymata kosztu/czasu na RTX 5090

Przy 0.3–0.5 s/req amortized @ concurrency 12:
- Throughput: **~24–40 req/s** = ~85k–145k URL/h
- 21M URL: **~145–245 godzin** = **~6–10 dni** jednej maszyny
- Koszt: 200h × $0.5/h = **~$100** (RunPod RTX 5090 spot, 1 GPU)

Przy 4 GPU równolegle: ~1.5–2.5 dni, ~$400.

(Wcześniejsza estymata 22–29 dni / $260 zakładała 0.7–1.0 s/req single-stream — batched workload wykorzystuje natywny FP4 lepiej, dlatego korekta w dół.)

---

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- vLLM image gemma4-cu130: custom build, patchowany dla sm_121 (`gemma4_patched.py`)
- vLLM issue #39130 (reasoning-parser bypass): https://github.com/vllm-project/vllm/issues/39130
- Gemma 4 26B A4B layer analysis (kaitchup): https://kaitchup.substack.com/ (KV cache math source)
- INSTRUCTIONS_FROM_CLAUDE.md — full spec architektury
