"""Analiza wyników Phase 2 — entity_layer.jsonl + final.jsonl.

Statystyki:
- OK / fail per Step
- Latencja (median, p95) per Step
- Rozkład kategorii
- Rozkład typów encji
- Sample N najciekawszych outputów do eyeballa

Wynik: result/phase2_twostep.md + console summary.

Użycie:
    python3 scripts/analyze_phase2.py
    python3 scripts/analyze_phase2.py --samples 10
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import RESULT_DIR  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # dedup po url_hash, ostatni wins
    by_hash: dict[str, dict] = {}
    for r in out:
        h = r.get("url_hash")
        if h:
            by_hash[h] = r
    return list(by_hash.values())


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "median": round(statistics.median(s), 3),
        "p95": round(s[int(len(s) * 0.95)], 3),
        "max": round(max(s), 3),
        "mean": round(statistics.mean(s), 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity-layer", default=str(RESULT_DIR / "entity_layer.jsonl"))
    parser.add_argument("--final", default=str(RESULT_DIR / "final.jsonl"))
    parser.add_argument("--out", default=str(RESULT_DIR / "phase2_twostep.md"))
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()

    s1 = read_jsonl(Path(args.entity_layer))
    s2 = read_jsonl(Path(args.final))

    # Step 1 stats
    s1_ok = [r for r in s1 if r.get("ok")]
    s1_fail = [r for r in s1 if not r.get("ok")]
    s1_lat = [r["latency_s"] for r in s1_ok if "latency_s" in r]
    cats = Counter(r["category"] for r in s1_ok if r.get("category"))
    langs = Counter(r["language"] for r in s1_ok if r.get("language"))
    n_ent = [len(r.get("entities", [])) for r in s1_ok]
    ent_types = Counter()
    ent_name_lens = []
    for r in s1_ok:
        for e in r.get("entities", []):
            ent_types[e.get("type", "?")] += 1
            if e.get("name"):
                ent_name_lens.append(len(e["name"]))
    s1_input_t = [r["usage"].get("prompt_tokens") for r in s1_ok if r.get("usage")]
    s1_output_t = [r["usage"].get("completion_tokens") for r in s1_ok if r.get("usage")]
    s1_cached = [
        (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        for r in s1_ok if r.get("usage")
    ]
    s1_cache_hit_pct = [
        100 * c / max(p, 1)
        for c, p in zip(s1_cached, s1_input_t) if p
    ]

    # Step 2 stats
    s2_ok = [r for r in s2 if r.get("ok")]
    s2_fail = [r for r in s2 if not r.get("ok")]
    s2_lat = [r["latency_s"] for r in s2_ok if "latency_s" in r]
    s2_input_t = [r["usage"].get("prompt_tokens") for r in s2_ok if r.get("usage")]
    s2_output_t = [r["usage"].get("completion_tokens") for r in s2_ok if r.get("usage")]
    s2_cached = [
        (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        for r in s2_ok if r.get("usage")
    ]
    s2_cache_hit_pct = [
        100 * c / max(p, 1)
        for c, p in zip(s2_cached, s2_input_t) if p
    ]
    title_lens = [len(r["title"]) for r in s2_ok if r.get("title")]
    meta_lens = [len(r["meta_description"]) for r in s2_ok if r.get("meta_description")]
    h1_lens = [len(r["h1"]) for r in s2_ok if r.get("h1")]
    summary_lens = [len(r["article_summary"]) for r in s2_ok if r.get("article_summary")]

    # Build markdown report
    lines = []
    lines.append("# Phase 2: pełen run two-step (100 URL)\n")
    lines.append(f"**Próbka:** {len(s1)} Step 1 + {len(s2)} Step 2.")
    lines.append(f"**Konfiguracja:** Google defaults (temp 1.0, top_p 0.95, top_k 64), guided JSON via `response_format` xgrammar, thinking OFF.\n")

    lines.append("## Step 1 (entity extraction + category + language)\n")
    lines.append(f"- OK: **{len(s1_ok)} / {len(s1)}** ({100*len(s1_ok)/max(len(s1),1):.1f}%)")
    lines.append(f"- Fail: {len(s1_fail)}")
    lines.append(f"- Latencja (s): {stats(s1_lat)}")
    lines.append(f"- Input tokeny: {stats(s1_input_t)}")
    lines.append(f"- Output tokeny: {stats(s1_output_t)}")
    if any(c > 0 for c in s1_cached):
        lines.append(f"- Cached tokens (prefix cache hit): {stats([c for c in s1_cached if c > 0])}")
        lines.append(f"- Cache hit % per request: {stats(s1_cache_hit_pct)}")
    else:
        lines.append("- Cached tokens: brak danych (prompt_tokens_details=null lub cache nie aktywny)")
    lines.append(f"- Liczba encji per artykuł: {stats(n_ent)}")
    lines.append(f"- Długość nazw encji (znaki): {stats(ent_name_lens)}\n")

    lines.append("### Wykryte języki\n")
    for lang, n in langs.most_common(10):
        lines.append(f"- `{lang}`: {n}")
    lines.append("")

    lines.append("### Top 10 kategorii\n")
    for cat, n in cats.most_common(10):
        lines.append(f"- {cat}: {n}")
    lines.append("")

    lines.append("### Rozkład typów encji\n")
    total_ent = sum(ent_types.values())
    for t, n in ent_types.most_common(25):
        lines.append(f"- {t}: {n} ({100*n/max(total_ent,1):.1f}%)")
    lines.append("")

    lines.append("## Step 2 (SEO meta generation)\n")
    lines.append(f"- OK: **{len(s2_ok)} / {len(s2)}** ({100*len(s2_ok)/max(len(s2),1):.1f}%)")
    lines.append(f"- Fail: {len(s2_fail)}")
    lines.append(f"- Latencja (s): {stats(s2_lat)}")
    lines.append(f"- Input tokeny: {stats(s2_input_t)}")
    lines.append(f"- Output tokeny: {stats(s2_output_t)}")
    if any(c > 0 for c in s2_cached):
        lines.append(f"- Cached tokens (prefix cache hit): {stats([c for c in s2_cached if c > 0])}")
        lines.append(f"- Cache hit % per request: {stats(s2_cache_hit_pct)}\n")
    else:
        lines.append("- Cached tokens: brak danych\n")

    lines.append("### Długości pól (znaki)\n")
    lines.append(f"- title (limit 70): {stats(title_lens)}")
    lines.append(f"- meta_description (limit 160, target 140-160): {stats(meta_lens)}")
    lines.append(f"- h1 (limit 100): {stats(h1_lens)}")
    lines.append(f"- article_summary (limit 400): {stats(summary_lens)}\n")

    # Sample wyników
    lines.append(f"## Przykładowe wyniki ({args.samples} losowych)\n")
    sample = s2_ok[: args.samples]
    for i, r in enumerate(sample, 1):
        lines.append(f"### {i}. {r.get('id', '?')}  ({r.get('domain', '?')})\n")
        lines.append(f"- URL: `{r.get('url', '?')}`")
        lines.append(f"- category: **{r.get('category', '?')}** | language: **{r.get('language', '?')}**")
        lines.append(f"- title ({len(r.get('title','') or '')}): {r.get('title')}")
        lines.append(f"- meta ({len(r.get('meta_description','') or '')}): {r.get('meta_description')}")
        lines.append(f"- h1 ({len(r.get('h1','') or '')}): {r.get('h1')}")
        lines.append(f"- summary ({len(r.get('article_summary','') or '')}): {r.get('article_summary')}")
        ents = r.get("entities", [])[:8]
        if ents:
            lines.append(f"- entities (top 8 z {len(r.get('entities',[]))}): " +
                         ", ".join(f"`{e['name']}` ({e['type']})" for e in ents))
        lines.append("")

    md = "\n".join(lines)
    Path(args.out).write_text(md, encoding="utf-8")
    print(md)
    print(f"\n→ Zapisane: {args.out}")


if __name__ == "__main__":
    main()
