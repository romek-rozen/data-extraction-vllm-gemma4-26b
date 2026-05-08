# PLAN: production deployment (DGX Spark dev → RTX 6000 Pro / RTX 5090 prod)

Tracking observations + concrete plan for moving the pipeline from dev (DGX Spark, ARM, sm_121, Marlin) to production (RTX 6000 Pro / RTX 5090, x86_64, sm_120, native NVFP4).

Last refreshed: 2026-05-08 23:08 CEST.

## Cache portability (D29 candidate — confirmed empirically 2026-05-08)

### TL;DR
**Cache `websites_cache/` jest w pełni portable między maszynami.** Można zbudować
go raz na DGX Sparku (CPU-bound, ARM jest tu OK) i wgrać tar.gz'em na maszynę
produkcyjną przed startem LLM inference. To **odbiera prod GPU 1-3 dni** w
ekstrakcji trafilatury i pozwala mu skupić się tylko na inference.

### Mierzone fakty (Spark 2026-05-08, websites/ = 25667 art)

- **Cache budowanie z zera, ProcessPool 64w:** 25667 art w **93.5s = 274/s**.
  Estymacja dla 26M URL na Sparku z tymi parametrami: **26000000 / 274 / 3600 ≈ 26.4h**
  (~1 dzień nieprzerwanej pracy CPU na Sparku).
- **Cache size:** 181 MB raw na dysku (25667 plików JSON × ~7KB avg markdown).
- **Cache size compressed (`tar -czf`):** **44 MB** (4.1× kompresja — typowy gzip dla
  textowego JSON z polskimi/angielskimi artykułami).
- **Transfer:** rsync przy 100 Mbps ≈ 4s, przy 1 Gbps ≈ 0.4s. Pomijalne.

### Format cache (deterministyczny)

`websites_cache/<sha256(url)>.json` zawiera:

```json
{
  "domain": "example.com",
  "url": "https://example.com/article",
  "content": "# Heading\n\nMarkdown body from trafilatura..."
}
```

- `<sha256(url)>` = identyczny na każdej maszynie dla tego samego URL → klucz
  determinizmu.
- `_version.txt` w `websites_cache/` trzyma `CACHE_VERSION="v4"` —
  `lib/streaming_loader.py:_init_cache` invalidates cache jeśli mismatch
  (graceful regen, ale chcemy uniknąć przez pinowanie wersji).
- **Trafilatura wersja** — pin `requirements.txt` MUST match między Sparkiem i prod
  hostem (różne wersje → różne markdown ekstrakcje → bit-rotted cache).

### Plan deployu cache na prod

1. **Spark — generation:**
   ```bash
   cd /path/to/repo
   find websites_cache -name "*.json" -delete  # wyczyść stary
   tmux new-session -d -s warmup64 \
     'python3 -u -c "
     from lib.streaming_loader import stream_articles_async
     for art in stream_articles_async(\"websites\", limit=0,
                                       n_loader_workers=64,
                                       executor_kind=\"process\"):
         pass
     "; sleep 3600'
   # ETA dla 26M URL: ~26h.
   ```

2. **Spark — package:**
   ```bash
   tar -czf websites_cache.tar.gz websites_cache/
   sha256sum websites_cache.tar.gz > websites_cache.tar.gz.sha256
   # ~44 MB per 25k articles → ~45 GB dla 26M URL.
   # Dla 26M: rozważ podział na shardy (10× 4.5GB) dla parallel transfer.
   ```

3. **Transfer:**
   ```bash
   rsync -avzP websites_cache.tar.gz prod_host:/path/to/repo/
   ```

4. **Prod — install:**
   ```bash
   cd /path/to/repo
   tar -xzf websites_cache.tar.gz
   ls websites_cache/_version.txt  # weryfikacja CACHE_VERSION
   ```

5. **Prod — run LLM-only:**
   ```bash
   python3 scripts/run_spo_v1_v2_test.py \
     --limit 0 --concurrency-each 4 \
     --no-clear-cache --no-warmup \
     --tag prod_full
   ```

   `--no-warmup` skipuje Stage 2; LLM startuje od razu na hot cache.

### Konsekwencja dla harmonogramu prod runa

Bez cache portability:
- Prod GPU robi cache + LLM = **GPU idle podczas trafilatura** (wąskie gardło CPU).
- Dla 26M URL: ~3-5 dni LLM inference + ~1-3 dni cache CPU = **4-8 dni total**.

Z cache portability (Spark → prod):
- Spark: ~1-3 dni cache (CPU-only, GPU idle, koszt operacyjny ~$0).
- Prod: ~3-5 dni LLM only (GPU 100%, koszt ~$200-400 RunPod RTX 6000).
- **Total: 4-8 dni ALE prod GPU billed dla 3-5 dni zamiast 4-8 = oszczędność ~$100-200.**

## RAM accounting (dlaczego Spark wygląda na "pełen")

Pomiar 2026-05-08 23:07 podczas 64w warmup:
- `Mem: 121Gi total, 108Gi used, 13Gi free` (free) + 2.3Gi buff/cache + swap 3.6Gi.
- Wyglądało jak "pełny RAM" — w istocie dominuje vLLM (Gemma 4 26B + KV cache + activations w shared CPU/GPU memory na Sparku, GB10 ma unified memory).

Rozkład estymowany:
- **vLLM (model + KV + activations):** ~70-80 GB (shared CPU/GPU pool na Spark unified memory).
- **64 ProcessPool workers × ~200-500 MB (tokenizer + lxml fork copies):** ~12-32 GB.
- **OS + reszta:** ~5-10 GB.
- = ~108 GB total.

### Implikacja dla prod hostów (dyskretny GPU = osobna VRAM)

Na RTX 6000 Pro / RTX 5090 model siedzi w **VRAM GPU**, nie w CPU RAM:
- 48 GB VRAM (RTX 6000 Pro) wystarcza dla Gemma 4 26B NVFP4 + KV cache (z `gpu-memory-utilization 0.85` ≈ 41 GB modelu + KV).
- **Host RAM cały dla workerów + OS** — nie konkuruje z modelem.

Rekomendowane budżety RAM hosta dla cache warmup workerów:

| Host RAM | Bezpieczna liczba ProcessPool workerów | Notes |
|---|---|---|
| 32 GB | 32-48 | Workers ~16-25 GB, OS ~5 GB, headroom ~7 GB |
| 64 GB | 64-96 | Workers ~32-50 GB, OS+inne 10 GB, plenty room |
| 128 GB+ | 64-96 | Limit = liczba rdzeni CPU, nie RAM |

Powyżej 96 workerów na 20-32 rdzeniowych hostach → context-switch overhead, no scaling.

## CPU topology — Spark vs prod kandydaci

### DGX Spark (NVIDIA GB10, ARM Neoverse)
- **20 rdzeni fizycznych** (10× Cortex-X925 perf + 10× Cortex-A725 efficiency).
- **Brak SMT/hyperthreading** (`Thread(s) per core: 1`).
- ProcessPool sweet spot: 16w (137/s w live test) lub 64w (274/s w live test, ale RAM stress).

### RTX 6000 Pro (typowy host x86_64)
- AMD EPYC / Intel Xeon, zwykle 16-64 rdzeni z SMT (32-128 threadów).
- **Z SMT można jechać 96-128 ProcessPool workers** (oversubscription 1.5-2× jest OK
  bo trafilatura I/O+GIL hybrid).
- **Estymacja:** 26M URL na 32-rdzeniowym hoście z SMT × 64w → możliwe **400-500/s**
  (Spark robi 274 z 20 rdzeniami bez SMT). Dla 26M: ~14-18h.

### Konsekwencja
- Cache build na prod hoście może być **2-3× szybszy** niż na Sparku.
- ALE generalnie nie blokuje to plan "build na Sparku → transfer". Sparka mamy "za darmo"
  (już opłacony), prod GPU $/h drogi → wszystko co da się zrobić preprocessing-only na
  Sparku odciąża prod budget.

## TODO przed prod runa (uzupełnij ten plan)

- [ ] Pinować `trafilatura==X.Y.Z` w `requirements.txt` (znaleźć wersję obecną na Sparku).
- [ ] Dodać `--no-warmup --no-clear-cache` jako default flagi w prod orchestratorach.
- [ ] Skrypt verify cache integrity: sprawdzenie `_version.txt`, sprawdzenie czy
      każdy `<hash>.json` jest valid JSON z polami `{domain, url, content}`.
- [ ] Test pełen flow Spark → tar → rsync → prod local → load → run (na małym sample).
- [ ] Pomiar `cache_warmup_meta.json` na rzeczywistym RTX 6000 hoście (32-rdzeniowy x86)
      → kalibracja ETA dla 26M URL w obu strategiach.
- [ ] Podział tar.gz na shardy (np. 10× ~4.5 GB) dla parallel rsync gdy mocno daleko.
- [ ] DECISIONS D29 — cache portability + protokół deployu.

## Powiązane decyzje

- D5 — trafilatura markdown (cache treść).
- D26 — schema v3 maxItems removed.
- D27 — parallel v1+v2 + osobno mierzony cache gen.
- D28 — ProcessPoolExecutor option dla streaming_loader (12-30× speedup CPU-bound warmup).

## Powiązane sesje

- `SESSIONS_SUMMARY/2026-05-08_spo_rich_json.md`.
- `SESSIONS_SUMMARY/2026-05-08_overnight_master.log`.
