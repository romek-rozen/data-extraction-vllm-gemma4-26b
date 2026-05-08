"""Smoke test streaming loadera + disk cache.

Pierwsze uruchomienie: cold (cache misses).
Drugie uruchomienie tych samych N: hot (cache hits, dużo szybsze).
Sprawdza spójność tekstu między run1 i run2.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import WEBSITES_DIR  # noqa: E402
from lib.streaming_loader import stream_articles_async  # noqa: E402

LIMIT = 20
WORKERS = 4


def run(label: str) -> dict[str, dict]:
    t0 = time.perf_counter()
    loader = stream_articles_async(
        WEBSITES_DIR,
        limit=LIMIT,
        random_sample=False,
        n_loader_workers=WORKERS,
        queue_maxsize=64,
    )
    by_hash: dict[str, dict] = {}
    for art in loader:
        by_hash[art["url_hash"]] = art
    dt = time.perf_counter() - t0
    print(f"[{label}] wall={dt:.2f}s  yielded={loader.stats.yielded}  "
          f"hits={loader.stats.cache_hits}  miss={loader.stats.cache_misses}  "
          f"err={loader.stats.parse_errors}")
    return by_hash


def main():
    print(f"=== smoke test streaming_loader (limit={LIMIT}, workers={WORKERS}) ===")
    print(f"websites: {WEBSITES_DIR}")
    run1 = run("run1 (cold)")
    run2 = run("run2 (hot)")

    # Compare
    keys1 = set(run1)
    keys2 = set(run2)
    if keys1 != keys2:
        print(f"FAIL: hash sets differ: only_in_1={len(keys1 - keys2)} only_in_2={len(keys2 - keys1)}")
        sys.exit(1)

    mismatches = 0
    for h, a1 in run1.items():
        a2 = run2[h]
        if a1["text"] != a2["text"]:
            mismatches += 1
            if mismatches <= 3:
                print(f"  diff {h}: len1={len(a1['text'])} len2={len(a2['text'])}")
    if mismatches:
        print(f"FAIL: {mismatches} text mismatches between runs")
        sys.exit(1)

    print(f"OK: {len(run1)} artykułów spójnych między run1 a run2")


if __name__ == "__main__":
    main()
