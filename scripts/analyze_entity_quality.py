"""Phase 4 — analiza jakości typów encji.

Pokazuje dla każdego typu:
- liczbę wystąpień
- top N najczęstszych nazw (case-insensitive)
- sample artykułów w których wystąpił

Cel: spotować "this looks wrong" patterns dla:
- substance vs therapy (witamina C → substance, dieta keto → therapy)
- brand vs organization (Apple Inc → org, Apple jako produkt → brand)
- structure vs location (Warszawa → location, Stadion Narodowy → structure)
- product vs technology (iPhone → product, React → technology)
- discipline vs activity (yoga → discipline, meditation → activity)

Użycie:
    python3 scripts/analyze_entity_quality.py
    python3 scripts/analyze_entity_quality.py --top 50 --jsonl result/entity_layer.jsonl
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import RESULT_DIR  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=str(RESULT_DIR / "entity_layer.jsonl"))
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--out", default=str(RESULT_DIR / "phase4_entity_quality.md"))
    args = parser.parse_args()

    rows = []
    with open(args.jsonl) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # dedup po url_hash, zostaje ostatni
    by_hash = {}
    for r in rows:
        if r.get("url_hash"):
            by_hash[r["url_hash"]] = r
    rows = [r for r in by_hash.values() if r.get("ok")]

    # group entities by type
    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    name_counts: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for e in r.get("entities", []):
            t = e.get("type", "?")
            name = e.get("name", "")
            name_counts[t][name.lower()] += 1
            by_type[t].append((name, r.get("id", "?")))

    # sortowanie typów po liczbie
    types_sorted = sorted(by_type.keys(), key=lambda t: -len(by_type[t]))

    md = [f"# Phase 4 — Analiza jakości typów encji\n"]
    md.append(f"**Źródło:** `{args.jsonl}`")
    md.append(f"**Artykułów (OK):** {len(rows)}")
    md.append(f"**Łączna liczba encji:** {sum(len(v) for v in by_type.values())}\n")

    md.append("## Liczba encji per typ\n")
    md.append("| Typ | # | % |")
    md.append("|---|---|---|")
    total_ent = sum(len(v) for v in by_type.values())
    for t in types_sorted:
        n = len(by_type[t])
        md.append(f"| {t} | {n} | {100*n/total_ent:.1f}% |")
    md.append("")

    md.append(f"## Top {args.top} unikalnych nazw per typ\n")
    md.append("Sprawdź czy nazwy semantycznie pasują do typu. Szczególna uwaga na:")
    md.append("- **substance** vs **therapy** (witamina/lek vs dieta/zabieg)")
    md.append("- **brand** vs **organization**")
    md.append("- **structure** vs **location**")
    md.append("- **product** vs **technology**")
    md.append("- **discipline** vs **activity**")
    md.append("- **other** — wszystkie wystąpienia (czy jest fallback dla niejasnych?)\n")

    for t in types_sorted:
        md.append(f"### `{t}` ({len(by_type[t])} łącznie, {len(name_counts[t])} unikalnych)\n")
        # case-insensitive top N
        top = name_counts[t].most_common(args.top)
        for name, n in top:
            md.append(f"- `{name}` ({n}×)")
        md.append("")

    Path(args.out).write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[:80]))  # short preview
    print(f"\n→ pełen raport: {args.out}")


if __name__ == "__main__":
    main()
