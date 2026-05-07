# Plan optymalizacji produkcji — RTX PRO 6000 Blackwell (96 GB)

**Cel:** zminimalizować czas (i koszt) przetworzenia 26M URL pipeline'em two-step (Step 1 entity extraction → Step 2 SEO meta) na pojedynczej RTX PRO 6000 Blackwell.

**Stan na:** 2026-05-07. Wszystkie liczby to estymaty wymagające walidacji benchmarkiem na docelowej karcie — pierwszy krok migracji to mikro-benchmark, *potem* tuning.

---

## Założenia bazowe

| Parametr | Wartość |
|---|---|
| GPU | RTX PRO 6000 Blackwell, 96 GB GDDR7, sm_120, native FP4 |
| Memory bandwidth | ~1792-1800 GB/s |
| Model | **`bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4`** (MoE 25.2B / 3.8B active, NVFP4 weights, FP8 KV) — to ten sam model którego używamy na Sparku |
| Wagi modelu | **~16 GB na dysku** (3 safetensors: 7.5 + 7.5 + 0.4 GB) — *istotnie* mniejszy niż wariant `nvidia/...` (~18.8 GB) |
| Native context | 131k (używamy 32k → planujemy ściąć do 24k) |
| Workload | 26M URL × (Step 1 + Step 2). Input ~5-12k tok, output ~250-750 tok |

> **Kluczowa korekta vs wcześniejsze estymaty:** model 16 GB (bg-digitalservices) zamiast 18.8 GB (nvidia) zwalnia ~3 GB → na 96 GB PRO 6000 zostaje ~75 GB na KV+activations → **batch 64-80** jest realny (a nie 48). Na 5090 (32 GB) z kolei batch 16-20 zamiast 12. Tabele estymatyczne na końcu dokumentu są zaktualizowane.

**Charakterystyka workloadu (kluczowe — to NIE jest typowy chatbot):**
- **Heavy prefill** — input ~5-12k tokenów (artykuł), output 250-750 (Step 1) i 200-400 (Step 2). Stosunek input:output ≈ 20-50:1.
- **Offline batch** — brak wymogu niskiej latencji per request, tylko throughput.
- **Powtarzalny system prompt** — duża wartość prefix caching (system + schema dla wszystkich requestów identyczne).
- **Structured output (xgrammar)** — schema JSON wymusza tokeny → narzut overhead grammar matching, ale skraca output (mniej "chitchat"-u).

---

## Quick wins — pierwsze do zrobienia (priorytet 1)

### 1. Native FP4 zamiast Marlin fallback
**Wymóg:** image vLLM z natywnym wsparciem sm_120 NVFP4 (`vllm/vllm-openai:latest` ≥ v0.19.0, sprawdź release notes pod kątem MoE NVFP4 na sm_120).

⚠ **Pułapka (z benchmarków społeczności):** dla niektórych MoE NVFP4 modeli na sm_120 paradoksalnie *Marlin W4A16 fallback* jest szybszy niż FlashInfer CUTLASS NVFP4 (CUTLASS TMA Warp Specialized grouped GEMM tactics fail at initialization). Zmierzyć **oba**:

```bash
# Wariant A (preferowany, native FP4):
# brak VLLM_MOE_FORCE_MARLIN, default backend FLASHINFER_CUTLASS od v0.19+

# Wariant B (fallback dla porównania):
VLLM_MOE_FORCE_MARLIN=1 vllm serve ...
```

**Wybrać szybszy** na podstawie smoke testu na 200-500 artykułach. Dla Gemma 4 26B A4B trzeba zmierzyć — różnica może być 1.5-2× w obie strony.

### 2. Async scheduling (V1 engine)
```
--async-scheduling
```
Overlap między schedulingiem a decode. Z release notes vLLM v0.16+: do +20% throughput na Blackwell. **Tanio** — sama flaga.

### 3. Chunked prefill + dobry budżet
```
--enable-chunked-prefill
--max-num-batched-tokens 16384   (start; tunować 8192-32768)
```
Dla ~12k input prefill jest etapem dominującym. Chunked prefill pozwala overlapować prefill jednych requestów z decode innych — kluczowe przy nierównych długościach inputów.

⚠ Trade-off: gdy prompt jest dzielony na chunki, tylko **pierwszy chunk** korzysta z prefix cache. Więc jeśli `max-num-batched-tokens` < długość systemu+schema, tracimy hit. **Reguła:** ustawić `max-num-batched-tokens` ≥ length(system_prompt + schema) dla obu kroków, żeby cały wspólny prefix mieścił się w jednym chunku. Zmierzyć tokenizerem przed startem.

### 4. Prefix caching — ON (już mamy, ale warto zwalidować)
```
--enable-prefix-caching
```
System prompt + schema są **identyczne** dla 26M requestów. Hit rate powinien być ~100% (poza pierwszym requestem na worker). Zwalidować przez `/metrics` endpoint vLLM (`vllm:prefix_cache_hit_rate`). Jeśli <90% — coś źle (np. zmienny zakres parametrów, nondetermimistyczny system prompt).

### 5. Wyższy batch (max-num-seqs)
Na 96 GB VRAM, **16 GB wagi (bg-digitalservices)**, FP8 KV ~420 MB/seq @ 32k:
- 64 seq × 420 MB = ~26.9 GB KV
- 80 seq × 420 MB = ~33.6 GB KV
- 16 GB wagi + 34 GB KV + 4 GB activations ≈ ~54 GB / 96 GB → **olbrzymi zapas (~40 GB)**

Start: `--max-num-seqs 80`, gpu-memory-utilization 0.92. Tunować: 64, 80, 96, 128.

> Im większy batch, tym wyższy hit rate prefix cache (więcej współdzielonych prefiksów w kolejce równocześnie), tym więcej tensor core utilization. Społeczność melduje że PRO 6000 utrzymuje skalowanie aż do batch 128+ (`databasemart` benchmark: ~7800 tok/s @ batch 128).

⚠ Klient (`lib/vllm_client.py` + `ThreadPoolExecutor` w `run_step1.py`/`run_step2.py`) musi też mieć podniesione `--concurrency` — obecny default 4 (`lib/config.py:DEFAULT_CONCURRENCY`). Bez tego nawet wysoki `max-num-seqs` nic nie da bo serwer dostaje za mało równoczesnych requestów. **Reguła:** `--concurrency` po stronie klienta ≥ 1.5× `max-num-seqs` (nadmiarowo, żeby kolejka serwera nie schła).

### 6. Skrócić max-model-len jeśli nie potrzeba 32k
Z `lib/config.py`: `MAX_ARTICLE_TOKENS = 15000` + ~5k overhead + 4k output = ~24k. **Dużo poniżej 32k.**

```
--max-model-len 24576   (było 32768)
```
KV cache full-attention layers skaluje się liniowo od seq_len → oszczędność ~25% pamięci KV cache full layers, więcej miejsca na wyższy `max-num-seqs`. Lub — można zostać przy 32k i mieć większy bufor; decyzja po policzeniu czy w batchu mieści się docelowe 64-128 seq.

---

## Optymalizacje średnie (priorytet 2 — po pomiarze baseline'u)

### 7. Compilation level 3 (torch.compile)
```
--compilation-config '{"level": 3}'
```
Z benchmarków społeczności: zauważalny zysk decode na Blackwell. Pierwszy start ~5-10 min dłuższy (kompilacja kerneli). Cache na `~/.cache/vllm/torch_compile_cache` — kolejne starty z tymi samymi flagami szybkie. **Sprawdzić** czy kompatybilne z xgrammar guided_json (release notes / smoke test JSON sanity).

### 8. Speculative decoding — N-gram (medusa-lite alternatywa)
Pipeline ma **strukturalne, powtarzalne outputy**: schema JSON wymusza klucze (`"name":`, `"type":`, `"category":`, ...). N-gram speculative decoding może dać 1.3-1.8× speedup na decode bez kosztu draft modelu.

```
--speculative-config '{"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4}'
```

⚠ Kompatybilność z xgrammar do zweryfikowania. Jeśli nie działa — pominąć.

### 9. Async output processing
Na V1 jest defaultem, ale warto sprawdzić w logach że nie cofnęło się do sync.

### 10. KV cache offloading (eksperyment)
vLLM v0.16+ ma CPU KV offloading. Dla naszego workloadu **nieprzydatne** — system prompt i tak trafia do GPU prefix cache, a unikalna część (artykuł) jest jednorazowa. Skip, chyba że bardzo duży batch zacznie wypierać prefix.

---

## Optymalizacje aplikacyjne (priorytet 1 — najtańsze, dają zwykle najwięcej)

### 11. Skrócenie inputu — agresywny truncate / smart compression
**To jest największa dźwignia.** Prefill jest O(N²) w attention compute, O(N) w bandwidth. Ścięcie inputu z 12k → 6k to nie 2×, to ~2-4× szybsze.

Działania:
- **Audyt długości** — uruchomić `scripts/measure_lengths.py` na próbce 5000, zobaczyć rozkład. Aktualne `MAX_ARTICLE_TOKENS=15000` dotyka ~1% (p99 ~13.5k). Rozważyć `MAX_ARTICLE_TOKENS=8000` lub 6000 — odsiać 5-10% długich (te artykuły zwykle mają i tak dużo szumu — boilerplate, komentarze, tabele).
- **Smart truncation** — zamiast hard cut na pierwszych N tokenach, brać pierwsze 60% i ostatnie 40% (intro + konkluzja zwykle wystarczy do meta SEO + encji). Albo: priorytet nagłówkom (markdown `#`, `##`).
- **Heuristics filter** — pominąć boilerplate trafilatury (linki nawigacyjne, stopki, wybory cookie). Sprawdzić co realnie zostawia trafilatura przy `include_links=True` — może `include_links=False` w step 2 (do meta SEO linki nie są potrzebne)?
- **Two-stage cleanup** — jeśli markdown ma >6k tok, drugi pass kompresji (usunięcie tabel, wieloliniowych list itd.).

**Estymata:** redukcja medianowej długości z 1.6k → 1.2k + odsianie 10% top-długich → spodziewane -25-40% latencji prefill.

### 12. Re-use Step 1 output w Step 2 (batch fusion?)
Obecnie Step 1 zwraca encje, Step 2 dostaje znów cały artykuł + encje. **Czy Step 2 potrzebuje całego artykułu, czy wystarczy summary + encje?**

Eksperyment:
- Wariant A (obecny): Step 2 input = system + artykuł + encje z Step 1
- Wariant B: Step 2 input = system + pierwsze 2000 tok artykułu + ALL encje + (opcjonalnie) lista nagłówków

Wariant B mógłby ściąć Step 2 latencję ~3-5×. Walidować jakością (5 sample manualnie + automated metric: title relevance vs ground truth).

### 13. Schema simplification — Step 1
Schema z 51 typami Azure NER + enum-y + per-type metadata to dużo branchy w xgrammar grammar matcher. Każdy nowy token musi być sprawdzony grafem stanu. Pomysły:
- Spróbować bez `metadata` (już jest TYPE_TO_CATEGORY deterministyczny po typie — a metadata daje minimalny lift).
- Mniejsza lista typów (top 20 zamiast 51, reszta jako "Other") — sprawdzić histogram używania typów w `final_results/`.

Estymata: -10-20% decode time per token w Step 1 (xgrammar overhead jest mierzalny).

### 14. Połączyć Step 1 + Step 2 w jeden request? (jednak one-step?)
W `INSTRUCTIONS_FROM_CLAUDE.md` two-step ma uzasadnienie (separacja: encje uniwersalne, meta language-aware). Ale **na poziomie technicznym**: 2× input prefill = 2× narzut na input.

Hybryda: **jeden duży request**, dwie sekcje w schema (entities + meta), system prompt mówi "extract both". Jeden prefill 12k zamiast dwóch po 12k → potencjalnie ~30-40% szybszy total dla URL-a.

⚠ Trade-off: jakość. W Phase 2 Mateusz wybrał two-step właśnie po pomiarze jakości. Wymaga A/B na 100-200 sample. **Warto retest** na finalnym v6 prompt — może dziś two-step już nie wygrywa znacząco.

---

## Tuning batchu — metodyka

Nie zgaduj — zmierz. Plan:

1. **Microbenchmark 100 URL @ baseline** (max-num-seqs 8, brak async-scheduling, brak chunked prefill). Zapisać `tok/s decode`, `tok/s prefill`, `s/req mediana`.
2. **Włącz async-scheduling + chunked-prefill + prefix-cache.** Pomiar.
3. **Sweep `max-num-seqs`** w {16, 32, 48, 64, 96, 128}. Plot: throughput vs batch size. Sweet spot tam gdzie krzywa zaczyna wypłaszczać.
4. **Sweep `max-num-batched-tokens`** w {8192, 16384, 24576, 32768}. Reguła: większy batch = lepszy throughput w prefill-heavy, ale dłuższe TTFT.
5. **Spróbuj compile level 3.**
6. **Spróbuj speculative ngram.**
7. **Spróbuj `--moe-backend marlin` vs default** (CUTLASS) — empirycznie który szybszy dla Gemma 4 NVFP4 MoE.

Mierz **przez 5-10 minut na ustawienie** — krócej zaszumi caching. Skrypt: rozszerzyć `scripts/snapshot_metrics.py` o pull `/metrics` z vLLM (Prometheus format) + parsing.

---

## Aplikacyjny scaling — wielowątkowość po stronie klienta

Obecny `ThreadPoolExecutor(max_workers=concurrency)` w `scripts/run_step1.py`. Issues:
- Każdy worker robi blocking `requests.post`. Z conc 64 i ~3s/req — 21 req/s peak.
- Network overhead per request — JSON serialization 12k tokens × ~4B/tok = 48 KB body. Przy 64 req/s = 3 MB/s — nieistotne.

**Co zmienić:**
- Przejść na `httpx.AsyncClient` + `asyncio.gather` (eliminuje GIL bottleneck przy >32 wątków, oszczędza ~5-10% CPU).
- Lub: prosto, podbić `--concurrency` do 96-128. Zysk z async client nieduży gdy serwer i tak bottleneck.
- **Read-ahead loadera** — `data_loader` powinien yieldować `prefetch=N` artykułów na zapas (na osobnym wątku trafilatura), żeby GPU nie czekał na trafilaturę na CPU. Trafilatura potrafi zająć 50-200ms na duży HTML — przy 64 req/s = potencjalnie 3-12s/s pracy CPU, czyli 3-12 wątków CPU. Dziś robi to jeden ThreadPoolExecutor w sumie z requestami → **CPU-bound trafilatura kradnie sloty na requesty**.

**Architektura docelowa:**
```
[disk reader] → [trafilatura pool, 8 workers] → [request queue, depth 256]
                                                       ↓
                                          [async http client, 128 in-flight]
                                                       ↓
                                          [result writer, 1 worker, jsonl append]
```

To rozwiązuje 3 problemy: (1) GIL, (2) CPU/GPU overlap, (3) checkpointing (writer flushuje per-N).

### Idempotencja przy 26M
`url_hash` lookup w jsonl jest O(N²) jak rośnie wynik. Po 1M URL-i każdy nowy request sprawdza 1M linii. **Migracja na sqlite** (`done_urls.sqlite`, indeks po `url_hash`) — O(log N) lookup, jednorazowy migration cost. Inaczej end-of-run będzie 5-10× wolniejszy niż początek.

---

## Ryzyka / pułapki specyficzne dla docelowego setupu

1. **NVFP4 MoE na sm_120 — niedojrzałe.** Społeczność melduje przypadki gdzie CUTLASS init fail-uje, fallback do Marlin (W4A16 dequant) działa, ale nie wykorzystuje native FP4 tensor cores → dostajesz "tylko" 2× szybciej niż BF16, nie 4×. **Należy zmierzyć Twoim modelem.** Jeśli zobaczysz 0.5 s/req zamiast docelowych 0.13 — to jest ten przypadek.

2. **xgrammar + speculative decoding** — historycznie konflikt. Sprawdzić release notes vLLM dla wersji deploymentowej.

3. **xgrammar + reasoning-parser** — już zdokumentowany w `DEPLOYMENT.md` (issue #39130). Nie regresować.

4. **Compilation cache invalidation** — każda zmiana flag czyści `torch_compile_cache`. Po finalnym wyborze flag, zrobić warmup + skopiować cache do snapshotu (Docker layer / wolumin).

5. **MoE expert parallel** — nie używać. Społeczność: "catastrophic on PCIe — 1.4-2.6 tok/s". Single GPU → expert parallel niepotrzebny i tak.

---

## Konkretna rekomendacja startowa (do walidacji)

```bash
# vLLM start dla RTX PRO 6000 Blackwell (96 GB), model bg-digitalservices ~16 GB
# UWAGA: na sm_120 (PRO 6000) brak potrzeby gemma4_patched.py — patch dotyczył sm_121 (Spark)
docker run -d --gpus all --ipc=host \
  -v /workspace/models/gemma4-26b-nvfp4-bg:/model \
  -p 8001:8000 \
  -e VLLM_USAGE_SOURCE=production \
  vllm/vllm-openai:latest \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 24576 \
  --max-num-seqs 80 \
  --max-num-batched-tokens 16384 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --async-scheduling \
  --compilation-config '{"level": 3}' \
  --default-chat-template-kwargs '{"enable_thinking": false}'
# Po smoke test: dodać --speculative-config (ngram) jeśli kompatybilny z xgrammar
# Próbować bez i z VLLM_MOE_FORCE_MARLIN=1 — wybrać szybszy
```

### ⚠ Czego NIE dodawać (mimo że pojawia się w niektórych template'ach internetowych)

```
--reasoning-parser gemma4    # ❌ ŁAMIE xgrammar guided_json (vLLM issue #39130)
--tool-call-parser gemma4    # ❌ niepotrzebne — nie używamy tool calls, tylko response_format
--moe-backend marlin         # ❌ na sm_120 PRO 6000 ma natywne FP4; Marlin to fallback dla sm_121 Spark
```

Te flagi krążą w przykładach społeczności, ale **dla naszego use case'u** (structured output via xgrammar, brak tool calls, native FP4) — wszystkie trzy są szkodliwe lub niepotrzebne. Trzymać się minimalnej listy wyżej. Patrz `DEPLOYMENT.md` i `INSTRUCTIONS_FROM_CLAUDE.md` dla pełnego uzasadnienia.

Po stronie klienta:
```python
# lib/config.py
DEFAULT_CONCURRENCY = 96   # 1.5× max-num-seqs
```

```bash
python3 scripts/run_full.py --limit 0 --concurrency 96 --tag prod_pro6000
```

---

## Estymata po optymalizacji (orientacyjnie, do walidacji)

Z poprawionym modelem 16 GB i batch 64-80:

| Wariant | s/req amortized | Czas 26M | Koszt @ $1.29/h (Cloudrift) |
|---|---|---|---|
| Baseline ref (batch 48, 0.13 s/req) | 0.13 | ~39 dni | ~$1210 |
| + async-scheduling + chunked prefill + prefix cache | ~0.10 | ~30 dni | ~$930 |
| + max-num-seqs 80 (zamiast 48) | ~0.060 | ~18 dni | ~$560 |
| + skrócenie inputu (15k → 8k median) | ~0.040 | ~12 dni | ~$370 |
| + compile level 3 + ngram spec (jeśli kompat) | ~0.030 | ~9 dni | ~$280 |
| + one-step fusion (jeśli jakość OK) | ~0.020 | ~6 dni | ~$190 |

Realny target z konserwatywnymi optymalizacjami: **9-18 dni i $300-560** dla 1× PRO 6000 na Cloudrift. **Najtańsza dźwignia to 5 (batch 80) + 11 (input length) + 14 (one-step retest)** — to operacje aplikacyjne i flag-only, nie wymaga zmiany hardware'u.

### Porównanie kosztów przy różnych providerach (1× PRO 6000, ~18 dni @ 0.06 s/req)

| Provider | $/h | Total dla 26M | Komentarz |
|---|---|---|---|
| Vast.ai | $1.00 | ~$430 | Marketplace — zweryfikować host przed commit |
| Cloudrift | $1.29 | ~$560 | Dedykowany cloud, dobry stosunek cena/stabilność |
| Hyperstack | $1.80 | ~$780 | |
| RunPod | $1.89 | ~$820 | Network Volume + integracje |
| CoreWeave | $2.50 | ~$1080 | 8× clustery, overkill |
| Google Cloud | $2.85 | ~$1230 | |
| AWS | $3.36 | ~$1450 | |

### Porównanie skalowania PRO 6000

| Setup | Czas | Total cost (Cloudrift) |
|---|---|---|
| 1× PRO 6000 | ~18 dni | ~$560 |
| 2× PRO 6000 | ~9 dni | ~$560 (skalowanie liniowe) |
| 4× PRO 6000 | ~5 dni | ~$580 |

Wybór wielokarty głównie kwestią deadline'u. Koszt prawie się nie zmienia.

---

## Plan walidacji — minimalny commit przed prod

**Krok 1: 1h test na Cloudrift (~$1.29) lub Vast.ai (~$1.00)**
- Spin up 1× PRO 6000, wgrać model bg-digitalservices (16 GB, ~5-10 min upload przez sieć)
- Run `scripts/run_pipeline.py --limit 200 --concurrency 96` z proponowaną konfiguracją
- Cel: zwalidować estymatę **0.06 s/req aggregated** dla Step 1 + Step 2 razem

**Akceptacja:**
- ✅ ≤0.08 s/req → zielone światło, 1× PRO 6000 / 18 dni / ~$560
- ⚠ 0.08-0.15 s/req → CUTLASS NVFP4 fail prawdopodobny → przetest z `VLLM_MOE_FORCE_MARLIN=1`, lub skok na 2-4 karty
- ❌ >0.15 s/req → jakiś regression w obrazie vLLM lub modelu, debug przed prodem

**Krok 2: short run 5000 URL** (parę godzin) — sprawdzić quality regression vs Spark, zwalidować że jakość outputu jest identyczna (model i sampling identyczne, więc powinno być).

**Krok 3: full prod run** z resume i checkpointami (już mamy `run_full.py --resume`).

**Krok 4 (opcjonalny):** jeśli deadline pozwala na 18 dni → 1 karta, najtaniej. Jeśli potrzeba <10 dni → 2 karty (load balancer prosty: hash(url) % N → port).

---

## TL;DR — kolejność działań

1. **Mikrobenchmark baseline** na 200-500 URL w docelowej konfiguracji (Marlin vs CUTLASS, smoke test xgrammar).
2. **Włącz tanie flagi:** `--async-scheduling`, `--enable-chunked-prefill`, `--max-num-seqs 64`, `--max-num-batched-tokens 16384`. Pomiar.
3. **Audyt długości inputu** — `MAX_ARTICLE_TOKENS` 15000 → prawdopodobnie 6000-8000. Pomiar jakości na 100 sample.
4. **Refaktor klienta na async + read-ahead trafilatura.** Zmiana sqlite do dedup (przy 26M wymóg).
5. **Compile level 3, speculative ngram** — jeśli kompatybilne, zysk ~10-20%.
6. **Retest one-step vs two-step** na aktualnym v6 prompcie — może 30-40% szybciej za free, jeśli jakość się utrzymuje.
7. **Sweep batch parametrów** dopiero po (1)-(6).

---

## Decyzja vs alternatywy

| Setup | Czas 26M | Total cost | Uwagi |
|---|---|---|---|
| 1× RTX 5090 (32 GB, batch 16-20) | ~60 dni | ~$1355 (RunPod $0.94/h) | OK ale wolne |
| 4× RTX 5090 | ~15 dni | ~$1355 | dużo skomplikowanych spraw z multi-GPU |
| 8× RTX 5090 | ~7.5 dni | ~$1360 | overkill ops |
| **1× RTX PRO 6000 @ Cloudrift** | **~18 dni** | **~$560** | ⚡ **rekomendacja** |
| 1× RTX PRO 6000 @ Vast.ai | ~18 dni | ~$430 | tańsze ale weryfikacja hosta |
| 2× RTX PRO 6000 @ Cloudrift | ~9 dni | ~$560 | jeśli deadline ciasny |
| 1× B200 @ RunPod | ~13 dni | ~$1480 | drożej i wolniej w single-GPU |
| 1× H100 SXM | porównywalnie | drożej | benchmarki: PRO 6000 daje 3140 vs 2987 tok/s na quantized models |

**Werdykt:** RTX PRO 6000 to "H100 killer" dla naszego use case'u (NVFP4 single-GPU, batch heavy, 16 GB model). Native FP4 + 96 GB + bandwidth 1800 GB/s + dojrzały vLLM support na sm_120 = optymalna karta.

❌ **NIE używać** A5000, A6000 (Ampere — brak native FP4, wymagałby AWQ/GPTQ konwersji modelu, wolniej mimo niższej ceny godzinowej).

---

## Źródła

- [Gemma 4 vLLM Recipes (oficjalne)](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
- [vLLM Optimization and Tuning](https://docs.vllm.ai/en/stable/configuration/optimization/)
- [Pro 6000 vLLM Inference Benchmark — Database Mart](https://www.databasemart.com/blog/vllm-gpu-benchmark-pro6000)
- [vLLM v0.16.0 — +20% throughput on Blackwell](https://joshua8.ai/vllm-v016-blackwell-throughput-benchmark/)
- [SM120 RTX PRO 6000 NVFP4 MoE Performance Report (Qwen3.5)](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536)
- [Support for RTX 6000 Blackwell 96GB — vLLM forum](https://discuss.vllm.ai/t/support-for-rtx-6000-blackwell-96gb-card/1707)
- [NVFP4 MoE backend selection issue (FLASHINFER vs MARLIN)](https://github.com/vllm-project/vllm/issues/38971)
- [Akamai Cloud RTX Pro 6000 Blackwell Benchmark](https://www.akamai.com/blog/cloud/benchmarking-nvidia-rtx-pro-6000-blackwell-akamai-cloud)
- [Edge AI Vision — NVFP4 Impact on LLM Inference](https://www.edge-ai-vision.com/2025/10/nvidia-blackwell-the-impact-of-nvfp4-for-llm-inference/)
- [vLLM Scheduling: Token Budgets, Chunked Prefill, Policies](https://audreywongkg.medium.com/understanding-vllm-scheduling-token-budgets-chunked-prefill-and-policies-2c879e3980e3)
