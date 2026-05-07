"""Loader artykułów z websites/<hash>/{html,json}.gz w formacie markdown.

Markdown daje modelowi sygnały struktury (nagłówki, linki, bold) bezpośrednio
użyteczne dla ekstrakcji encji + SEO meta. Koszt ~+2,3% długości względem
plain text — patrz CLAUDE.md sekcja "Konwencje".
"""

import gzip
import hashlib
import json
import logging
import os
from pathlib import Path

import trafilatura

from lib.config import TEXT_TRUNCATE_LIMIT

logger = logging.getLogger(__name__)


def extract_markdown_from_html_gz(file_path: str) -> str | None:
    """Ekstrahuj treść artykułu jako markdown z gzipowanego HTML."""
    try:
        with gzip.open(file_path, "rb") as f:
            html_content = f.read()
        return trafilatura.extract(
            html_content,
            output_format="markdown",
            include_links=True,
            include_formatting=True,
            include_comments=False,
            include_tables=False,
        )
    except (gzip.BadGzipFile, OSError) as e:
        logger.error(f"Error extracting markdown from {file_path}: {e}")
        return None


def get_url_from_json_gz(file_path: str) -> str | None:
    """Wyciągnij URL źródłowy z gzipowanego JSON metadata."""
    try:
        if os.path.exists(file_path):
            with gzip.open(file_path, "rb") as f:
                data = json.load(f)
                return data.get("url")
    except (gzip.BadGzipFile, json.JSONDecodeError, OSError) as e:
        logger.error(f"Error reading URL from {file_path}: {e}")
    return None


def url_hash(url: str) -> str:
    """Deterministyczny klucz dla idempotencji (sha256 hex)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def load_articles(input_dir: str | Path, limit: int = 0) -> list[dict]:
    """Skanuj katalog wejściowy i załaduj artykuły do batcha.

    Zwraca deterministycznie posortowaną listę dictów:
        {id, text (markdown), url, url_hash, text_len}.
    Artykuły, dla których trafilatura zwraca None, są pomijane.
    """
    input_path = Path(input_dir)
    articles: list[dict] = []

    subdirs = sorted(
        d for d in input_path.iterdir()
        if d.is_dir() and (d / "html.gz").exists()
    )

    for subdir in subdirs:
        if limit > 0 and len(articles) >= limit:
            break

        html_path = subdir / "html.gz"
        json_path = subdir / "json.gz"

        text = extract_markdown_from_html_gz(str(html_path))
        if not text:
            logger.warning(f"Skipping {subdir.name}: trafilatura returned None")
            continue

        if len(text) > TEXT_TRUNCATE_LIMIT:
            logger.debug(
                f"Truncating {subdir.name}: {len(text)} → {TEXT_TRUNCATE_LIMIT} chars"
            )
            text = text[:TEXT_TRUNCATE_LIMIT]

        url = get_url_from_json_gz(str(json_path))

        articles.append({
            "id": subdir.name,
            "text": text,
            "url": url,
            "url_hash": url_hash(url) if url else subdir.name,
            "text_len": len(text),
        })

    logger.info(f"Loaded {len(articles)} articles from {input_path}")
    return articles
