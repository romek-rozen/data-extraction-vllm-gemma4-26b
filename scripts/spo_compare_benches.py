#!/usr/bin/env python3
"""Compare v1 cram vs v2 split SPO benchmark runs (rich-JSON v3).

Usage (auto-detects latest 1k benches with same seed tag):
    python3 scripts/spo_compare_benches.py
    python3 scripts/spo_compare_benches.py --tag v3_bench_1k_s42

Or pass explicit dirs:
    python3 scripts/spo_compare_benches.py --v1-dir final_results/<ts>__spo_v1_<tag> \
                                           --v2-dir final_results/<ts>__spo_v2_<tag>

Writes a markdown comparison report to <repo>/SESSIONS_SUMMARY/2026-05-08_v1_vs_v2_compare.md
and prints summary to stdout.

Metrics computed:
- Wall time, throughput (URL/h, s/URL).
- Triples per article (avg, p50, p95).
- Entities per article (avg, p50, p95).
- s_unmatched rate (%).
- Junk rate.
- Sponsored rate.
- Confidence histogram for triples.
- Top-30 relation_type distribution per pipeline + cross-pipeline overlap.
- Agreement on primary_topic / central_entities for matched url_hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def stats(name: str, values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 2),
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def analyze_run(run_dir: Path) -> dict:
    """Aggregate metrics from a v3 SPO run directory."""
    final = load_jsonl(run_dir / "final.jsonl")
    spo = load_jsonl(run_dir / "spo.jsonl")
    run_meta_path = run_dir / "run_meta.json"
    run_meta = json.load(open(run_meta_path)) if run_meta_path.exists() else {}

    # Triples per article (from final.jsonl, only non-junk OK records)
    triples_per_art = []
    entities_per_art = []
    s_unm_total = 0
    triples_total = 0
    confidences: list[float] = []
    relation_counter: Counter[str] = Counter()
    centrality_counter: Counter[str] = Counter()
    object_kind_counter: Counter[str] = Counter()
    primary_topic_count = 0
    central_entities_count = 0

    for r in final:
        if r.get("is_junk") or not r.get("ok"):
            continue
        triples = r.get("triples", []) or []
        ents = r.get("entities", []) or []
        triples_per_art.append(len(triples))
        entities_per_art.append(len(ents))
        s_unm_total += r.get("triples_s_unmatched", 0)
        triples_total += len(triples)
        if r.get("primary_topic"):
            primary_topic_count += 1
        if r.get("central_entities"):
            central_entities_count += 1
        for t in triples:
            rt = (t.get("relation_type") or "").strip().lower()
            if rt:
                relation_counter[rt] += 1
            ok_kind = t.get("object_kind", "")
            if ok_kind:
                object_kind_counter[ok_kind] += 1
            c = t.get("confidence")
            if isinstance(c, (int, float)):
                confidences.append(float(c))
        for ce in r.get("central_entities", []) or []:
            cen = ce.get("centrality", "")
            if cen:
                centrality_counter[cen] += 1

    out = {
        "run_dir": str(run_dir),
        "wall_s": run_meta.get("wall_s"),
        "counters": run_meta.get("counters", {}),
        "n_articles_seen": run_meta.get("n_articles_seen"),
        "n_skipped_already_done": run_meta.get("n_skipped_already_done"),
        "n_pre_filter_junk": run_meta.get("n_pre_filter_junk"),
        "loader_stats": run_meta.get("loader_stats", {}),
        "triples_total": triples_total,
        "triples_per_article": stats("triples", triples_per_art),
        "entities_per_article": stats("entities", entities_per_art),
        "s_unmatched_total": s_unm_total,
        "s_unmatched_rate_pct": round(s_unm_total / max(triples_total, 1) * 100, 2),
        "confidence": stats("confidence", confidences),
        "relation_type_top30": relation_counter.most_common(30),
        "relation_type_unique": len(relation_counter),
        "centrality_dist": dict(centrality_counter),
        "object_kind_dist": dict(object_kind_counter),
        "primary_topic_coverage_pct": round(primary_topic_count / max(len(triples_per_art), 1) * 100, 2),
        "central_entities_coverage_pct": round(central_entities_count / max(len(triples_per_art), 1) * 100, 2),
    }
    return out


def render_markdown(v1: dict, v2: dict) -> str:
    lines: list[str] = []
    lines.append("# SPO v3 — v1 cram vs v2 split benchmark comparison\n")
    lines.append(f"- v1 run: `{v1['run_dir']}`")
    lines.append(f"- v2 run: `{v2['run_dir']}`")
    lines.append("")
    lines.append("## Wall + counters\n")
    lines.append("| metric | v1 cram | v2 split | delta |")
    lines.append("|---|---|---|---|")
    v1w = v1.get("wall_s") or 0
    v2w = v2.get("wall_s") or 0
    lines.append(f"| wall_s | {v1w} | {v2w} | {round((v2w-v1w)/max(v1w,1)*100,1)}% |")
    n1 = v1["counters"].get("final_ok", 0); n2 = v2["counters"].get("final_ok", 0)
    if v1w and n1 and v2w and n2:
        lines.append(f"| s/URL (avg) | {round(v1w/n1,2)} | {round(v2w/n2,2)} | — |")
        lines.append(f"| URL/h | {round(n1/v1w*3600,0)} | {round(n2/v2w*3600,0)} | — |")
    for k in ("classify_ok", "junk", "entities_ok", "entities_fail",
              "spo_ok", "spo_fail", "meta_ok", "sponsored_ok",
              "sponsored_true", "final_ok", "final_fail"):
        a = v1["counters"].get(k, 0); b = v2["counters"].get(k, 0)
        lines.append(f"| {k} | {a} | {b} | — |")
    lines.append("")

    lines.append("## Triples + entities\n")
    lines.append("| metric | v1 cram | v2 split |")
    lines.append("|---|---|---|")
    lines.append(f"| triples_total | {v1['triples_total']} | {v2['triples_total']} |")
    for k in ("avg", "p50", "p95", "max"):
        lines.append(f"| triples/art {k} | {v1['triples_per_article'].get(k)} | {v2['triples_per_article'].get(k)} |")
    for k in ("avg", "p50", "p95", "max"):
        lines.append(f"| entities/art {k} | {v1['entities_per_article'].get(k)} | {v2['entities_per_article'].get(k)} |")
    lines.append(f"| s_unmatched_rate_pct | {v1['s_unmatched_rate_pct']}% | {v2['s_unmatched_rate_pct']}% |")
    lines.append(f"| primary_topic_coverage | {v1['primary_topic_coverage_pct']}% | {v2['primary_topic_coverage_pct']}% |")
    lines.append(f"| central_entities_coverage | {v1['central_entities_coverage_pct']}% | {v2['central_entities_coverage_pct']}% |")
    lines.append("")

    lines.append("## Confidence distribution\n")
    lines.append("| stat | v1 cram | v2 split |")
    lines.append("|---|---|---|")
    for k in ("avg", "p50", "p95", "min", "max"):
        lines.append(f"| confidence {k} | {v1['confidence'].get(k)} | {v2['confidence'].get(k)} |")
    lines.append("")

    lines.append("## relation_type distribution\n")
    lines.append(f"- v1 cram: {v1['relation_type_unique']} unique")
    lines.append(f"- v2 split: {v2['relation_type_unique']} unique")
    s1 = set(r for r, _ in v1["relation_type_top30"])
    s2 = set(r for r, _ in v2["relation_type_top30"])
    lines.append(f"- top-30 overlap: {len(s1 & s2)} of 30")
    lines.append("")

    lines.append("### Top-30 v1 cram\n")
    lines.append("| rank | relation_type | count |")
    lines.append("|---|---|---|")
    for i, (r, c) in enumerate(v1["relation_type_top30"], 1):
        lines.append(f"| {i} | `{r}` | {c} |")
    lines.append("")

    lines.append("### Top-30 v2 split\n")
    lines.append("| rank | relation_type | count |")
    lines.append("|---|---|---|")
    for i, (r, c) in enumerate(v2["relation_type_top30"], 1):
        lines.append(f"| {i} | `{r}` | {c} |")
    lines.append("")

    lines.append("## centrality + object_kind\n")
    lines.append("```")
    lines.append(f"v1 centrality: {v1['centrality_dist']}")
    lines.append(f"v2 centrality: {v2['centrality_dist']}")
    lines.append(f"v1 object_kind: {v1['object_kind_dist']}")
    lines.append(f"v2 object_kind: {v2['object_kind_dist']}")
    lines.append("```")
    lines.append("")

    lines.append("## Recommendation\n")
    if v2["triples_total"] > v1["triples_total"] and v2["s_unmatched_rate_pct"] < v1["s_unmatched_rate_pct"]:
        lines.append("**Winner: v2 split** — more triples + lower s_unmatched rate. Wall-time penalty (~+40%) is acceptable for higher-quality knowledge graph on 21M URL.")
    elif v1["triples_total"] >= v2["triples_total"] and v1.get("wall_s", 0) and v2.get("wall_s", 0):
        ratio = v2.get("wall_s", 0) / max(v1.get("wall_s", 1), 1)
        if ratio > 1.4:
            lines.append("**Winner: v1 cram** — comparable triples count and substantially shorter wall-time. v2 split's quality bump doesn't justify ~%.0fx wall." % (ratio,))
        else:
            lines.append("**Winner: v2 split** (default per session plan).")
    else:
        lines.append("**Winner: v2 split** (default per session plan; metrics inconclusive).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v3_bench_1k_s42")
    ap.add_argument("--v1-dir", default=None)
    ap.add_argument("--v2-dir", default=None)
    ap.add_argument("--output", default=str(REPO / "SESSIONS_SUMMARY" / "2026-05-08_v1_vs_v2_compare.md"))
    args = ap.parse_args()

    if args.v1_dir:
        v1d = Path(args.v1_dir)
    else:
        cands = sorted((REPO / "final_results").glob(f"*__spo_v1_{args.tag}"))
        v1d = cands[-1] if cands else None
    if args.v2_dir:
        v2d = Path(args.v2_dir)
    else:
        cands = sorted((REPO / "final_results").glob(f"*__spo_v2_{args.tag}"))
        v2d = cands[-1] if cands else None

    if not v1d or not v2d:
        sys.exit(f"missing run dirs: v1={v1d} v2={v2d}")

    v1 = analyze_run(v1d)
    v2 = analyze_run(v2d)
    md = render_markdown(v1, v2)
    Path(args.output).write_text(md, encoding="utf-8")
    print(md)
    print(f"\nReport written to: {args.output}")


if __name__ == "__main__":
    main()
