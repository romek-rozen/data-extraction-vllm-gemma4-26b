"""Deterministyczny pre-classifier dla URL — łapie 100% pewne junk patterns
PRZED wywołaniem LLM. Oszczędza tokeny + 0.2-0.6s/URL + zwiększa recall na
listingach (klasyfikator LLM w v2 miał 27% miss rate na /tag/ pages).

Zasada: tylko patterny dla których prose-content jest **ekstremalnie rzadko**
substantive (tag pages, author archives, search results, paginated archives).
NIE łapie `/category/` ani `/page/N/` — tam zostawiamy LLM (e-commerce
description vs pagination).

Użycie:
    from lib.junk_pre_filter import is_definite_url_junk, build_junk_stub
    if is_definite_url_junk(article["url"], article["path"]):
        rec = build_junk_stub(article, reason="url_pattern_tag")
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

# Compiled patterns. Match na PATH (po normalizacji) lub na URL/query.
# Każdy element: (regex, reason_label).
PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|/)(tag|tags|tagi)/", re.IGNORECASE),       "tag_listing"),
    (re.compile(r"(^|/)(author|autor)/",  re.IGNORECASE),       "author_archive"),
    (re.compile(r"(^|/)(archive|archives|archiwum)/", re.IGNORECASE), "date_archive"),
    (re.compile(r"(^|/)(search|szukaj|wyszukaj)/?", re.IGNORECASE),   "search_results"),
    # Type-only labels (rzadziej, ale spotykane): /label/, /labels/
    (re.compile(r"(^|/)(label|labels|etykieta|etykiety)/", re.IGNORECASE), "label_listing"),
]

QUERY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # WordPress search
    (re.compile(r"(^|&)s=", re.IGNORECASE),                "wp_search_query"),
    # Pagination via query (start=N, paged=N, page=N — N > 0)
    (re.compile(r"(^|&)(paged|start)=\d+", re.IGNORECASE), "paginated_query"),
]


def is_definite_url_junk(
    url: str | None = None,
    path: str | None = None,
    query: str | None = None,
) -> tuple[bool, str | None]:
    """Sprawdź czy URL ma deterministyczny pattern junkowy.

    Args:
        url: pełny URL (jeśli podany, path/query wyciągane z niego gdy brak).
        path: path component (override).
        query: query string component (override).

    Returns:
        (is_junk, reason). reason=None gdy not junk.
    """
    if url and (path is None or query is None):
        try:
            p = urlparse(url)
            if path is None:
                path = p.path or ""
            if query is None:
                query = p.query or ""
        except Exception:
            pass
    path = path or ""
    query = query or ""

    for rx, reason in PATH_PATTERNS:
        if rx.search(path):
            return True, reason
    for rx, reason in QUERY_PATTERNS:
        if rx.search(query):
            return True, reason
    return False, None


def build_junk_stub(article: dict, reason: str) -> dict:
    """Buduj rekord classified.jsonl identyczny z LLM-output, ale ml_skipped=True."""
    return {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "path": article.get("path", ""),
        "text_tokens": article.get("text_tokens", 0),
        "ok": True,
        "error": None,
        "is_junk": True,
        "raw": "1",
        "latency_s": 0.0,
        "usage": {},
        "finish_reason": "url_pre_filter",
        "attempts": 0,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ml_skipped": True,
        "junk_reason": reason,
    }
