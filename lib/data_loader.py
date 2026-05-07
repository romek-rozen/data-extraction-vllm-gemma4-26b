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
from urllib.parse import urlparse

import trafilatura

from lib.config import MAX_ARTICLE_TOKENS, TEXT_TRUNCATE_LIMIT
from lib.tokenizer import truncate_to_tokens

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
            include_tables=True,
        )
    except (gzip.BadGzipFile, OSError) as e:
        logger.error(f"Error extracting markdown from {file_path}: {e}")
        return None


def load_url_info_from_json_gz(file_path: str) -> dict:
    """Wczytaj minimalne info o URL z gzipowanego JSON.

    Bierzemy tylko url_finish (URL po redirectach — to jest faktyczny URL strony,
    z której pochodzi HTML). Z niego wyliczamy domenę i ścieżkę.
    Headingi/inne pola są ignorowane — black box, czasem mają błędne struktury.

    Zwraca dict: {url, domain, path} lub puste stringi jeśli brak.
    """
    try:
        if os.path.exists(file_path):
            with gzip.open(file_path, "rb") as f:
                data = json.load(f)
                url = data.get("url_finish") or data.get("url") or ""
                if url:
                    parsed = urlparse(url)
                    return {
                        "url": url,
                        "domain": parsed.netloc,
                        "path": parsed.path,
                    }
    except (gzip.BadGzipFile, json.JSONDecodeError, OSError, ValueError) as e:
        logger.error(f"Error reading URL info from {file_path}: {e}")
    return {"url": "", "domain": "", "path": ""}


def url_hash(url: str) -> str:
    """Deterministyczny klucz dla idempotencji (sha256 hex)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def load_articles(
    input_dir: str | Path,
    limit: int = 0,
    random_sample: bool = False,
    seed: int = 42,
) -> list[dict]:
    """Skanuj katalog wejściowy i załaduj artykuły do batcha.

    Zwraca deterministycznie posortowaną listę dictów:
        {id, text (markdown), url, domain, path, url_hash, text_len}.
    Artykuły, dla których trafilatura zwraca None, są pomijane.

    Args:
        random_sample: jeśli True i limit > 0, weź losową próbkę zamiast
            pierwszych N posortowanych. Reproducible — ten sam seed zawsze
            daje ten sam zestaw subkatalogów.
        seed: ziarno PRNG dla random_sample (default 42).
    """
    import random

    input_path = Path(input_dir)
    articles: list[dict] = []

    all_subdirs = [
        d for d in input_path.iterdir()
        if d.is_dir() and (d / "html.gz").exists()
    ]

    if random_sample and limit > 0 and limit < len(all_subdirs):
        # Sortuj najpierw dla determinizmu (kolejność iterdir() bywa różna),
        # potem losuj z fixed seed — daje reproducible sample.
        all_subdirs.sort()
        rng = random.Random(seed)
        chosen = rng.sample(all_subdirs, limit)
        # Po wyborze sortuj dla deterministycznej kolejności przetwarzania
        subdirs = sorted(chosen)
        logger.info(
            f"Random sample: {limit} z {len(all_subdirs)} subdirów (seed={seed})"
        )
    else:
        subdirs = sorted(all_subdirs)

    for subdir in subdirs:
        if limit > 0 and len(articles) >= limit:
            break

        html_path = subdir / "html.gz"
        json_path = subdir / "json.gz"

        text = extract_markdown_from_html_gz(str(html_path))
        if not text:
            logger.warning(f"Skipping {subdir.name}: trafilatura returned None")
            continue

        # Safety net 1: szybki char-level cap dla patologicznych outlierów.
        if len(text) > TEXT_TRUNCATE_LIMIT:
            logger.debug(
                f"Char-truncating {subdir.name}: {len(text)} → {TEXT_TRUNCATE_LIMIT} chars"
            )
            text = text[:TEXT_TRUNCATE_LIMIT]

        # Safety net 2: dokładne odcięcie po tokenach (~2 ms/req).
        text, n_tokens = truncate_to_tokens(text, MAX_ARTICLE_TOKENS)
        if n_tokens == MAX_ARTICLE_TOKENS:
            logger.warning(
                f"Token-truncated {subdir.name} to {MAX_ARTICLE_TOKENS} tokens"
            )

        url_info = load_url_info_from_json_gz(str(json_path))
        url = url_info["url"]

        articles.append({
            "id": subdir.name,
            "text": text,
            "url": url,
            "domain": url_info["domain"],
            "path": url_info["path"],
            "url_hash": url_hash(url) if url else subdir.name,
            "text_len": len(text),
            "text_tokens": n_tokens,
        })

    logger.info(f"Loaded {len(articles)} articles from {input_path}")
    return articles
