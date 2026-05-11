#!/usr/bin/env python3
"""Embed articles using Qwen3-Embedding via vLLM.

Per-article doc text:
    {h1}
    {article_summary}
    {entities}                  <- union(strong, central), deduped, comma-separated

Reads source from a v1 final.jsonl (default) — skips junk and rows without h1/summary.
Calls OpenAI-compatible /v1/embeddings (vLLM) in concurrent batches.
Writes:
    <out_dir>/embeddings.npy        — float32 [N, D]
    <out_dir>/manifest.jsonl        — per row: {url_hash, url, domain, idx, doc_text_len, n_ent_strong, n_ent_central}
    <out_dir>/meta.json             — {model, dim, n_rows, source, ts}
Idempotent: --resume re-reads manifest + npy and only embeds missing rows.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import requests


def build_doc_text(rec: dict) -> tuple[str, int, int]:
    """Compose h1 \\n summary \\n entities (union of strong + central, deduped).
    Returns (text, n_ent_used, n_central)."""
    h1 = (rec.get("h1") or "").strip()
    summary = (rec.get("article_summary") or "").strip()
    ents = rec.get("entities") or []
    selected = []
    n_central = 0
    seen = set()
    for e in ents:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        if e.get("is_central"):
            n_central += 1
        if e.get("strength") != "strong" and not e.get("is_central"):
            continue  # skip weak non-central (numbers, dates, percentages — noise for clustering)
        k = name.lower()
        if k in seen:
            continue
        seen.add(k)
        selected.append(name)

    parts = []
    if h1:
        parts.append(h1)
    if summary:
        parts.append(summary)
    if selected:
        parts.append(", ".join(selected))
    return "\n".join(parts), len(selected), n_central


def call_embed(session: requests.Session, url: str, model: str, batch: list[str], timeout: int = 120) -> list[list[float]]:
    payload = {"model": model, "input": batch}
    r = session.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()["data"]
    return [d["embedding"] for d in data]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="final_results/2026-05-09_00-21-48__spo_v1_mns32_full/final.jsonl")
    ap.add_argument("--out", required=True, help="output dir")
    ap.add_argument("--endpoint", default="http://127.0.0.1:8002/v1/embeddings")
    ap.add_argument("--model", default="Qwen/Qwen3-Embedding-4B")
    ap.add_argument("--batch-size", type=int, default=32, help="docs per HTTP call")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel batches in flight")
    ap.add_argument("--limit", type=int, default=0, help="0 = all eligible rows")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="build docs, count, but do not call embedding API")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    src = (repo / args.source).resolve()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[load] {src}")
    rows = []  # list of (url_hash, url, domain, doc_text, n_strong, n_central)
    skipped_junk = skipped_empty = 0
    with src.open() as f:
        for line in f:
            d = json.loads(line)
            if d.get("is_junk"):
                skipped_junk += 1; continue
            text, ns, nc = build_doc_text(d)
            if not text.strip():
                skipped_empty += 1; continue
            rows.append((d["url_hash"], d.get("url"), d.get("domain"), text, ns, nc))
    print(f"[load] eligible={len(rows)}  skipped_junk={skipped_junk}  skipped_empty={skipped_empty}")
    if args.limit:
        rows = rows[: args.limit]
        print(f"[load] limited to {len(rows)}")

    # Resume
    manifest_path = out_dir / "manifest.jsonl"
    npy_path = out_dir / "embeddings.npy"
    meta_path = out_dir / "meta.json"
    done_hashes: set[str] = set()
    if args.resume and manifest_path.exists() and npy_path.exists():
        with manifest_path.open() as f:
            for line in f:
                done_hashes.add(json.loads(line)["url_hash"])
        existing = np.load(npy_path)
        print(f"[resume] {len(done_hashes)} rows already embedded, shape={existing.shape}")
    else:
        existing = None

    todo = [r for r in rows if r[0] not in done_hashes]
    print(f"[todo] {len(todo)} rows to embed (batch_size={args.batch_size}, concurrency={args.concurrency})")
    if args.dry_run:
        sample_lens = [len(r[3]) for r in todo[:1000]]
        if sample_lens:
            print(f"[dry-run] doc_text char length (first 1000): mean={np.mean(sample_lens):.0f} p50={np.percentile(sample_lens,50):.0f} p95={np.percentile(sample_lens,95):.0f} max={max(sample_lens)}")
        print(f"[dry-run] example doc_text:\n----\n{todo[0][3][:800] if todo else '<none>'}\n----")
        return

    # Build batches
    batches = [todo[i : i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    print(f"[plan] {len(batches)} batches")

    session = requests.Session()
    # Health check
    health = args.endpoint.replace("/v1/embeddings", "/v1/models")
    try:
        models = session.get(health, timeout=10).json()
        print(f"[health] {health} OK — models: {[m.get('id') for m in models.get('data', [])]}")
    except Exception as e:
        print(f"[health] FAILED at {health}: {e}", file=sys.stderr)
        print(f"        Start vLLM with Qwen3-Embedding first (see comment at top of script).", file=sys.stderr)
        sys.exit(2)

    # Run
    new_vecs: list[np.ndarray] = []
    new_manifest: list[dict] = []
    t0 = time.time()
    ok = fail = 0

    def work(idx, batch):
        texts = [b[3] for b in batch]
        embs = call_embed(session, args.endpoint, args.model, texts)
        return idx, batch, embs

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(work, i, b): i for i, b in enumerate(batches)}
        results: dict[int, tuple[list, list]] = {}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                idx, batch, embs = fut.result()
                results[idx] = (batch, embs)
                ok += len(batch)
            except Exception as e:
                fail += 1
                print(f"[err] batch {i} failed: {e}", file=sys.stderr)
            if (ok + fail * args.batch_size) % (args.batch_size * 10) == 0:
                dt = time.time() - t0
                print(f"[prog] ok={ok} fail={fail} dt={dt:.1f}s rate={ok/max(0.001,dt):.1f} docs/s")

    # Reassemble in original order
    for i in range(len(batches)):
        if i not in results:
            continue
        batch, embs = results[i]
        for (uh, url, dom, text, ns, nc), emb in zip(batch, embs):
            new_vecs.append(np.asarray(emb, dtype=np.float32))
            new_manifest.append({
                "url_hash": uh, "url": url, "domain": dom,
                "doc_text_len": len(text), "n_ent_strong": ns, "n_ent_central": nc,
            })

    if not new_vecs:
        print("[done] nothing to write")
        return

    new_arr = np.vstack(new_vecs)
    if existing is not None and existing.size:
        all_arr = np.vstack([existing, new_arr])
    else:
        all_arr = new_arr

    np.save(npy_path, all_arr)
    with manifest_path.open("a") as f:
        for m in new_manifest:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    dt = time.time() - t0
    meta = {
        "model": args.model,
        "dim": int(all_arr.shape[1]),
        "n_rows": int(all_arr.shape[0]),
        "source": str(src),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "wall_s_this_run": round(dt, 2),
        "rate_docs_per_s": round(ok / max(0.001, dt), 2),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[done] wrote {npy_path}  shape={all_arr.shape}  rate={meta['rate_docs_per_s']} docs/s  wall={dt:.1f}s")
    print(f"[done] meta: {meta_path}")


if __name__ == "__main__":
    main()
