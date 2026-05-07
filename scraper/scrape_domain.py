#!/usr/bin/env python3
"""Scrape całej domeny → format Mateusza: websites/<md5(url)>/{html.gz, json.gz}.

Użycie:
    python scraper/scrape_domain.py --sitemap https://example.com/sitemap.xml --out-dir websites_new
    python scraper/scrape_domain.py --domain https://example.com --max-urls 500 --out-dir websites_new
    python scraper/scrape_domain.py --urls-file urls.txt --out-dir websites_new

Idempotentność: katalogi <md5> już istniejące są skipowane.
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from lxml import html as lxml_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("scraper")

SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".tar", ".gz", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".woff", ".woff2", ".ttf", ".eot",
    ".css", ".js", ".json", ".xml",
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Strip fragment, normalize trailing slash on root, lowercase host."""
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((p.scheme, netloc, path, p.params, p.query, ""))


def should_skip_url(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return True
    path_lower = p.path.lower()
    for ext in SKIP_EXTENSIONS:
        if path_lower.endswith(ext):
            return True
    return False


# ---------- discovery ----------

def discover_from_sitemap(sitemap_url: str, max_urls: int | None = None) -> list[str]:
    """Pobierz URL-e ze sitemapy (obsługuje sitemap-index rekursywnie)."""
    log.info(f"Sitemap: {sitemap_url}")
    urls: list[str] = []
    visited_sitemaps: set[str] = set()
    queue = deque([sitemap_url])
    while queue:
        sm = queue.popleft()
        if sm in visited_sitemaps:
            continue
        visited_sitemaps.add(sm)
        try:
            r = requests.get(sm, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Sitemap fetch failed {sm}: {e}")
            continue
        try:
            # strip namespace dla prostszego XPath
            content = re.sub(r'\sxmlns="[^"]+"', '', r.text, count=1)
            root = ET.fromstring(content)
        except ET.ParseError as e:
            log.warning(f"Sitemap parse failed {sm}: {e}")
            continue
        # sitemap-index?
        for sm_node in root.findall(".//sitemap/loc"):
            if sm_node.text:
                queue.append(sm_node.text.strip())
        # urlset
        for url_node in root.findall(".//url/loc"):
            if url_node.text:
                u = normalize_url(url_node.text.strip())
                if u and not should_skip_url(u):
                    urls.append(u)
                    if max_urls and len(urls) >= max_urls:
                        return urls
    log.info(f"Sitemap zwróciło {len(urls)} URL")
    return urls


def discover_from_homepage(start_url: str, max_urls: int, same_domain_only: bool = True) -> list[str]:
    """BFS od homepage; ogranicza się do tej samej domeny."""
    log.info(f"BFS crawl od {start_url} (max {max_urls} URL)")
    base_domain = urlparse(start_url).netloc.lower()
    seen: set[str] = set()
    out: list[str] = []
    queue = deque([normalize_url(start_url)])
    while queue and len(out) < max_urls:
        url = queue.popleft()
        if url in seen or should_skip_url(url):
            continue
        seen.add(url)
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
        except Exception as e:
            log.warning(f"BFS fetch fail {url}: {e}")
            continue
        out.append(url)
        try:
            tree = lxml_html.fromstring(r.content)
        except Exception:
            continue
        for link in tree.xpath("//a/@href"):
            try:
                abs_url = normalize_url(urljoin(url, link))
            except Exception:
                continue
            if not abs_url or abs_url in seen or should_skip_url(abs_url):
                continue
            if same_domain_only and urlparse(abs_url).netloc.lower() != base_domain:
                continue
            queue.append(abs_url)
    log.info(f"BFS zebrał {len(out)} URL")
    return out


def discover_from_file(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        u = normalize_url(line)
        if u and not should_skip_url(u):
            urls.append(u)
    log.info(f"Plik {path}: {len(urls)} URL")
    return urls


# ---------- parsing ----------

def extract_metadata(html: str) -> dict:
    """Wyciągnij headers (h1-h6), title, description w formacie Mateusza."""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return {"headers": [], "title": "", "description": ""}

    headers = []
    for el in tree.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(el.tag[1])
        text = " ".join((el.text_content() or "").split()).strip()
        if text:
            headers.append({"level": level, "text": text})

    title_el = tree.find(".//title")
    title = (title_el.text_content() or "").strip() if title_el is not None else ""

    description = ""
    for meta in tree.xpath('//meta[@name="description"] | //meta[@property="og:description"]'):
        content = meta.get("content", "").strip()
        if content:
            description = content
            break

    return {"headers": headers, "title": title, "description": description}


# ---------- save ----------

def save_url(out_dir: Path, url: str, html: str, url_finish: str,
             http_code: int, http_code_finish: int) -> Path:
    h = url_hash(url)
    target = out_dir / h
    target.mkdir(parents=True, exist_ok=True)

    meta = extract_metadata(html)
    record = {
        "url": url,
        "url_finish": url_finish,
        "http_code": http_code,
        "http_code_finish": http_code_finish,
        "headers": meta["headers"],
        "title": meta["title"],
        "description": meta["description"],
    }

    with gzip.open(target / "html.gz", "wb") as f:
        f.write(html.encode("utf-8"))
    with gzip.open(target / "json.gz", "wb") as f:
        f.write(json.dumps(record, ensure_ascii=False).encode("utf-8"))
    return target


# ---------- crawl ----------

async def crawl_urls(urls: list[str], out_dir: Path, concurrency: int = 4) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [u for u in urls if not (out_dir / url_hash(u)).exists()]
    skipped_existing = len(urls) - len(todo)
    log.info(f"Do pobrania: {len(todo)} (skip istniejących: {skipped_existing})")

    if not todo:
        return 0, skipped_existing

    browser_cfg = BrowserConfig(
        headless=True,
        user_agent=USER_AGENT,
        verbose=False,
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=30000,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        word_count_threshold=0,
        excluded_tags=[],
    )

    ok = 0
    fail = 0
    sem = asyncio.Semaphore(concurrency)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        async def worker(url: str, idx: int) -> None:
            nonlocal ok, fail
            async with sem:
                try:
                    res = await crawler.arun(url=url, config=run_cfg)
                    if not res.success or not res.html:
                        fail += 1
                        log.warning(f"[{idx}/{len(todo)}] FAIL {url} | "
                                    f"status={getattr(res,'status_code',None)} err={res.error_message}")
                        return
                    save_url(
                        out_dir=out_dir,
                        url=url,
                        html=res.html,
                        url_finish=getattr(res, "url", url),
                        http_code=getattr(res, "status_code", 200) or 200,
                        http_code_finish=getattr(res, "status_code", 200) or 200,
                    )
                    ok += 1
                    if idx % 10 == 0 or idx == len(todo):
                        log.info(f"[{idx}/{len(todo)}] ok={ok} fail={fail}")
                except Exception as e:
                    fail += 1
                    log.warning(f"[{idx}/{len(todo)}] EXC {url}: {e}")

        await asyncio.gather(*(worker(u, i + 1) for i, u in enumerate(todo)))

    log.info(f"DONE ok={ok} fail={fail} skipped_existing={skipped_existing}")
    return ok, fail


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape domeny → format Mateusza")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sitemap", help="URL do sitemap.xml (lub sitemap-index)")
    src.add_argument("--domain", help="URL homepage do BFS crawl")
    src.add_argument("--urls-file", help="Plik z URL-ami (1 linia = 1 URL)")

    ap.add_argument("--out-dir", required=True, help="Katalog wyjściowy (websites_new/)")
    ap.add_argument("--max-urls", type=int, default=None, help="Limit URL (None = bez limitu)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if args.sitemap:
        urls = discover_from_sitemap(args.sitemap, max_urls=args.max_urls)
    elif args.domain:
        if args.max_urls is None:
            log.error("--domain wymaga --max-urls (BFS bez limitu = niebezpieczne)")
            return 2
        urls = discover_from_homepage(args.domain, max_urls=args.max_urls)
    else:
        urls = discover_from_file(Path(args.urls_file))
        if args.max_urls:
            urls = urls[: args.max_urls]

    if not urls:
        log.error("Brak URL do scrapowania")
        return 1

    asyncio.run(crawl_urls(urls, out_dir, concurrency=args.concurrency))
    return 0


if __name__ == "__main__":
    sys.exit(main())
