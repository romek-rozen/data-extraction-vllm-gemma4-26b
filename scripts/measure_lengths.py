"""Phase 1: pomiar dystrybucji długości po cleanup HTML.

Dla N URL z websites/ porównuje:
- BEFORE: surowy HTML (znaki)
- MARKDOWN: trafilatura output_format='markdown' z formatting+links (znaki + tokeny)
- PLAIN: trafilatura bez formatting (znaki + tokeny)

Tokeny liczone tokenizerem Gemma 4 (z lokalnego katalogu modelu).
Wynik: result/phase1_lengths.json + tabelka median/p95/max do PLAN.md.

Użycie:
    python3 scripts/measure_lengths.py --limit 100
"""

import argparse
import gzip
import json
import logging
import statistics
import sys
from pathlib import Path

import trafilatura

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import RESULT_DIR, WEBSITES_DIR  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def extract_md(html: bytes) -> str:
    return trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_formatting=True,
        include_comments=False,
        include_tables=True,
    ) or ""


def extract_plain(html: bytes) -> str:
    return trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
    ) or ""


from lib.tokenizer import count_tokens  # noqa: E402  szybki Rust tokenizer


def stats(values: list[int]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": int(statistics.median(values)),
        "p95": int(sorted(values)[int(len(values) * 0.95)]),
        "max": max(values),
        "mean": int(statistics.mean(values)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--websites", default=str(WEBSITES_DIR))
    parser.add_argument("--out", default=str(RESULT_DIR / "phase1_lengths.json"))
    args = parser.parse_args()

    websites = Path(args.websites)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)


    subdirs = sorted(d for d in websites.iterdir()
                     if d.is_dir() and (d / "html.gz").exists())[:args.limit]

    rows: list[dict] = []
    for i, sub in enumerate(subdirs, 1):
        try:
            with gzip.open(sub / "html.gz", "rb") as f:
                html = f.read()
        except (gzip.BadGzipFile, OSError) as e:
            logger.warning(f"Skip {sub.name}: {e}")
            continue

        md = extract_md(html)
        plain = extract_plain(html)
        if not md or not plain:
            logger.warning(f"Skip {sub.name}: trafilatura returned empty")
            continue

        md_t = count_tokens(md)
        plain_t = count_tokens(plain)

        rows.append({
            "id": sub.name,
            "html_chars": len(html),
            "md_chars": len(md),
            "plain_chars": len(plain),
            "md_tokens": md_t,
            "plain_tokens": plain_t,
        })

        if i % 25 == 0:
            logger.info(f"{i}/{len(subdirs)}")

    # statystyki
    summary = {
        "n_articles": len(rows),
        "html_chars": stats([r["html_chars"] for r in rows]),
        "md_chars": stats([r["md_chars"] for r in rows]),
        "plain_chars": stats([r["plain_chars"] for r in rows]),
    }
    md_tokens = [r["md_tokens"] for r in rows if r["md_tokens"] is not None]
    plain_tokens = [r["plain_tokens"] for r in rows if r["plain_tokens"] is not None]
    if md_tokens:
        summary["md_tokens"] = stats(md_tokens)
        summary["plain_tokens"] = stats(plain_tokens)

    # markdown overhead vs plain
    if rows and rows[0]["md_tokens"] is not None:
        overhead_pct = [
            100 * (r["md_tokens"] - r["plain_tokens"]) / max(r["plain_tokens"], 1)
            for r in rows if r["md_tokens"] and r["plain_tokens"]
        ]
        summary["markdown_overhead_pct"] = {
            "median": round(statistics.median(overhead_pct), 2),
            "mean": round(statistics.mean(overhead_pct), 2),
            "max": round(max(overhead_pct), 2),
        }

    # cleanup ratio html→md (znaki)
    cleanup_pct = [
        100 * (1 - r["md_chars"] / max(r["html_chars"], 1)) for r in rows
    ]
    summary["html_to_md_reduction_pct"] = {
        "median": round(statistics.median(cleanup_pct), 2),
        "mean": round(statistics.mean(cleanup_pct), 2),
    }

    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    logger.info(f"Zapisane: {out_path}")

    # print short tabelkę do konsoli
    print("\n=== Phase 1: dystrybucja długości ===")
    print(f"N artykułów: {summary['n_articles']}")
    for k in ("html_chars", "md_chars", "plain_chars"):
        s = summary[k]
        print(f"  {k:14} median={s['median']:>8}  p95={s['p95']:>8}  max={s['max']:>8}")
    if "md_tokens" in summary:
        for k in ("md_tokens", "plain_tokens"):
            s = summary[k]
            print(f"  {k:14} median={s['median']:>8}  p95={s['p95']:>8}  max={s['max']:>8}")
        print(f"\n  Markdown overhead vs plain (tokens): median={summary['markdown_overhead_pct']['median']}%  max={summary['markdown_overhead_pct']['max']}%")
    print(f"  HTML → Markdown cleanup (znaki): median={summary['html_to_md_reduction_pct']['median']}%")


if __name__ == "__main__":
    main()
