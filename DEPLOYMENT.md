# DEPLOYMENT

Konfiguracja vLLM + budżet pamięci dla różnych targetów GPU.

**Stan na:** 2026-05-07 (po bumpie `--max-model-len` 24576 → 32768).

---

## Model

| Parametr | Wartość |
|---|---|
| Model | `bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4` |
| Architektura | Gemma 4, **MoE** 25.2B total / 3.8B active |
| Quantization | **NVFP4** (4-bit weights) |
| KV cache | **FP8** |
| Rozmiar na dysku | **~16 GB** (3 safetensors: 7.5 + 7.5 + 0.4 GB) |
| Native context | 131k (my używamy 32k) |

## Aktualne flagi vLLM (`scripts/start_vllm.sh`)

```
--model /model
--quantization modelopt
--kv-cache-dtype fp8
--max-model-len 32768
--max-num-seqs 8
--gpu-memory-utilization 0.85
--moe-backend marlin                       # tylko Spark sm_121 fallback
--enable-prefix-caching
--default-chat-template-kwargs '{"enable_thinking": false}'
```

Image: `vllm/vllm-openai:gemma4-cu130` (custom Gemma 4 build).

## Budżet pamięci

| Komponent | VRAM | Komentarz |
|---|---|---|
| Wagi modelu (NVFP4) | **~16 GB** | 25.2B params × 4 bity (+ scaling) |
| KV cache (FP8, 32k × 8 seq) | **~8–10 GB** | 62 layers × 4 KV heads × 64 head_dim × 2(K+V) × 1B (FP8) × 32768 tok × 8 seq ≈ 8.4 GB |
| Activations + workspace | **~2–4 GB** | forward pass, attention buffers |
| **Razem** | **~26–30 GB** | |

---

## DGX Spark GB10 (dev) — obecny

- **VRAM:** 128 GB unified (CPU+GPU shared)
- **Compute capability:** sm_121
- **FP4 native:** ❌ → Marlin fallback (`--moe-backend marlin`)
- **Throughput Gemma 4 26B:** 2.76 s/req (Step 1, concurrency=8) na ~5979-tok artykule

**Status:** stabilnie pracuje, mamy headroom (kontener używa ~3 GB / 121 GB w idle, peak workspace mieści się luźno).

## RTX 5090 (target prod, RunPod) — czy się zmieści?

- **VRAM:** 32 GB GDDR7 dedicated
- **Compute capability:** sm_120 (Blackwell)
- **FP4 native:** ✅ (3–5× szybciej niż Marlin fallback)
- **Throughput estymata:** 0.7–1.0 s/req (Gemma 4 26B z natywnym FP4)

**Werdykt:** **TAK, ale ciasno.** Przy `--gpu-memory-utilization 0.85` masz 27.2 GB dostępne, budżet ~26–30 GB → marża 0-2 GB.

### Rekomendowane zmiany dla RTX 5090

```bash
# 1. Usuń Marlin fallback — sm_120 ma NATYWNY FP4
# usuń linię: --moe-backend marlin

# 2. Podbij utilization (RTX = dedicated VRAM, nie unified)
--gpu-memory-utilization 0.92             # 29.4 GB dla vLLM

# 3. Jeśli OOM przy starcie — najpierw zmniejsz max-num-seqs
--max-num-seqs 6                          # oszczędza ~2 GB KV, throughput podobny

# 4. Alternatywnie zmniejsz okno (rzadko używamy pełne 32k)
--max-model-len 24576                     # oszczędza ~2 GB KV
```

### Strategia stopniowa (jeśli OOM):

1. `--gpu-memory-utilization 0.92` (włącz natywny FP4 bez Marlina)
2. Jeśli OOM → `--max-num-seqs 6`
3. Jeśli nadal OOM → `--max-model-len 24576`
4. Jeśli nadal OOM → kombinacja (4 seq × 24k) — to już zła sytuacja, sprawdź image vLLM

---

## Sanity test po każdym starcie

```bash
curl -s http://localhost:8001/v1/models | python3 -c \
  "import sys,json; d=json.load(sys.stdin); m=d['data'][0]; \
   print('max_model_len:', m['max_model_len'])"
# Spark/RTX: 32768

bash scripts/smoke_test.sh   # math + JSON guided_json sanity
```

---

## Spark vs RTX 5090 — porównanie

|                          | DGX Spark GB10                | RTX 5090                  |
|--------------------------|-------------------------------|---------------------------|
| VRAM                     | 128 GB **unified** (CPU+GPU)  | 32 GB **dedicated** GDDR7 |
| Compute capability       | sm_121                        | sm_120                    |
| Native FP4               | ❌ (Marlin fallback)          | ✅ Blackwell              |
| Throughput (Gemma 4 26B) | 2.76 s/req (conc=8)           | est. 0.7–1.0 s/req        |
| Koszt                    | dev hardware (jeden raz)      | ~$0.4–0.6/h RunPod        |
| Use case                 | dev / staging / debug         | prod 21M URL              |

## 21M URL — estymata kosztu/czasu na RTX 5090

Przy 0.7-1.0 s/req amortized @ concurrency 8:
- Throughput: ~8-11 req/s = ~30k–40k URL/h
- 21M URL: **~525-700 godzin** = ~22-29 dni jednej maszyny
- Koszt: 525h × $0.5/h = **~$260** (RunPod RTX 5090 spot)

Przy 4 GPU równolegle: ~5-7 dni, ~$1000.

---

## Linki

- Repo: https://github.com/romek-rozen/data-extraction-vllm-gemma4-26b
- vLLM image gemma4-cu130: custom build, patchowany dla sm_121 (`gemma4_patched.py`)
- vLLM issue #39130 (reasoning-parser bypass): https://github.com/vllm-project/vllm/issues/39130
- INSTRUCTIONS_FROM_CLAUDE.md — full spec architektury
