"""Streaming loader z dyskowym cache markdown.

Motywacja: `lib.data_loader.load_articles()` jest sekwencyjny — parsuje
trafilaturą wszystkie ~25k HTMLi PRZED zwróceniem listy. Dla 1M artykułów to
godziny GPU idle. Tu robimy:

- producer ThreadPoolExecutor (n_loader_workers) parsuje równolegle (gzip +
  trafilatura zwalniają GIL),
- bounded queue (`queue_maxsize`) ogranicza pamięć,
- per-hash markdown cache w `<cache_dir>/<hash>.md` — drugi run czyta z dysku
  bez trafilatury,
- generator yieluje dicty zgodne z `load_articles` (te same klucze).

Cache trzyma ORYGINALNY markdown (przed truncate), runtime stosuje budżet
tokenów — zmiana `MAX_ARTICLE_TOKENS` nie inwaliduje cache.
"""

from __future__ import annotations

import gzip
import logging
import os
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Iterable

from lib.config import MAX_ARTICLE_TOKENS, PROJECT_ROOT, TEXT_TRUNCATE_LIMIT
from lib.data_loader import (
    extract_markdown_from_html_gz,
    load_url_info_from_json_gz,
    url_hash,
)
from lib.tokenizer import truncate_to_tokens

logger = logging.getLogger(__name__)

CACHE_VERSION = "v4"  # v4: czysty JSON {"domain","url","content"} (bez cache_version w pliku — trzymane tylko w _version.txt)


def _serialize_cache(text: str, url: str, domain: str) -> str:
    """JSON object: {"domain","url","content"} — w tej kolejności."""
    import json as _json
    return _json.dumps(
        {"domain": domain or "", "url": url or "", "content": text},
        ensure_ascii=False,
    )


def _parse_cache(content: str) -> tuple[str, dict]:
    """Zwraca (body, header_dict). Wspiera v3 (czysty JSON)."""
    import json as _json
    s = content.strip()
    if not s.startswith("{"):
        return content, {}
    try:
        obj = _json.loads(s)
    except _json.JSONDecodeError:
        return content, {}
    if not isinstance(obj, dict):
        return content, {}
    body = obj.get("content", "")
    header = {k: v for k, v in obj.items() if k != "content"}
    return body, header


@dataclass
class StreamStats:
    cache_hits: int = 0
    cache_misses: int = 0
    parse_errors: int = 0
    yielded: int = 0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "parse_errors": self.parse_errors,
            "yielded": self.yielded,
            "elapsed_s": round(time.time() - self.started_at, 3),
        }


class StreamingLoader:
    """Wrapper na generator, eksponujący `.stats` po wyczerpaniu.

    Użycie:
        loader = StreamingLoader(...)
        for art in loader:
            ...
        print(loader.stats.as_dict())
    """

    def __init__(
        self,
        websites_dir: str | Path,
        *,
        limit: int = 0,
        random_sample: bool = False,
        seed: int = 42,
        n_loader_workers: int = 4,
        queue_maxsize: int = 200,
        cache_dir: str | Path | None = None,
    ):
        self.websites_dir = Path(websites_dir)
        self.limit = limit
        self.random_sample = random_sample
        self.seed = seed
        self.n_loader_workers = max(1, n_loader_workers)
        self.queue_maxsize = queue_maxsize
        self.cache_dir = Path(cache_dir) if cache_dir else (PROJECT_ROOT / "websites_cache")
        self.stats = StreamStats()
        self._init_cache()

    def _init_cache(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        version_file = self.cache_dir / "_version.txt"
        existing = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
        if existing != CACHE_VERSION:
            if existing is not None:
                logger.warning(
                    f"Cache version mismatch ({existing} != {CACHE_VERSION}); invalidating {self.cache_dir}"
                )
                # Remove cache artifacts of all known versions (.md from v1/v2, .json from v3+).
                for pat in ("*.md", "*.json"):
                    for p in self.cache_dir.glob(pat):
                        try:
                            p.unlink()
                        except OSError:
                            pass
            version_file.write_text(CACHE_VERSION, encoding="utf-8")

    def _cache_path(self, h: str) -> Path:
        return self.cache_dir / f"{h}.json"

    def _enumerate_subdirs(self) -> Iterable[Path]:
        """Lazy w przypadku !random && limit=0; inaczej pełna lista (sortowana)."""
        if not self.random_sample and self.limit == 0:
            # lazy scandir
            with os.scandir(self.websites_dir) as it:
                for entry in it:
                    if entry.is_dir():
                        p = Path(entry.path)
                        if (p / "html.gz").exists():
                            yield p
            return

        # Need full list (random sample, or limit>0 — limit>0 with random=False
        # technically can be lazy too, ale bierzemy posortowane dla determinizmu;
        # to wymaga materializacji listy).
        all_subdirs = [
            d for d in self.websites_dir.iterdir()
            if d.is_dir() and (d / "html.gz").exists()
        ]
        all_subdirs.sort()

        if self.random_sample and self.limit > 0 and self.limit < len(all_subdirs):
            rng = random.Random(self.seed)
            chosen = rng.sample(all_subdirs, self.limit)
            chosen.sort()
            logger.info(
                f"Random sample: {self.limit} z {len(all_subdirs)} subdirów (seed={self.seed})"
            )
            for p in chosen:
                yield p
            return

        for p in all_subdirs:
            yield p

    def _load_one(self, subdir: Path) -> dict | None:
        """Załaduj jeden artykuł — z cache albo trafilaturą. Zwraca dict lub None."""
        html_path = subdir / "html.gz"
        json_path = subdir / "json.gz"

        # Determine url_hash early via json.gz (URL is the canonical key).
        url_info = load_url_info_from_json_gz(str(json_path))
        url = url_info["url"]
        h = url_hash(url) if url else subdir.name

        cache_path = self._cache_path(h)
        text: str | None = None

        if cache_path.exists():
            try:
                raw = cache_path.read_text(encoding="utf-8")
                text, fm = _parse_cache(raw)
                # Sanity: jeśli frontmatter ma url i ono się różni od url_finish
                # z json.gz, ufamy json.gz (dataset truth) i regenerujemy cache.
                if fm.get("url") and url and fm["url"] != url:
                    logger.warning(f"Cache URL mismatch for {h}: cache={fm['url']!r} json={url!r}; regenerating")
                    text = None
                else:
                    self.stats.cache_hits += 1
            except OSError as e:
                logger.warning(f"Cache read fail {cache_path}: {e}")
                text = None

        if text is None:
            text = extract_markdown_from_html_gz(str(html_path))
            if not text:
                self.stats.parse_errors += 1
                return None
            self.stats.cache_misses += 1
            try:
                cache_path.write_text(
                    _serialize_cache(text, url=url or "", domain=url_info["domain"]),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.warning(f"Cache write fail {cache_path}: {e}")

        # Runtime budget (cache trzyma raw → wolno zmieniać MAX_ARTICLE_TOKENS).
        if len(text) > TEXT_TRUNCATE_LIMIT:
            text = text[:TEXT_TRUNCATE_LIMIT]
        text, n_tokens = truncate_to_tokens(text, MAX_ARTICLE_TOKENS)

        return {
            "id": subdir.name,
            "text": text,
            "url": url,
            "domain": url_info["domain"],
            "path": url_info["path"],
            "url_hash": h,
            "text_len": len(text),
            "text_tokens": n_tokens,
            "html_path": str(html_path),
            "json_path": str(json_path),
        }

    def __iter__(self) -> Generator[dict, None, None]:
        return self._stream()

    def _stream(self) -> Generator[dict, None, None]:
        q: queue.Queue = queue.Queue(maxsize=self.queue_maxsize)
        SENTINEL = object()
        stop_flag = threading.Event()

        # Producer dispatcher: jeden wątek karmi pulę worker threads. Worker
        # wkłada wynik do kolejki. Po skończeniu — sentinel × n_workers.
        def submit_loop():
            try:
                with ThreadPoolExecutor(
                    max_workers=self.n_loader_workers,
                    thread_name_prefix="strload",
                ) as pool:
                    futures = []
                    count = 0
                    for subdir in self._enumerate_subdirs():
                        if stop_flag.is_set():
                            break
                        if self.limit > 0 and count >= self.limit and not self.random_sample:
                            # Dla random_sample _enumerate_subdirs sam ogranicza.
                            break
                        count += 1

                        # bounded by queue (worker blokuje się na q.put).
                        # Submit; rezultat przepchniemy poniżej.
                        fut = pool.submit(self._load_one, subdir)
                        futures.append(fut)

                        # Drain zakończone futures, żeby ograniczyć rosnącą listę.
                        if len(futures) >= self.n_loader_workers * 4:
                            self._drain_futures(futures, q, partial=True, stop_flag=stop_flag)

                    # Drain reszty.
                    self._drain_futures(futures, q, partial=False, stop_flag=stop_flag)
            except Exception as e:
                logger.exception(f"submit_loop error: {e}")
            finally:
                # Tylko jeden sentinel — consumer to single-threaded generator.
                q.put(SENTINEL)

        producer_thread = threading.Thread(target=submit_loop, name="strload-producer", daemon=True)
        producer_thread.start()

        try:
            while True:
                item = q.get()
                if item is SENTINEL:
                    break
                if item is None:
                    continue
                self.stats.yielded += 1
                yield item
        finally:
            stop_flag.set()
            # Drain queue żeby producer mógł skończyć.
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            producer_thread.join(timeout=5)

    @staticmethod
    def _drain_futures(futures: list, q: queue.Queue, *, partial: bool, stop_flag: threading.Event):
        """Drain finished futures into queue. partial=True → tylko done."""
        i = 0
        while i < len(futures):
            f = futures[i]
            if stop_flag.is_set():
                return
            if partial and not f.done():
                i += 1
                continue
            try:
                rec = f.result()
            except Exception as e:
                logger.warning(f"loader worker error: {e}")
                rec = None
            q.put(rec)  # blokuje gdy queue pełny — backpressure
            futures.pop(i)


def stream_articles_async(
    websites_dir: str | Path,
    *,
    limit: int = 0,
    random_sample: bool = False,
    seed: int = 42,
    n_loader_workers: int = 4,
    queue_maxsize: int = 200,
    cache_dir: str | Path | None = None,
) -> StreamingLoader:
    """Zwraca StreamingLoader (iterowalny). Po skończeniu iteracji ma `.stats`.

    Output dictów ma klucze: id, text (markdown po truncate), url, domain,
    path, url_hash, text_len, text_tokens, html_path, json_path.
    """
    return StreamingLoader(
        websites_dir,
        limit=limit,
        random_sample=random_sample,
        seed=seed,
        n_loader_workers=n_loader_workers,
        queue_maxsize=queue_maxsize,
        cache_dir=cache_dir,
    )
