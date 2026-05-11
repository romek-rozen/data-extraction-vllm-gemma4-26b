# Session: v1 vs v2 comparison na pełnym cache + setup Qwen3-Embedding na DGX Spark
## 2026-05-11 (od ~09:30 do ~12:30 CEST, ~3h)

## TL;DR

1. **SPO v1 vs v2 — porównanie 1:1 na 15 730 wspólnych URL** z 25 668 z `websites_cache/`.
   v2 było w trakcie (~56%) po ~24h, kill + porównanie po `url_hash`.
   **Wynik: v1 i v2 jakościowo równoważne dla junk/lang/sponsored/meta (≥98.3% agreement),
   ale SPO triple Jaccard tylko 0.10 — duża wariantywność relacji na poziomie surface form.
   v1 jest 15% szybsze (4,80 vs 5,53 s/URL).** → propozycja v1 jako default; ostateczna
   decyzja w `DECISIONS.md` D29 wymagałaby LLM-judge na próbce triples.

2. **Rozkład encji na pełnym cache (22 582 non-junk artykułów, 281 167 encji).**
   71.64% strong, 28.36% weak. Top 4 kategorie = 76.9% (Product 40.7%, Information 13.4%,
   Location 11.7%, Organization 11.0%). 14 typów Azure NER z <200 wystąpień każde —
   kandydaci do wykluczenia z embedding/clusteringu.

3. **Setup Qwen3-Embedding-4B na DGX Spark.** Nowy orchestrator
   `scripts/start_vllm_llm_plus_embedding.py` startuje OBA kontenery (Gemma + Qwen embed)
   z memory split 0.60/0.20 (~73 GB + 24 GB z 121 GB unified), poll healthcheck na
   `/v1/models` aż oba `[OK]`. Embedding bf16 — natywne na Blackwell, **nie ma NVFP4
   wariantu dla embedderów** (i tak by nie przyspieszyło: forward-only + krótkie sekwencje
   = memory-bound, nie compute-bound).

4. **Skrypt embeddingowy** `scripts/embed_articles.py` z idempotencją po `url_hash`,
   OpenAI-compatible `/v1/embeddings`, format doc_text:
   ```
   {h1}
   {article_summary}
   {strong ∪ central entities, deduped, comma-separated}
   ```
   Weak non-central encje (liczby, daty, procenty) pomijane — szum dla clusteringu.

## Punkt startowy

- v1 run skończony 2026-05-10 10:35 (wall 123 227 s ≈ 34h14min, 25 667 URL).
- v2 run leciał od 2026-05-10 10:35, ~56% z 25 668 po 23h.
- Gemma 4 na :8001 — działała 2 dni nieprzerwanie.
- 21M URL prod-target → na razie pracujemy na 25 668 URL z `websites_cache/`
  (cały populated cache, random sample seed=42).

## Co zrobiono per blok

### 09:30-10:00 — Statusy aktualnie biegnących runów
- Master `scripts/run_spo_v1_v2_sequential.sh` (PID 1674435, bash wrapper PID 1674434
  z `sleep 86400`) odpalił stage1=v1 (✅ done) i stage2=v2 (in progress).
- v2 progres: 14 463 classified / 12 700-14 500 dla każdego stage'a, fail=0.
- ETA do końca v2: ~17h (628 URL/h przy CONC=32).

### 10:00-11:30 — Decyzja: stop v2, zrób porównanie

Powód: 15 730 wspólnych URL = wystarczający sample. Nie ma sensu czekać kolejnych 17h
na pełny v2 jeśli można już teraz porównać.

`scripts/compare_v1_vs_v2.py` — load obu `final.jsonl`, intersection po `url_hash`,
metryki:
- `is_junk` agreement (whole intersection)
- non-junk: language, category, sponsored bool/subtype, n_central
- entities: count + Jaccard po name-normalized
- triples: count + Jaccard po (subj, rel, obj) normalized
- meta: title/desc length stats

Output: `final_results/2026-05-11_11-34-30__compare_v1_vs_v2/{joined.jsonl, report.md}`.

**Wyniki kluczowe (15 730 URL intersection):**

| metryka | v1 | v2 | zgodność |
|---|---|---|---|
| **is_junk** | — | — | 99.89% (17 mismatch) |
| **language** | — | — | 99.99% |
| **category** (exact str) | — | — | 93.39% |
| **sponsored bool** | 34.70% True | 34.51% True | 98.97% |
| **sponsored subtype** | — | — | 98.34% |
| title length p50 | 57 | 57 | identyczne |
| desc length p50 | 151 | 151 | identyczne |
| entities/article (mean) | 12.43 | 12.69 | v2 +2% (ogon dłuższy) |
| n_central/article (mean) | 1.83 | 2.09 | v2 +14% |
| **entities Jaccard** | — | — | mean 0.47, p50 0.46 |
| triples/article (mean) | 9.31 | 10.19 | v2 +9% |
| **triples Jaccard** | — | — | **mean 0.10, p50 0.06** |

**Triples Jaccard 0.10 to alarmujący sygnał** — modele generują różne triples nawet
dla tego samego artykułu (różnice surface form: "died_in"/"died-in", "Krzysztof Krawczyk
zmarł w"/"zmarł w roku" itp.). Wymagałoby semantic eval (LLM-judge) żeby ocenić czy
"różne triples" = "różne fakty" czy "te same fakty, inny zapis".

### 11:00 — Czas ekstrakcji (z logów wall_s)

```
v1: 25 667 URL  → 123 227s (34h14min)  ≈ 4.80 s/URL  ≈ 750 URL/h
v2: 15 730 URL  →  86 900s (24h08min)  ≈ 5.53 s/URL  ≈ 651 URL/h
```

**v1 szybsze o 15.2%** (jedno wywołanie entities+spo vs dwa osobne calle w v2).

### 11:00-11:30 — Rozkład encji na pełnym v1 (22 582 non-junk)

281 167 encji total, mean 12.45 per article, central 14.62%.

| category | n | % |
|---|---|---|
| Product | 114 516 | 40.73% |
| Information | 37 769 | 13.43% |
| Location | 32 952 | 11.72% |
| Organization | 30 902 | 10.99% |
| Quantity | 23 982 | 8.53% |
| Person | 15 026 | 5.34% |
| DateTime | 8 262 | 2.94% |
| Event | 8 019 | 2.85% |
| Skill | 4 741 | 1.69% |
| URL | 3 233 | 1.15% |
| PersonType | 1 171 | 0.42% |
| Address/Phone/Email/IP | 594 | 0.21% |

**Strong vs weak:** 71.64% / 28.36%. Mapowanie deterministyczne po typie
(`lib/pipeline.py:TYPE_TO_CATEGORY`).

**14 typów <200 wystąpień każde** — kandydaci do wykluczenia z embedding:
`Height`, `Ordinal`, `OrganizationStockExchange`, `SetTemporal`, `DateTimeRange`,
`IpAddress`, `DateTime`, `Airport`, `SportsEvent`, `TimeRange`, `Speed`, `Email`,
`PhoneNumber`, `Area`, `Time`, `Age`, `NumberRange`, `Address` (<0.1% każde).

### 11:30-12:30 — Setup Qwen3-Embedding na Spark

Cele:
- Embedding artykułów dla HDBSCAN clusteringu (przyspieszyć kategoryzację).
- Doc_text: `h1 + summary + (strong ∪ central) entities deduped`.

**Decyzja: Qwen3-Embedding-4B** (zamiast 8B). Powody:
- 4B mieści się obok Gemmy w unified memory bez restartu (8 GB wagi bf16 vs 16 GB).
- MTEB różnica 4B vs 8B ~1-2 pkt — nieistotne dla HDBSCAN na 22k krótkich doc'ów.
- 2× szybsze inference.

**bf16, nie NVFP4** — Qwen nie publikuje NVFP4 dla embedderów, i to nie jest problem:
embedding to forward-only, krótkie sekwencje (~150 tokenów per doc), bottleneck =
memory-bound. NVFP4 daje ~4× compute speedup, ale tu compute nie jest wąskim gardłem.
Blackwell GB10 ma natywne bf16 tensor cores — żadnej emulacji.

**Nowy orchestrator**: `scripts/start_vllm_llm_plus_embedding.py` (mimo rozszerzenia
`.py` to bash — user explicit request). Startuje OBA kontenery jednocześnie:

| kontener | port | image | GPU_MEM | ~VRAM |
|---|---|---|---|---|
| `vllm-gemma4` | 8001 | `vllm/vllm-openai:gemma4-cu130` | 0.60 | ~73 GB |
| `vllm-qwen3-embed` | 8002 | `nvcr.io/nvidia/vllm:26.02-py3` | 0.20 | ~24 GB |
| **Suma** | | | **0.80** | **~97 GB / 121 GB** |

Skrypt po `docker run` pollinguje `GET /v1/models` na obu portach (3s krok, timeout
600s/300s), drukuje `[OK] <name> ready po Ns na :PORT` kiedy każdy odpowie. Jeśli
kontener padnie po drodze — drukuje ostatnie 30 linii logów i exit 2.

Wariant `scripts/start_vllm.sh` zostaje **niezmieniony** (`GPU_MEM=0.85` hardcoded,
solo-mode dla Gemmy).

**Format doc_text dla embedding** (skrypt `scripts/embed_articles.py`):
```
Gdzie będzie pochowany Krzysztof Krawczyk?
Artykuł omawia kwestię miejsca spoczynku Krzysztofa Krawczyka, podkreślając decyzję...
Krzysztof Krawczyk, polska muzyka, rodzina Krzysztofa Krawczyka
```

Eligible: 22 582 artykułów (junk 3 085 pominięte), mean doc_len ~412 znaków, p95 ~480.
Idempotencja przez resume po `url_hash` + append do `manifest.jsonl` + `embeddings.npy`.

## Nowe pliki

| Plik | Cel |
|---|---|
| `scripts/compare_v1_vs_v2.py` | Porównanie v1 vs v2 na intersection url_hash |
| `scripts/embed_articles.py` | OpenAI-compatible client do `/v1/embeddings`, build doc_text, batch + concurrent, resume |
| `scripts/start_vllm_llm_plus_embedding.py` | Orchestrator: startuje Gemma + Qwen3-Embedding razem z memory split + health wait |
| `final_results/2026-05-11_11-34-30__compare_v1_vs_v2/{joined.jsonl,report.md}` | Wyniki porównania |

## Otwarte pytania / next steps

1. **Czy v1 jako default?** Wymaga semantic eval triples (LLM-judge na 100-500 próbce
   "czy triple jest poprawny faktycznie") — Jaccard 0.10 nie mówi czy v1 czy v2 jest
   bliżej prawdy. → planowane do `DECISIONS.md` jak będzie eval.
2. **HDBSCAN na embeddingach** — gdy `embed_articles.py` skończy, klastrowanie 22 582
   wektorów (4096-dim bf16 → cast do float32 dla sklearn-compat). Parametry do tuning:
   `min_cluster_size=20-50`, `metric=cosine` (lub euclidean po L2-norm).
3. **Filtr typów Azure NER** — 14 typów <0.1% (1 174 encji łącznie) można wykluczyć
   z embedding/clustering bez utraty sygnału. Wymaga decyzji co z `Quantity`-podzbiorem
   (Currency, Percentage, Date, Number, Length, Duration, Dimension, Temperature, Weight,
   Age, Volume, Area, Speed, NumberRange, DateRange, Time, TimeRange) — czy zostawiamy
   tylko strong central, czy włączamy wszystkie? Obecny default w `embed_articles.py`:
   union(strong, central), weak non-central pominięte.
4. **Czas wykonania embedding** — szacunek 5-10 min wall dla 22 582 doc'ów @ Qwen3-4B,
   batch 16, conc 8. Faktyczny pomiar po pierwszym pełnym runie.
5. **`scripts/run_spo_v1_v2_sequential.sh`** — master skrypt został zatrzymany (PID
   1674435 + 3238606 killed). Jeśli kontynuować v2 do końca, trzeba odpalić
   `run_spo_v2.py --resume` osobno.

## Liczby do zapamiętania

- **15 730 URL** intersection v1 ∩ v2 (z 25 668 total cache).
- **99.89% / 98.97% / 93.39%** agreement (is_junk / sponsored / category) na non-junk.
- **0.47 / 0.10** mean Jaccard (entities / triples).
- **4.80 vs 5.53 s/URL** wall — v1 +15% szybsze.
- **22 582 / 281 167** non-junk artykułów / total encji.
- **71.64%** strong entities (kandydaci do embeddingu).
- **0.60 / 0.20** GPU_MEM split dla Gemma + Qwen3-Embedding na Spark.
