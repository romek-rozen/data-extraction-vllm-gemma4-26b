# Streaming loader + disk cache — plan

## Problem

`lib.data_loader.load_articles()` jest sekwencyjny i materializuje całą listę
przed zwrotem. Dla 25k artykułów to 5–15 min idle GPU; dla 1M → godziny. Każdy
restart pipeline'u parsuje trafilaturą od nowa.

## Rozwiązanie

`lib/streaming_loader.py` — generator yieldujący artykuły on-the-fly:

- **ThreadPoolExecutor** (`n_loader_workers`, default 4): gzip + trafilatura
  zwalniają GIL → realne parallelism.
- **Bounded queue** (`queue.Queue(maxsize=queue_maxsize)`, default 200):
  producer blokuje się gdy konsument nie nadąża → memory-bounded.
- **Disk cache**: `<cache_dir>/<url_hash>.md` (default
  `<PROJECT_ROOT>/websites_cache/`). Hit → bezpośredni `read_text`, miss →
  trafilatura + write. Wersjonowanie przez `_version.txt` (`v1`); zmiana wersji
  inwaliduje wszystkie `.md` w katalogu.
- **Cache trzyma surowy markdown** (przed truncate). Runtime stosuje
  `TEXT_TRUNCATE_LIMIT` + `MAX_ARTICLE_TOKENS` — zmiana budżetu nie inwaliduje
  cache.
- **Lazy enumeracja** dla `random_sample=False && limit=0`:
  `os.scandir()`. Inne tryby wymagają posortowanej listy (determinizm) →
  materializacja nazw subdirów (tanie, bez parsingu HTML).
- **Stats** dostępne po wyczerpaniu generatora przez `.stats` na obiekcie
  zwróconym przez `stream_articles_async`.

## Sygnatura

```python
def stream_articles_async(
    websites_dir, *,
    limit=0, random_sample=False, seed=42,
    n_loader_workers=4, queue_maxsize=200,
    cache_dir=None,           # default: PROJECT_ROOT / "websites_cache"
) -> StreamingLoader          # iterable; po iteracji: .stats (StreamStats)
```

Klucze yieldowanego dicta (zgodne z `load_articles` + ścieżki do plików):
`id, text, url, domain, path, url_hash, text_len, text_tokens, html_path,
json_path`.

## Wyniki smoke (websites/, limit=20, workers=4)

```
run1 (cold):  wall=1.91s  miss=20  hits=0
run2 (hot):   wall=0.34s  hits=20  miss=0   (5.6× szybciej)
20/20 tekstów identycznych między run1 a run2.
```

Dla 1M URL przy ~50 ms/article (cold, 4 workers, GIL-free trafilatura)
estymata: ~3.5h → po cache 30–60 min na samo I/O markdownu.

## Integracja z orchestratorami

`run_spo_v1.py` / `run_spo_v2.py` zamiast:

```python
articles = load_articles(args.websites, limit=args.limit, ...)
todo = [a for a in articles if a["url_hash"] not in done_final]
for art in todo:
    bump("pending", 1)
    q_classify.put(art)
producer_done.set()
```

zrobi:

```python
from lib.streaming_loader import stream_articles_async

loader = stream_articles_async(
    args.websites, limit=args.limit, random_sample=args.random,
    seed=args.seed, n_loader_workers=4, queue_maxsize=200,
)
for art in loader:
    if art["url_hash"] in done_final:
        continue
    bump("pending", 1)
    if art["url_hash"] in done_classify and art["url_hash"] in classify_cache:
        fan_out_after_classify(art, classify_cache[art["url_hash"]])
    else:
        q_classify.put(art)
producer_done.set()
logger.info(f"loader stats: {loader.stats.as_dict()}")
```

GPU dostaje pierwsze artykuły w <1s zamiast po pełnej preprocessing fazie.

## Trade-offy

- **Disk usage**: ~5–50 KB/article markdown × 25k = 125 MB – 1.25 GB. Dla 1M
  worst case ~50 GB. Akceptowalne na DGX.
- **Cache invalidation**: prosta (wersja globalna). Nie pamięta zmian w HTML
  (`websites/<hash>/html.gz`); zakładamy że HTML jest immutable. Jeśli kiedyś
  zmieni — bump `CACHE_VERSION` w `streaming_loader.py`.
- **Determinizm kolejności**: dla `limit=0 && !random_sample` używamy
  `os.scandir()` (kolejność systemowa, nie sortowana — szybciej dla 1M).
  Dla `random_sample` lub `limit>0` zachowujemy posortowaną kolejność.
  Pipeline jest idempotentny po `url_hash`, więc kolejność nie ma znaczenia
  dla wyniku — jedynie dla porównań A/B sample.
- **n_loader_workers vs vLLM concurrency**: 4 loadery to typically dość; bottleneck
  to GPU. Dla bardzo szybkich modeli można podnieść do 8.

## TODO / otwarte

- [ ] Po integracji w `run_spo_v1.py` zmierzyć end-to-end speedup (TTFB GPU,
      całkowity wall) na 1k random sample.
- [ ] Rozważyć kompresję cache (`.md.gz`) — markdown kompresuje się ~4×, koszt
      CPU minimalny. Decyzja po pomiarze realnego miejsca na 1M.
- [ ] Metryka `bytes_read` w stats — pomocne przy disk-bound runach.
- [ ] Optional: prefetch JSON.gz parsing też do workerów (obecnie w
      `_load_one` synchronicznie, ale workerów jest 4 więc i tak parallel).
