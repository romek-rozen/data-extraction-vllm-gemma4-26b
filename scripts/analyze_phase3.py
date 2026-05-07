"""Analiza A/B sampling — porównanie configów A/B/C dla Step 1 i Step 2.

Wynik: result/phase3_compare.md
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
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
    return out


def med(values):
    return statistics.median(values) if values else 0


def md_table_row(label: str, values: list) -> str:
    return f"| {label} | " + " | ".join(str(v) for v in values) + " |"


def analyze_step1(configs: list[str]) -> list[str]:
    lines = ["## Step 1 — A/B sampling\n"]
    rows = {c: read_jsonl(RESULT_DIR / f"phase3_step1_{c}.jsonl") for c in configs}

    header = "| Metryka | " + " | ".join(f"{c}" for c in configs) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(configs))

    ok_counts = [sum(1 for r in rows[c] if r.get("ok")) for c in configs]
    totals = [len(rows[c]) for c in configs]
    lines.append(md_table_row("OK / total", [f"{o}/{t}" for o, t in zip(ok_counts, totals)]))

    lat = [med([r["latency_s"] for r in rows[c] if r.get("ok")]) for c in configs]
    lines.append(md_table_row("Latency median (s)", [f"{v:.2f}" for v in lat]))

    n_ent_med = [med([len(r.get("entities", [])) for r in rows[c] if r.get("ok")]) for c in configs]
    lines.append(md_table_row("Encje median", [f"{v:.0f}" for v in n_ent_med]))

    n_ent_max = [max([len(r.get("entities", [])) for r in rows[c] if r.get("ok")] or [0]) for c in configs]
    lines.append(md_table_row("Encje max", n_ent_max))

    out_t = [med([r["usage"].get("completion_tokens", 0) for r in rows[c] if r.get("ok")]) for c in configs]
    lines.append(md_table_row("Output tokens median", [f"{v:.0f}" for v in out_t]))

    uniq_names = []
    for c in configs:
        names = set()
        for r in rows[c]:
            for e in r.get("entities", []):
                if e.get("name"):
                    names.add(e["name"].lower())
        uniq_names.append(len(names))
    lines.append(md_table_row("Unikalne nazwy encji", uniq_names))
    lines.append("")

    # rozkład typów
    lines.append("### Top typów encji per config\n")
    type_counts = {c: Counter() for c in configs}
    for c in configs:
        for r in rows[c]:
            if r.get("ok"):
                for e in r.get("entities", []):
                    type_counts[c][e.get("type", "?")] += 1
    all_types = sorted(set().union(*[set(t) for t in type_counts.values()]),
                       key=lambda x: -sum(type_counts[c][x] for c in configs))
    lines.append("| Typ | " + " | ".join(configs) + " |")
    lines.append("|---|" + "---|" * len(configs))
    for t in all_types[:15]:
        lines.append(f"| {t} | " + " | ".join(str(type_counts[c][t]) for c in configs) + " |")
    lines.append("")

    # stabilność kategorii
    lines.append("### Stabilność kategorii (czy ten sam URL dostaje tę samą kategorię?)\n")
    by_hash = defaultdict(dict)
    for c in configs:
        for r in rows[c]:
            if r.get("ok"):
                by_hash[r["url_hash"]][c] = r.get("category")
    matched = [v for v in by_hash.values() if len(v) == len(configs)]
    if matched:
        same = sum(1 for v in matched if len(set(v.values())) == 1)
        diff2 = sum(1 for v in matched if len(set(v.values())) == 2)
        diff3 = sum(1 for v in matched if len(set(v.values())) >= 3)
        lines.append(f"- Identyczna kategoria w wszystkich configach: **{same}/{len(matched)}** ({100*same/len(matched):.0f}%)")
        lines.append(f"- 2 różne: {diff2}/{len(matched)}")
        lines.append(f"- 3+ różne: {diff3}/{len(matched)}")
    lines.append("")

    return lines


def analyze_step2(configs: list[str]) -> list[str]:
    lines = ["## Step 2 — A/B sampling\n"]
    rows = {c: read_jsonl(RESULT_DIR / f"phase3_step2_{c}.jsonl") for c in configs}

    lines.append("| Metryka | " + " | ".join(configs) + " |")
    lines.append("|---|" + "---|" * len(configs))

    ok_counts = [sum(1 for r in rows[c] if r.get("ok")) for c in configs]
    totals = [len(rows[c]) for c in configs]
    lines.append(md_table_row("OK / total", [f"{o}/{t}" for o, t in zip(ok_counts, totals)]))
    lines.append(md_table_row("Latency median (s)",
                              [f"{med([r['latency_s'] for r in rows[c] if r.get('ok')]):.2f}" for c in configs]))
    for fld in ("title", "meta_description", "h1", "article_summary"):
        vals = [med([len(r[fld]) for r in rows[c] if r.get("ok") and r.get(fld)]) for c in configs]
        lines.append(md_table_row(f"{fld} len median", [f"{v:.0f}" for v in vals]))
    lines.append(md_table_row("Output tokens median",
                              [f"{med([r['usage'].get('completion_tokens', 0) for r in rows[c] if r.get('ok')]):.0f}" for c in configs]))
    lines.append("")

    lines.append("### Diversity (unikalne tytuły / meta z 100)\n")
    for fld in ("title", "meta_description"):
        vals = [len(set(r.get(fld) for r in rows[c] if r.get("ok") and r.get(fld))) for c in configs]
        lines.append(md_table_row(f"Unikalne {fld}", vals))
    lines.append("")

    # fail diagnose
    lines.append("### Failed records\n")
    any_fail = False
    for c in configs:
        for r in rows[c]:
            if not r.get("ok"):
                any_fail = True
                lines.append(f"- **{c}** `{r.get('id')}`: {r.get('error', '')[:200]}")
                lines.append(f"  - finish_reason={r.get('finish_reason')}, completion_tokens={r.get('usage', {}).get('completion_tokens')}")
    if not any_fail:
        lines.append("Brak (wszystkie configi 100/100 OK).")
    lines.append("")

    # stabilność tytułów/meta między configami
    lines.append("### Stabilność outputów między configami (czy ten sam URL dostaje to samo?)\n")
    by_hash = defaultdict(dict)
    for c in configs:
        for r in rows[c]:
            if r.get("ok"):
                by_hash[r["url_hash"]][c] = r
    matched = [v for v in by_hash.values() if len(v) == len(configs)]
    for fld in ("title", "meta_description", "h1"):
        same = sum(1 for v in matched if len(set(v[c].get(fld) for c in configs)) == 1)
        lines.append(f"- Identyczne `{fld}` (A==B==C): {same}/{len(matched)} ({100*same/max(len(matched),1):.0f}%)")
    lines.append("")

    # sample side-by-side
    if matched:
        lines.append("### Side-by-side (3 pierwsze URL)\n")
        for i, v in enumerate(matched[:3]):
            r0 = v[configs[0]]
            lines.append(f"#### {i+1}. {r0.get('url', '?')}\n")
            for c in configs:
                r = v[c]
                lines.append(f"**{c}:**")
                lines.append(f"- title: {r.get('title')}")
                lines.append(f"- meta:  {r.get('meta_description')}")
                lines.append(f"- h1:    {r.get('h1')}")
                lines.append("")

    return lines


def analyze_consistency(step: int, config: str, runs: int) -> list[str]:
    lines = [f"## Consistency Step {step} config {config} ({runs}× rerun, sequential)\n"]
    runs_data = []
    for i in range(1, runs + 1):
        path = RESULT_DIR / f"phase3_step{step}_{config}_x{i}.jsonl"
        runs_data.append(read_jsonl(path))
    if not runs_data[0]:
        lines.append("Brak danych.")
        return lines

    by_hash = defaultdict(list)
    for run_recs in runs_data:
        for rec in run_recs:
            by_hash[rec["url_hash"]].append(rec)
    matched = [recs for recs in by_hash.values() if len(recs) == runs and all(r.get("ok") for r in recs)]
    lines.append(f"Wspólne (OK we wszystkich {runs} runach): **{len(matched)}**\n")

    if step == 1:
        identical = same_set = same_count = 0
        for recs in matched:
            tuples = [tuple(sorted((e.get("name", ""), e.get("type", "")) for e in r.get("entities", []))) for r in recs]
            if len(set(tuples)) == 1:
                identical += 1
            sets_only_names = [frozenset(e.get("name", "").lower() for e in r.get("entities", [])) for r in recs]
            if len(set(sets_only_names)) == 1:
                same_set += 1
            counts = [len(r.get("entities", [])) for r in recs]
            if len(set(counts)) == 1:
                same_count += 1
        lines.append(f"- Pełna identyczność (name+type, kolejność): **{identical}/{len(matched)}** ({100*identical/max(len(matched),1):.0f}%)")
        lines.append(f"- Same nazwy bez kolejności: {same_set}/{len(matched)}")
        lines.append(f"- Sama liczba encji: {same_count}/{len(matched)}")

        # przykład rozbieżności
        for recs in matched:
            tuples = [tuple(sorted((e.get("name", ""), e.get("type", "")) for e in r.get("entities", []))) for r in recs]
            if len(set(tuples)) > 1:
                lines.append(f"\n**Przykład różnicy** dla `{recs[0].get('id')}`:")
                for i, r in enumerate(recs, 1):
                    names = [e.get("name") for e in r.get("entities", [])]
                    lines.append(f"- run {i}: {names}")
                break
    else:
        identical_title = identical_meta = 0
        for recs in matched:
            if len(set(r.get("title") for r in recs)) == 1:
                identical_title += 1
            if len(set(r.get("meta_description") for r in recs)) == 1:
                identical_meta += 1
        lines.append(f"- Identyczne tytuły: {identical_title}/{len(matched)}")
        lines.append(f"- Identyczne meta_description: {identical_meta}/{len(matched)}")
    lines.append("")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", default="A,B,C")
    parser.add_argument("--out", default=str(RESULT_DIR / "phase3_compare.md"))
    parser.add_argument("--consistency-runs", type=int, default=0)
    parser.add_argument("--consistency-configs", default="A,C")
    args = parser.parse_args()

    configs = args.configs.split(",")
    md = ["# Phase 3: A/B sampling — porównanie configów\n"]
    md.append(f"**Configs Step 1:** A=(1.0,0.95,64) B=(0.7,0.9,50) C=(0.3,0.9,40)")
    md.append(f"**Configs Step 2:** A=(1.0,0.95,64) B=(0.8,0.9,50) C=(0.5,0.9,40)\n")

    if (RESULT_DIR / f"phase3_step1_{configs[0]}.jsonl").exists():
        md.extend(analyze_step1(configs))
    if (RESULT_DIR / f"phase3_step2_{configs[0]}.jsonl").exists():
        md.extend(analyze_step2(configs))

    if args.consistency_runs > 0:
        for c in args.consistency_configs.split(","):
            for step in (1, 2):
                if (RESULT_DIR / f"phase3_step{step}_{c}_x1.jsonl").exists():
                    md.extend(analyze_consistency(step, c, args.consistency_runs))

    text = "\n".join(md)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
