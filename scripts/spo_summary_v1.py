"""SPO summary generator — analizuje wyniki run_spo_v1.py.

Czyta `final_results/<run>/{final.jsonl, classified.jsonl, run_meta.json}` i
generuje `SUMMARY.md` z:
- liczniki classify/entities/triples
- wall, throughput
- top 100 predicates (count, %)
- top 50 central entities (cross-article)
- entity type × is_central heatmapa
- domain × junk_rate × triples_per_article
- predicate length histogram
- 30 random sample triples (do eyeball)

Idempotentny — można wywoływać wielokrotnie. Auto-call z run_spo_v1.py po runie.

Użycie:
    python3 scripts/spo_summary_v1.py --out-dir final_results/<ts>__spo_v1_<tag>
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _format_top(counter: Counter, n: int, total: int) -> str:
    rows = []
    for i, (k, v) in enumerate(counter.most_common(n), 1):
        pct = v / total * 100 if total else 0
        rows.append(f"| {i} | `{k}` | {v} | {pct:.2f}% |")
    return "\n".join(rows)


def _predicate_word_lengths(triples: list[dict]) -> Counter:
    c = Counter()
    for t in triples:
        p = t.get("p", "")
        n_words = len(p.split())
        c[n_words] += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="Katalog runa (final_results/<ts>__spo_v1_<tag>)")
    ap.add_argument("--top-predicates", type=int, default=100)
    ap.add_argument("--top-entities", type=int, default=50)
    ap.add_argument("--sample-triples", type=int, default=30)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.exists():
        sys.exit(f"out_dir {out_dir} nie istnieje")

    final = _read_jsonl(out_dir / "final.jsonl")
    # Dedup po url_hash (zostaje OSTATNI dla danego klucza — resume safety)
    by_hash: dict[str, dict] = {}
    for r in final:
        h = r.get("url_hash")
        if h:
            by_hash[h] = r
    records = list(by_hash.values())

    run_meta_path = out_dir / "run_meta.json"
    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.exists() else {}

    n_total = len(records)
    n_junk = sum(1 for r in records if r.get("is_junk"))
    n_ok = sum(1 for r in records if r.get("ok") and not r.get("is_junk"))
    n_fail = sum(1 for r in records if not r.get("ok"))

    # Per-domain
    domain_total: Counter = Counter()
    domain_junk: Counter = Counter()
    domain_triples: defaultdict = defaultdict(int)
    domain_articles_with_triples: defaultdict = defaultdict(int)

    pred_counter: Counter = Counter()
    central_counter: Counter = Counter()  # canonical name (entity.name) → count of articles where it's central
    type_central_counter: Counter = Counter()  # (type, is_central) → count of entity occurrences
    type_overall: Counter = Counter()

    all_triples_for_sample: list[tuple[str, dict]] = []  # (url, triple)
    s_unmatched_total = 0
    triples_total = 0
    entities_total = 0

    for r in records:
        domain = r.get("domain", "?")
        domain_total[domain] += 1
        if r.get("is_junk"):
            domain_junk[domain] += 1
            continue
        if not r.get("ok"):
            continue
        ents = r.get("entities", [])
        triples = r.get("triples", [])
        entities_total += len(ents)
        triples_total += len(triples)
        s_unmatched_total += r.get("triples_s_unmatched", 0)

        if triples:
            domain_triples[domain] += len(triples)
            domain_articles_with_triples[domain] += 1

        # Entities
        seen_central_names = set()
        for e in ents:
            t = e.get("type", "?")
            type_overall[t] += 1
            if e.get("is_central"):
                type_central_counter[t] += 1
                nm = e.get("name", "?")
                if nm not in seen_central_names:
                    central_counter[nm] += 1
                    seen_central_names.add(nm)

        # Predicates
        for tr in triples:
            pred_counter[tr.get("p", "")] += 1
            all_triples_for_sample.append((r.get("url", ""), tr))

    # Predicate word length
    word_len_dist = Counter()
    for tr_list in (r.get("triples", []) for r in records if r.get("ok")):
        word_len_dist.update(_predicate_word_lengths(tr_list))

    # Top domains by junk_rate (min 5 articles)
    domain_rows = []
    for d, total in domain_total.most_common():
        junk = domain_junk.get(d, 0)
        triples_count = domain_triples.get(d, 0)
        articles_with_t = domain_articles_with_triples.get(d, 0)
        if total >= 5:
            junk_rate = junk / total * 100
            avg_triples = triples_count / articles_with_t if articles_with_t else 0
            domain_rows.append((d, total, junk, junk_rate, avg_triples))
    domain_rows.sort(key=lambda x: -x[3])  # po junk_rate desc

    # Type × is_central
    type_table_rows = []
    for t, total in type_overall.most_common():
        central = type_central_counter.get(t, 0)
        pct = central / total * 100 if total else 0
        type_table_rows.append((t, total, central, pct))

    # Sample triples (random)
    rng = random.Random(42)
    sample = rng.sample(all_triples_for_sample, min(args.sample_triples, len(all_triples_for_sample))) if all_triples_for_sample else []

    # MARKDOWN
    md = []
    md.append(f"# SPO Run Summary — {out_dir.name}\n")

    md.append("## Run metadata\n")
    if run_meta:
        md.append(f"- Pipeline: `{run_meta.get('pipeline', '?')}`")
        md.append(f"- Pattern: `{run_meta.get('pattern', '?')}`")
        md.append(f"- Concurrency: {run_meta.get('concurrency', '?')}")
        md.append(f"- Random sample: {run_meta.get('random_sample', '?')}, seed={run_meta.get('seed', '?')}")
        md.append(f"- Wall: {run_meta.get('wall_s', 0):.1f}s "
                  f"({run_meta.get('wall_s', 0)/3600:.2f} h)")
        md.append(f"- Started: {run_meta.get('started_at', '?')}, Ended: {run_meta.get('ended_at', '?')}")
        md.append(f"- N articles: {run_meta.get('n_articles', '?')}, N todo: {run_meta.get('n_todo', '?')}")
    md.append("")

    md.append("## Counters\n")
    md.append(f"- Total records: **{n_total}**")
    md.append(f"- Junk: **{n_junk}** ({n_junk/n_total*100:.2f}%)")
    md.append(f"- Non-junk OK: **{n_ok}** ({n_ok/n_total*100:.2f}%)")
    md.append(f"- Fails: **{n_fail}** ({n_fail/n_total*100:.2f}%)")
    md.append(f"- Entities total: **{entities_total}** (avg/non-junk: {entities_total/max(n_ok,1):.2f})")
    md.append(f"- Triples total: **{triples_total}** (avg/non-junk: {triples_total/max(n_ok,1):.2f})")
    md.append(f"- Triples with subject ∉ entities: **{s_unmatched_total}** "
              f"({s_unmatched_total/max(triples_total,1)*100:.2f}% — niższe = lepiej)")
    md.append(f"- Unique predicates: **{len(pred_counter)}**")
    md.append(f"- Unique central entity names: **{len(central_counter)}**")
    md.append("")

    md.append(f"## Top {args.top_predicates} predicates (free-form bootstrap)\n")
    md.append("| # | predicate | count | % of all triples |")
    md.append("|---|---|---|---|")
    md.append(_format_top(pred_counter, args.top_predicates, triples_total))
    md.append("")

    md.append("## Predicate word-length distribution\n")
    md.append("| n_words | count | % |")
    md.append("|---|---|---|")
    for n_words in sorted(word_len_dist.keys()):
        cnt = word_len_dist[n_words]
        md.append(f"| {n_words} | {cnt} | {cnt/triples_total*100 if triples_total else 0:.2f}% |")
    md.append("")

    md.append(f"## Top {args.top_entities} central entities (cross-article)\n")
    md.append("| # | name | n_articles |")
    md.append("|---|---|---|")
    for i, (nm, cnt) in enumerate(central_counter.most_common(args.top_entities), 1):
        md.append(f"| {i} | `{nm}` | {cnt} |")
    md.append("")

    md.append("## Entity type × is_central\n")
    md.append("| type | total occurrences | is_central=true | % central |")
    md.append("|---|---|---|---|")
    for t, total, central, pct in type_table_rows:
        md.append(f"| {t} | {total} | {central} | {pct:.2f}% |")
    md.append("")

    md.append("## Top 30 domains by junk rate (min 5 articles)\n")
    md.append("| domain | total | junk | junk% | avg triples/non-junk |")
    md.append("|---|---|---|---|---|")
    for d, total, junk, junk_rate, avg_triples in domain_rows[:30]:
        md.append(f"| {d} | {total} | {junk} | {junk_rate:.2f}% | {avg_triples:.2f} |")
    md.append("")

    md.append(f"## {len(sample)} sample triples (random, seed=42)\n")
    md.append("| url | s | p | o |")
    md.append("|---|---|---|---|")
    for url, t in sample:
        s = (t.get("s") or "").replace("|", "\\|")[:60]
        p = (t.get("p") or "").replace("|", "\\|")[:30]
        o = (t.get("o") or "").replace("|", "\\|")[:80]
        u_short = (url or "")[:60]
        md.append(f"| {u_short} | {s} | {p} | {o} |")
    md.append("")

    md.append("## Decision feed (analiza bottom-up dla closed vocab v2)\n")
    md.append("Heurystyki do podjęcia decyzji o closed enum predicates v2:")
    md.append(f"- Top-50 predicates pokrywa: **{sum(c for _, c in pred_counter.most_common(50))/max(triples_total,1)*100:.2f}%** triples")
    md.append(f"- Top-100 predicates pokrywa: **{sum(c for _, c in pred_counter.most_common(100))/max(triples_total,1)*100:.2f}%** triples")
    md.append(f"- Long-tail (poniżej top-100): **{(triples_total - sum(c for _, c in pred_counter.most_common(100)))/max(triples_total,1)*100:.2f}%**")
    md.append("")

    summary_path = out_dir / "SUMMARY.md"
    summary_path.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {summary_path} ({len(md)} lines)")


if __name__ == "__main__":
    main()
