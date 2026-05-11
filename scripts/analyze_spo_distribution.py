#!/usr/bin/env python3
"""Analiza dystrybucji SPO triples z bieżącego runa.

Cel: ocenić czy SPO triples mają sens, znaleźć wzorce, zaproponować decyzje
architektoniczne (cap, drop, group).

Output: OBSERVATIONS/<TS>__spo_distribution_analysis.md
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path
from datetime import datetime
import statistics

import numpy as np
from rapidfuzz import process, fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import HDBSCAN

REPO = Path("/home/spark001/Spark-testy/mateusz-g-two-step-vllm")
RUN_DIR = REPO / "final_results/2026-05-09_00-21-48__spo_v1_mns32_full"
ENTITIES_SPO = RUN_DIR / "entities_spo.jsonl"

TS = datetime.now().strftime("%Y-%m-%d_%H-%M")
OUT_FILE = REPO / "OBSERVATIONS" / f"{TS}__spo_distribution_analysis.md"


def histogram_ascii(values, bins=20, width=50):
    """Tekstowy histogram do markdown."""
    if not values:
        return "(empty)"
    if all(v == values[0] for v in values):
        return f"all values = {values[0]:.3f} (n={len(values)})"
    lo, hi = min(values), max(values)
    edges = np.linspace(lo, hi, bins + 1)
    counts, _ = np.histogram(values, bins=edges)
    cmax = counts.max()
    out = []
    for i, c in enumerate(counts):
        bar = "█" * int(width * c / cmax) if cmax > 0 else ""
        out.append(f"  {edges[i]:7.3f} – {edges[i+1]:7.3f} | {bar} {c}")
    return "\n".join(out)


def main():
    print(f"Loading {ENTITIES_SPO} …", file=sys.stderr)
    t0 = time.time()
    records = []
    triples_all = []  # all triples flat, with article_idx + position
    n_records_with_triples = 0

    with open(ENTITIES_SPO) as fh:
        for idx, line in enumerate(fh):
            d = json.loads(line)
            if not d.get("ok"):
                continue
            records.append(
                {
                    "url_hash": d["url_hash"],
                    "n_entities": len(d.get("entities", [])),
                    "n_central": len(d.get("central_entities", [])),
                    "n_triples": len(d.get("triples", [])),
                    "primary_topic": d.get("primary_topic", "") or "",
                    "triples_s_unmatched": d.get("triples_s_unmatched", 0),
                    "triples_o_unmatched": d.get("triples_o_unmatched", 0),
                }
            )
            for pos, t in enumerate(d.get("triples", [])):
                triples_all.append(
                    {
                        "article_idx": idx,
                        "position": pos,  # 0-based
                        "subject": t.get("subject", ""),
                        "subject_type": t.get("subject_type", ""),
                        "relation_type": t.get("relation_type", ""),
                        "predicate_phrase": t.get("predicate_phrase", "") or "",
                        "object": t.get("object", ""),
                        "object_type": t.get("object_type", ""),
                        "object_kind": t.get("object_kind", ""),
                        "evidence_span": t.get("evidence_span", "") or "",
                        "confidence": float(t.get("confidence", 0.0) or 0.0),
                    }
                )
            if d.get("triples"):
                n_records_with_triples += 1
    print(f"  loaded {len(records)} records, {len(triples_all)} triples in {time.time()-t0:.1f}s", file=sys.stderr)

    # ---- Aggregate stats ----
    n_records = len(records)
    n_triples = len(triples_all)

    n_triples_per = [r["n_triples"] for r in records]
    n_central_per = [r["n_central"] for r in records]
    n_entities_per = [r["n_entities"] for r in records]
    confidences = [t["confidence"] for t in triples_all]
    evidence_lens = [len(t["evidence_span"]) for t in triples_all]
    s_unm_per = [r["triples_s_unmatched"] for r in records]
    o_unm_per = [r["triples_o_unmatched"] for r in records]

    relation_types = Counter(t["relation_type"] for t in triples_all)
    subject_types = Counter(t["subject_type"] for t in triples_all)
    object_types = Counter(t["object_type"] for t in triples_all)
    object_kinds = Counter(t["object_kind"] for t in triples_all)

    # Confidence vs position (key analysis)
    pos_buckets = {1: [], 2: [], 3: [], 4: [], 5: [], 6: [], 7: [], 8: [], 9: [], 10: [], 11: [], 12: [], 13: [], 14: [], 15: [], 16: [], 17: [], 18: [], 19: [], 20: []}
    for t in triples_all:
        p = t["position"] + 1  # 1-based
        if p in pos_buckets:
            pos_buckets[p].append(t["confidence"])

    # Confidence by relation_type
    rel_conf = {}
    for t in triples_all:
        r = t["relation_type"]
        rel_conf.setdefault(r, []).append(t["confidence"])

    # ---- RapidFuzz grouping of predicate_phrase ----
    # Take only top-2000 phrases by frequency (would be too slow for full set)
    phrases = Counter(t["predicate_phrase"].lower().strip() for t in triples_all if t["predicate_phrase"])
    top_phrases = [p for p, _ in phrases.most_common(3000)]
    print(f"  fuzzy grouping {len(top_phrases)} unique top phrases…", file=sys.stderr)

    # Greedy clustering: walk through phrases, assign to cluster if fuzz.ratio > 80
    fuzz_clusters = []
    fuzz_assignment = {}
    THRESHOLD = 82  # Fuzz partial ratio threshold
    for ph in top_phrases:
        best_cluster = None
        best_score = 0
        if fuzz_clusters:
            # quick check against cluster centroids (first phrase added)
            choices = [c[0] for c in fuzz_clusters]
            match = process.extractOne(ph, choices, scorer=fuzz.token_sort_ratio)
            if match and match[1] >= THRESHOLD:
                best_cluster = match[2]
                best_score = match[1]
        if best_cluster is None:
            fuzz_clusters.append([ph])
            fuzz_assignment[ph] = len(fuzz_clusters) - 1
        else:
            fuzz_clusters[best_cluster].append(ph)
            fuzz_assignment[ph] = best_cluster

    # Score clusters by total triple count
    cluster_scores = []
    for ci, members in enumerate(fuzz_clusters):
        total_count = sum(phrases[m] for m in members)
        cluster_scores.append((ci, total_count, members))
    cluster_scores.sort(key=lambda x: -x[1])
    print(f"  → {len(fuzz_clusters)} fuzz clusters", file=sys.stderr)

    # ---- HDBSCAN on TF-IDF of predicate phrases (semantic-ish) ----
    # Use top 5000 phrases for speed
    print(f"  HDBSCAN on tfidf vectors…", file=sys.stderr)
    top_5k = [p for p, _ in phrases.most_common(5000)]
    if len(top_5k) >= 50:
        tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=2000)
        try:
            X = tfidf.fit_transform(top_5k).toarray()
        except ValueError:
            X = None
        if X is not None and X.shape[0] >= 10:
            try:
                hdb = HDBSCAN(min_cluster_size=8, metric="euclidean", n_jobs=-1)
                labels = hdb.fit_predict(X)
                hdb_clusters = Counter(labels)
                print(f"  → {len(hdb_clusters)} hdbscan clusters (incl. -1 noise)", file=sys.stderr)
            except Exception as e:
                print(f"  hdbscan failed: {e}", file=sys.stderr)
                labels = None
                hdb_clusters = None
        else:
            labels = None
            hdb_clusters = None
    else:
        labels = None
        hdb_clusters = None

    # ---- Build markdown report ----
    out = []
    out.append(f"# SPO Distribution Analysis")
    out.append(f"")
    out.append(f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out.append(f"**Source:** `{ENTITIES_SPO.relative_to(REPO)}`")
    out.append(f"**Records:** {n_records:,} ok / total triples: {n_triples:,}")
    out.append(f"")

    out.append(f"## 1. Coverage")
    out.append(f"")
    out.append(f"| Metric | Value |")
    out.append(f"|---|---|")
    out.append(f"| ok records | {n_records:,} |")
    out.append(f"| records with triples | {n_records_with_triples:,} ({100*n_records_with_triples/n_records:.1f}%) |")
    out.append(f"| total triples | {n_triples:,} |")
    out.append(f"| mean triples/record | {n_triples/n_records:.2f} |")
    out.append(f"")

    def stats_row(name, x):
        if not x:
            return f"| {name} | – | – | – | – | – |"
        x = sorted(x)
        n = len(x)
        return f"| {name} | {min(x):.2f} | {x[n//2]:.2f} | {x[int(n*0.95)]:.2f} | {x[-1]:.2f} | {statistics.mean(x):.2f} |"

    out.append(f"## 2. Per-record distribution")
    out.append(f"")
    out.append(f"| Metric | min | p50 | p95 | max | mean |")
    out.append(f"|---|---|---|---|---|---|")
    out.append(stats_row("n_entities", n_entities_per))
    out.append(stats_row("n_central",  n_central_per))
    out.append(stats_row("n_triples",  n_triples_per))
    out.append(stats_row("s_unmatched", s_unm_per))
    out.append(stats_row("o_unmatched", o_unm_per))
    out.append(f"")
    out.append(f"### n_triples histogram")
    out.append(f"```")
    out.append(histogram_ascii([float(v) for v in n_triples_per], bins=20))
    out.append(f"```")
    out.append(f"")

    out.append(f"## 3. Confidence distribution")
    out.append(f"")
    out.append(stats_row("confidence (all triples)", confidences))
    out.append(f"")
    out.append(f"### Histogram")
    out.append(f"```")
    out.append(histogram_ascii(confidences, bins=20))
    out.append(f"```")
    out.append(f"")
    # Confidence buckets
    cf_buckets = {"<0.5": 0, "0.5-0.7": 0, "0.7-0.85": 0, "0.85-0.95": 0, "≥0.95": 0}
    for c in confidences:
        if c < 0.5: cf_buckets["<0.5"] += 1
        elif c < 0.7: cf_buckets["0.5-0.7"] += 1
        elif c < 0.85: cf_buckets["0.7-0.85"] += 1
        elif c < 0.95: cf_buckets["0.85-0.95"] += 1
        else: cf_buckets["≥0.95"] += 1
    out.append(f"### Confidence buckets")
    out.append(f"")
    out.append(f"| Range | Count | % |")
    out.append(f"|---|---|---|")
    for k, v in cf_buckets.items():
        out.append(f"| {k} | {v:,} | {100*v/n_triples:.1f}% |")
    out.append(f"")

    out.append(f"## 4. Confidence by triple position (KEY: cap=8 decision)")
    out.append(f"")
    out.append(f"Pytanie: czy trójki na pozycji 9-14 mają niższe confidence niż 1-8?")
    out.append(f"Jeśli tak → cap=8 odetnie szum bez utraty wartościowych trójek.")
    out.append(f"")
    out.append(f"| pos | n | mean conf | median conf | min | max |")
    out.append(f"|---|---|---|---|---|---|")
    for p in sorted(pos_buckets.keys()):
        x = pos_buckets[p]
        if not x: continue
        n = len(x)
        out.append(f"| {p} | {n:,} | {statistics.mean(x):.3f} | {sorted(x)[n//2]:.3f} | {min(x):.3f} | {max(x):.3f} |")
    out.append(f"")

    # Detect drop-off
    means_by_pos = []
    for p in sorted(pos_buckets.keys()):
        x = pos_buckets[p]
        if x:
            means_by_pos.append((p, statistics.mean(x)))
    if len(means_by_pos) >= 8:
        first_8_mean = statistics.mean([m for _, m in means_by_pos[:8]])
        rest_mean = statistics.mean([m for _, m in means_by_pos[8:]]) if len(means_by_pos) > 8 else 0
        delta = first_8_mean - rest_mean
        out.append(f"")
        out.append(f"**Drop-off:** mean conf positions 1-8 = **{first_8_mean:.3f}**, positions 9+ = **{rest_mean:.3f}** (Δ {delta:+.3f})")

    out.append(f"")
    out.append(f"## 5. Most common relation types")
    out.append(f"")
    out.append(f"Top 30 (z ogólnej liczby {len(relation_types)} unikalnych):")
    out.append(f"")
    out.append(f"| relation_type | count | % | mean conf |")
    out.append(f"|---|---|---|---|")
    for r, n in relation_types.most_common(30):
        mean_conf = statistics.mean(rel_conf[r]) if rel_conf.get(r) else 0
        out.append(f"| `{r}` | {n:,} | {100*n/n_triples:.1f}% | {mean_conf:.3f} |")
    out.append(f"")

    out.append(f"## 6. Subject / Object types")
    out.append(f"")
    out.append(f"### Top 20 subject_type")
    out.append(f"")
    out.append(f"| type | count | % |")
    out.append(f"|---|---|---|")
    for t, n in subject_types.most_common(20):
        out.append(f"| `{t}` | {n:,} | {100*n/n_triples:.1f}% |")
    out.append(f"")
    out.append(f"### Top 20 object_type")
    out.append(f"")
    out.append(f"| type | count | % |")
    out.append(f"|---|---|---|")
    for t, n in object_types.most_common(20):
        out.append(f"| `{t}` | {n:,} | {100*n/n_triples:.1f}% |")
    out.append(f"")
    out.append(f"### object_kind")
    out.append(f"")
    out.append(f"| kind | count | % |")
    out.append(f"|---|---|---|")
    for k, n in object_kinds.most_common():
        out.append(f"| `{k}` | {n:,} | {100*n/n_triples:.1f}% |")
    out.append(f"")

    out.append(f"## 7. Predicate phrase analysis — RapidFuzz greedy clustering")
    out.append(f"")
    out.append(f"Klastrowanie {len(top_phrases)} top unique predicate phrases używając")
    out.append(f"`fuzz.token_sort_ratio` z thresholdem {THRESHOLD}.")
    out.append(f"Total clusters: **{len(fuzz_clusters)}**")
    out.append(f"")
    out.append(f"### Top 30 największych klastrów (po sumarycznej liczbie wystąpień)")
    out.append(f"")
    out.append(f"| Cluster | Total occurrences | Members (top 5) |")
    out.append(f"|---|---|---|")
    for ci, total, members in cluster_scores[:30]:
        sample = sorted(members, key=lambda m: -phrases[m])[:5]
        sample_str = " · ".join(f"`{m}` ({phrases[m]})" for m in sample)
        out.append(f"| #{ci} | {total:,} | {sample_str} |")
    out.append(f"")

    out.append(f"## 8. HDBSCAN clustering (TF-IDF char-ngrams)")
    out.append(f"")
    if hdb_clusters is not None:
        n_noise = hdb_clusters.get(-1, 0)
        n_real = sum(c for k, c in hdb_clusters.items() if k != -1)
        out.append(f"Top 5000 phrases vectorized (TF-IDF char_wb 3-5 ngrams), HDBSCAN min_cluster_size=8.")
        out.append(f"")
        out.append(f"- Total clusters: **{len(hdb_clusters) - (1 if -1 in hdb_clusters else 0)}**")
        out.append(f"- Noise (label=-1): **{n_noise:,}** ({100*n_noise/len(top_5k):.1f}%)")
        out.append(f"- Clustered: **{n_real:,}** ({100*n_real/len(top_5k):.1f}%)")
        out.append(f"")
        # Top 20 clusters by phrase count
        # Map label -> phrases
        label_to_phrases = {}
        for ph, lab in zip(top_5k, labels):
            label_to_phrases.setdefault(lab, []).append(ph)
        sorted_labels = sorted(
            [(l, len(ps)) for l, ps in label_to_phrases.items() if l != -1],
            key=lambda x: -x[1],
        )[:20]
        out.append(f"### Top 20 HDBSCAN clusters")
        out.append(f"")
        out.append(f"| Label | Size | Sample phrases |")
        out.append(f"|---|---|---|")
        for lab, size in sorted_labels:
            sample = sorted(label_to_phrases[lab], key=lambda m: -phrases.get(m, 0))[:5]
            sample_str = " · ".join(f"`{m}`" for m in sample)
            out.append(f"| {lab} | {size} | {sample_str} |")
        out.append(f"")
    else:
        out.append(f"(HDBSCAN unavailable for this dataset)")
        out.append(f"")

    out.append(f"## 9. Decision summary")
    out.append(f"")
    out.append(f"### A. Czy SPO ma sens utrzymać?")
    out.append(f"")
    high_conf_pct = sum(1 for c in confidences if c >= 0.85) / max(n_triples, 1) * 100
    out.append(f"- **Confidence:** {high_conf_pct:.1f}% trójek ma confidence ≥0.85 (top quality)")
    out.append(f"- **Mean triples/record:** {n_triples/n_records:.2f} → znacząco bogata reprezentacja")
    out.append(f"- **Unique relation_types:** {len(relation_types)} → schema bogaty, ale czy warto?")
    out.append(f"")
    out.append(f"### B. Czy cap=8 jest uzasadnione?")
    out.append(f"")
    if len(means_by_pos) >= 8 and rest_mean > 0:
        if delta > 0.03:
            out.append(f"**TAK** — confidence pozycji 9+ jest o {delta:.3f} niższy niż 1-8 (drop-off statystyczny).")
        elif delta > 0.01:
            out.append(f"**MARGINALNIE** — drop-off {delta:.3f}, sygnał słaby ale obecny.")
        else:
            out.append(f"**NIE** — confidence stabilny niezależnie od pozycji (Δ {delta:.3f}). Cap=8 ucina dobre trójki.")
    out.append(f"")
    out.append(f"### C. Klastrowanie — co mówi?")
    out.append(f"")
    out.append(f"- RapidFuzz: **{len(fuzz_clusters)}** unikalnych klastrów predicate_phrase z {len(top_phrases)} top phrases")
    out.append(f"- Top klaster zawiera **{cluster_scores[0][1]:,}** wystąpień ({100*cluster_scores[0][1]/n_triples:.1f}% wszystkich triples)")
    if hdb_clusters is not None:
        out.append(f"- HDBSCAN: **{len(hdb_clusters) - (1 if -1 in hdb_clusters else 0)}** klastrów (po TF-IDF char-ngrams)")
    out.append(f"- **Implikacja:** wysokie pokrycie przez kilka top klastrów = predicate_phrase można skompresować do enuma")

    OUT_FILE.write_text("\n".join(out), encoding="utf-8")
    print(f"\nReport written: {OUT_FILE}", file=sys.stderr)
    print(f"Lines: {len(out)}", file=sys.stderr)


if __name__ == "__main__":
    main()
