#!/usr/bin/env python3
"""Compare SPO v1 (cram) vs v2 (split) outputs on intersection of url_hash."""
from __future__ import annotations
import argparse, json, re, statistics, sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> dict[str, dict]:
    out = {}
    with path.open() as f:
        for line in f:
            d = json.loads(line)
            out[d["url_hash"]] = d
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def ent_keys(ents: list[dict]) -> set[str]:
    return {norm(e.get("name", "")) for e in ents if e.get("name")}


def triple_keys(triples: list[dict]) -> set[tuple[str, str, str]]:
    return {
        (norm(t.get("subject", "")), norm(t.get("relation_type") or t.get("predicate_phrase") or ""), norm(t.get("object", "")))
        for t in triples
    }


def percentiles(xs: list[float], qs=(50, 90, 95)) -> dict:
    if not xs:
        return {f"p{q}": None for q in qs}
    xs = sorted(xs)
    out = {}
    for q in qs:
        idx = max(0, min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1)))))
        out[f"p{q}"] = xs[idx]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", default="final_results/2026-05-09_00-21-48__spo_v1_mns32_full/final.jsonl")
    ap.add_argument("--v2", default="final_results/2026-05-10_10-35-36__spo_v2_mns32_full/final.jsonl")
    ap.add_argument("--out", required=True, help="output dir")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    v1p = repo / args.v1
    v2p = repo / args.v2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading v1: {v1p}")
    v1 = load_jsonl(v1p)
    print(f"loading v2: {v2p}")
    v2 = load_jsonl(v2p)
    common = sorted(set(v1) & set(v2))
    print(f"v1={len(v1)}  v2={len(v2)}  common={len(common)}")

    # Aggregates
    agree_junk = 0
    junk_both = junk_v1_only = junk_v2_only = neither_junk = 0

    # On non-junk subset where both ok
    nb_ok = []  # list of dicts per url with comparison
    cat_match = lang_match = 0
    spons_bool_match = spons_subtype_match = 0
    n_spons_v1 = n_spons_v2 = 0

    ent_jacc, ent_n_v1, ent_n_v2 = [], [], []
    trip_jacc, trip_n_v1, trip_n_v2 = [], [], []
    central_v1, central_v2 = [], []

    title_present_v1 = title_present_v2 = 0
    title_len_v1, title_len_v2 = [], []
    desc_len_v1, desc_len_v2 = [], []

    joined_path = out_dir / "joined.jsonl"
    with joined_path.open("w") as jf:
        for h in common:
            a = v1[h]
            b = v2[h]
            row = {"url_hash": h, "url": a.get("url"), "domain": a.get("domain"),
                   "v1_is_junk": a.get("is_junk"), "v2_is_junk": b.get("is_junk")}
            if a.get("is_junk") == b.get("is_junk"):
                agree_junk += 1
            if a.get("is_junk") and b.get("is_junk"):
                junk_both += 1
            elif a.get("is_junk") and not b.get("is_junk"):
                junk_v1_only += 1
            elif not a.get("is_junk") and b.get("is_junk"):
                junk_v2_only += 1
            else:
                neither_junk += 1
                # Real content comparison
                if a.get("category", "") == b.get("category", "") and a.get("category"):
                    cat_match += 1
                if a.get("language", "") == b.get("language", "") and a.get("language"):
                    lang_match += 1

                if a.get("sponsored") == b.get("sponsored"):
                    spons_bool_match += 1
                if (a.get("sponsored_subtype") or "") == (b.get("sponsored_subtype") or ""):
                    spons_subtype_match += 1
                if a.get("sponsored"): n_spons_v1 += 1
                if b.get("sponsored"): n_spons_v2 += 1

                ea, eb = ent_keys(a.get("entities") or []), ent_keys(b.get("entities") or [])
                ej = jaccard(ea, eb)
                ent_jacc.append(ej)
                ent_n_v1.append(len(ea))
                ent_n_v2.append(len(eb))
                central_v1.append(a.get("n_central") or 0)
                central_v2.append(b.get("n_central") or 0)

                ta, tb = triple_keys(a.get("triples") or []), triple_keys(b.get("triples") or [])
                tj = jaccard(ta, tb)
                trip_jacc.append(tj)
                trip_n_v1.append(len(ta))
                trip_n_v2.append(len(tb))

                if a.get("title"): title_present_v1 += 1
                if b.get("title"): title_present_v2 += 1
                title_len_v1.append(len(a.get("title") or ""))
                title_len_v2.append(len(b.get("title") or ""))
                desc_len_v1.append(len(a.get("meta_description") or ""))
                desc_len_v2.append(len(b.get("meta_description") or ""))

                row.update({
                    "v1_cat": a.get("category"), "v2_cat": b.get("category"),
                    "v1_lang": a.get("language"), "v2_lang": b.get("language"),
                    "v1_sponsored": a.get("sponsored"), "v2_sponsored": b.get("sponsored"),
                    "v1_n_ent": len(ea), "v2_n_ent": len(eb), "ent_jaccard": round(ej, 4),
                    "v1_n_trip": len(ta), "v2_n_trip": len(tb), "trip_jaccard": round(tj, 4),
                    "v1_n_central": a.get("n_central"), "v2_n_central": b.get("n_central"),
                    "v1_title": a.get("title"), "v2_title": b.get("title"),
                })
            jf.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(common)
    nb_n = neither_junk

    def fmt(x, d=3):
        return "n/a" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))

    def stats_line(xs):
        if not xs:
            return "n=0"
        p = percentiles(xs)
        mean = statistics.mean(xs)
        return f"n={len(xs)} mean={mean:.2f} p50={p['p50']:.2f} p90={p['p90']:.2f} p95={p['p95']:.2f}"

    report = []
    rp = report.append
    rp("# Comparison: SPO v1 (cram) vs SPO v2 (split) — same URL intersection\n")
    rp(f"- v1 source: `{v1p.relative_to(repo)}` ({len(v1)} rows)")
    rp(f"- v2 source: `{v2p.relative_to(repo)}` ({len(v2)} rows)")
    rp(f"- **Intersection: {n} URL** (random sample seed=42, same cache)")
    rp("")
    rp("## Junk classification (whole intersection)\n")
    rp(f"- Agreement on is_junk: **{agree_junk}/{n} = {100*agree_junk/n:.2f}%**")
    rp(f"- Both junk: {junk_both}")
    rp(f"- v1-only junk: {junk_v1_only}")
    rp(f"- v2-only junk: {junk_v2_only}")
    rp(f"- Neither junk (real content compared below): **{neither_junk}**")
    rp("")
    if nb_n:
        rp("## Real-content subset (both non-junk)\n")
        rp(f"### Meta\n")
        rp(f"- Language match: {lang_match}/{nb_n} = {100*lang_match/nb_n:.2f}%")
        rp(f"- Category match (exact string): {cat_match}/{nb_n} = {100*cat_match/nb_n:.2f}%")
        rp(f"- v1 has title: {title_present_v1}/{nb_n} ({100*title_present_v1/nb_n:.1f}%); v2: {title_present_v2}/{nb_n} ({100*title_present_v2/nb_n:.1f}%)")
        rp(f"- title length: v1 {stats_line(title_len_v1)}; v2 {stats_line(title_len_v2)}")
        rp(f"- meta_description length: v1 {stats_line(desc_len_v1)}; v2 {stats_line(desc_len_v2)}")
        rp("")
        rp("### Sponsored\n")
        rp(f"- sponsored bool agreement: {spons_bool_match}/{nb_n} = {100*spons_bool_match/nb_n:.2f}%")
        rp(f"- subtype agreement: {spons_subtype_match}/{nb_n} = {100*spons_subtype_match/nb_n:.2f}%")
        rp(f"- v1 sponsored=True: {n_spons_v1} ({100*n_spons_v1/nb_n:.2f}%); v2: {n_spons_v2} ({100*n_spons_v2/nb_n:.2f}%)")
        rp("")
        rp("### Entities\n")
        rp(f"- count per article: v1 {stats_line(ent_n_v1)}; v2 {stats_line(ent_n_v2)}")
        rp(f"- n_central per article: v1 {stats_line(central_v1)}; v2 {stats_line(central_v2)}")
        rp(f"- Jaccard (name-normalized): {stats_line(ent_jacc)}")
        rp(f"  - exact match (J=1.0): {sum(1 for x in ent_jacc if x == 1.0)}/{len(ent_jacc)} = {100*sum(1 for x in ent_jacc if x == 1.0)/len(ent_jacc):.2f}%")
        rp(f"  - zero overlap (J=0.0): {sum(1 for x in ent_jacc if x == 0.0)}/{len(ent_jacc)} = {100*sum(1 for x in ent_jacc if x == 0.0)/len(ent_jacc):.2f}%")
        rp("")
        rp("### Triples (SPO)\n")
        rp(f"- count per article: v1 {stats_line(trip_n_v1)}; v2 {stats_line(trip_n_v2)}")
        rp(f"- Jaccard (subj+rel+obj normalized): {stats_line(trip_jacc)}")
        rp(f"  - exact match: {sum(1 for x in trip_jacc if x == 1.0)}/{len(trip_jacc)} = {100*sum(1 for x in trip_jacc if x == 1.0)/len(trip_jacc):.2f}%")
        rp(f"  - zero overlap: {sum(1 for x in trip_jacc if x == 0.0)}/{len(trip_jacc)} = {100*sum(1 for x in trip_jacc if x == 0.0)/len(trip_jacc):.2f}%")
    rp("")
    rp("---")
    rp("Joined per-row data: `joined.jsonl` in this directory.")

    (out_dir / "report.md").write_text("\n".join(report))
    print(f"\n=== report ===\n")
    print("\n".join(report))
    print(f"\nwrote: {out_dir/'report.md'}")
    print(f"wrote: {joined_path}")


if __name__ == "__main__":
    main()
