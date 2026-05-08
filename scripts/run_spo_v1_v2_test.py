#!/usr/bin/env python3
"""SPO v3 full A/B parallel test orchestrator.

Runs the v1 cram pipeline AND the v2 split pipeline simultaneously on the FULL article
sample (default: --limit 0 = all articles), each with --concurrency 4 (sum = 8 vLLM
inflight, the Spark sweet spot).

Stages:
  1. Clear websites_cache/ (cold-cache baseline).
  2. Pre-warm cache by streaming all articles through trafilatura
     (multi-threaded). Times this whole pass in isolation — the CPU cost is
     pipeline-agnostic, so we measure it separately for ETA extrapolation onto
     RTX 6000 Pro (GPU ~3-5× faster, CPU cache gen the same).
  3. Launch run_spo_v1.py and run_spo_v2.py as subprocesses, both reading the now-warm
     cache. Each writes its own per-run dir under final_results/.
  4. Wait for both, then write a comparison report into the master dir.

Usage:
    python3 scripts/run_spo_v1_v2_test.py                           # full sample default
    python3 scripts/run_spo_v1_v2_test.py --limit 1000              # bench-sized run
    python3 scripts/run_spo_v1_v2_test.py --concurrency-each 4      # default
    python3 scripts/run_spo_v1_v2_test.py --no-clear-cache          # reuse warm cache
    python3 scripts/run_spo_v1_v2_test.py --no-warmup               # skip warmup stage
                                                                      (rely on per-run
                                                                       streaming loader
                                                                       to populate cache
                                                                       on the fly)

The master dir layout:
    final_results/<ts>__spo_v1_v2_test_<tag>/
      cache_warmup_meta.json   # n_articles, elapsed_s, throughput, n_loader_workers
      run_log.txt              # stage transitions + subprocess stdout/stderr
      v1_dir.txt               # path to v1 per-run dir
      v2_dir.txt               # path to v2 per-run dir
      comparison_report.md     # post-run v1 vs v2 metrics

Each per-run dir is the standard `final_results/<ts>__spo_v{1,2}_<tag>/` layout produced
by run_spo_v{1,2}.py (final.jsonl, classified.jsonl, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.config import FINAL_RESULT_DIR, WEBSITES_DIR  # noqa: E402
from lib.streaming_loader import stream_articles_async  # noqa: E402


def _clear_cache(cache_dir: Path) -> int:
    """Delete every *.json in the cache dir. Returns number of files removed."""
    if not cache_dir.exists():
        return 0
    n = 0
    for p in cache_dir.glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _warm_cache(
    websites_dir: Path,
    cache_dir: Path,
    limit: int,
    n_loader_workers: int,
    log_fh,
) -> dict:
    """Pull every article through stream_articles_async — this forces trafilatura
    HTML→markdown extraction and writes the JSON envelope to `cache_dir`. We just count
    + discard the yielded dicts; the side effect of cache write is what matters.

    Returns a meta dict suitable to drop as JSON.
    """
    print(f"[warmup] starting cache warmup: limit={limit} workers={n_loader_workers}",
          file=log_fh, flush=True)
    t0 = time.perf_counter()
    n_yielded = 0
    iterator = stream_articles_async(
        str(websites_dir),
        limit=limit,
        random_sample=False,  # pre-warm everything in dir order; per-run scripts can still
                              # do their own random sampling on the warmed cache
        seed=42,
        n_loader_workers=n_loader_workers,
        queue_maxsize=200,
        cache_dir=str(cache_dir),
    )
    for art in iterator:
        n_yielded += 1
        if n_yielded % 1000 == 0:
            elapsed = time.perf_counter() - t0
            print(f"[warmup] yielded={n_yielded} elapsed={elapsed:.1f}s "
                  f"rate={n_yielded/elapsed:.1f}/s", file=log_fh, flush=True)
    elapsed = time.perf_counter() - t0
    # Pull stats from the iterator object (cache_hits, cache_misses, parse_errors).
    try:
        loader_stats = iterator.stats.as_dict()
    except Exception:
        loader_stats = {}
    print(f"[warmup] DONE n_articles={n_yielded} elapsed={elapsed:.1f}s "
          f"throughput={n_yielded/max(elapsed,1):.1f}/s loader_stats={loader_stats}",
          file=log_fh, flush=True)
    return {
        "n_articles": n_yielded,
        "elapsed_s": round(elapsed, 2),
        "throughput_per_s": round(n_yielded / max(elapsed, 1), 2),
        "n_loader_workers": n_loader_workers,
        "websites_dir": str(websites_dir),
        "cache_dir": str(cache_dir),
        "loader_stats": loader_stats,
        "started_at": datetime.fromtimestamp(time.time() - elapsed).isoformat(timespec="seconds"),
        "ended_at": datetime.now().isoformat(timespec="seconds"),
    }


def _spawn_run(pipeline: str, args, master_dir: Path, env_extra: dict | None = None) -> subprocess.Popen:
    """Launch run_spo_v{1,2}.py as a subprocess. Stdout+stderr → master_dir/<pipeline>_subproc.log.

    Each subprocess decides its own per-run output dir via FINAL_RESULT_DIR + tag, which
    is computed at script startup. We pass the tag we want and capture the resulting
    dir from a sentinel after subprocess completes (using its standard --tag layout).
    """
    log_path = master_dir / f"{pipeline}_subproc.log"
    log_fh = open(log_path, "w", encoding="utf-8", buffering=1)
    cmd = [
        sys.executable, "-u",
        str(REPO / f"scripts/run_spo_{pipeline}.py"),
        "--limit", str(args.limit),
        "--concurrency", str(args.concurrency_each),
        "--tag", args.tag,
        "--no-summary",
    ]
    if args.random:
        cmd += ["--random", "--seed", str(args.seed)]
    if args.cache_dir:
        cmd += ["--cache-dir", str(args.cache_dir)]
    print(f"[spawn-{pipeline}] cmd={' '.join(cmd)}", flush=True)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        cmd, stdout=log_fh, stderr=subprocess.STDOUT, env=env, cwd=str(REPO),
    )


def _find_run_dir(pipeline: str, tag: str, ts_prefix: str) -> Path | None:
    """Locate the per-run dir that the subprocess just created. We look for any dir
    matching `<ts>__spo_<pipeline>_<tag>` whose timestamp is >= ts_prefix (the
    orchestrator's start time)."""
    pattern = f"*__spo_{pipeline}_{tag}"
    candidates = sorted(FINAL_RESULT_DIR.glob(pattern))
    # Filter to those at-or-after our master start
    matches = [c for c in candidates if c.name >= ts_prefix]
    return matches[-1] if matches else None


def _summarize_run(run_dir: Path) -> dict:
    """Read run_meta.json + final.jsonl tally for a per-run dir. Returns a dict with
    headline metrics for the comparison report."""
    meta_path = run_dir / "run_meta.json"
    meta = json.load(open(meta_path)) if meta_path.exists() else {}
    counters = meta.get("counters", {})
    return {
        "run_dir": str(run_dir),
        "pipeline": meta.get("pipeline", run_dir.name),
        "wall_s": meta.get("wall_s"),
        "n_articles_seen": meta.get("n_articles_seen"),
        "n_pre_filter_junk": meta.get("n_pre_filter_junk"),
        "counters": counters,
        "loader_stats": meta.get("loader_stats", {}),
    }


def _write_comparison_report(master_dir: Path, cache_meta: dict, v1_meta: dict, v2_meta: dict) -> Path:
    out = master_dir / "comparison_report.md"
    lines: list[str] = []
    lines.append(f"# SPO v3 full parallel A/B — comparison report\n")
    lines.append(f"Master dir: `{master_dir}`")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")

    lines.append("## Cache pre-warm (CPU-bound, trafilatura)\n")
    lines.append("```")
    lines.append(json.dumps(cache_meta, indent=2, ensure_ascii=False))
    lines.append("```\n")

    lines.append("## v1 cram (single-call entities + rich SPO)\n")
    lines.append("```")
    lines.append(json.dumps(v1_meta, indent=2, ensure_ascii=False))
    lines.append("```\n")

    lines.append("## v2 split (entities_only + spo_pipe)\n")
    lines.append("```")
    lines.append(json.dumps(v2_meta, indent=2, ensure_ascii=False))
    lines.append("```\n")

    # Quick deltas
    lines.append("## Quick deltas\n")
    lines.append("| metric | v1 cram | v2 split | delta |")
    lines.append("|---|---|---|---|")
    v1w = v1_meta.get("wall_s") or 0
    v2w = v2_meta.get("wall_s") or 0
    lines.append(f"| wall_s | {v1w} | {v2w} | {round((v2w-v1w)/max(v1w,1)*100,1) if v1w else '—'}% |")
    for k in ("classify_ok", "junk", "entities_ok", "entities_fail",
              "spo_ok", "spo_fail", "meta_ok", "sponsored_ok", "sponsored_true",
              "final_ok", "final_fail",
              "triples_total", "entities_total", "central_total",
              "s_unmatched_total"):
        a = v1_meta.get("counters", {}).get(k)
        b = v2_meta.get("counters", {}).get(k)
        lines.append(f"| {k} | {a} | {b} | — |")
    lines.append("")
    lines.append("## Notes\n")
    lines.append("- Cache warmup time is CPU-bound (trafilatura HTML→markdown) — independent\n"
                 "  of GPU. For RTX 6000 Pro ETA extrapolation, this stage stays ~constant.")
    lines.append("- v1+v2 ran in parallel on shared vLLM (concurrency 4 each = 8 total inflight).\n"
                 "  Per-pipeline wall_s is somewhat noisy; for clean wall_s use a sequential rerun.")
    lines.append("- Predicate harvest for v4 enum: load triples from final.jsonl in both run dirs and\n"
                 "  aggregate `relation_type` + `predicate_phrase` distribution.")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="0 = all articles in websites/")
    ap.add_argument("--concurrency-each", type=int, default=4,
                    help="vLLM concurrency per pipeline (sum = 2 * this on the shared vLLM).")
    ap.add_argument("--tag", default="v3_test_full",
                    help="Tag suffix for the per-run dirs. Default 'v3_test_full'.")
    ap.add_argument("--master-tag", default=None,
                    help="Tag suffix for the master dir; defaults to --tag.")
    ap.add_argument("--random", action="store_true",
                    help="Pass --random to per-run scripts (random sample by seed).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--websites", default=str(WEBSITES_DIR))
    ap.add_argument("--cache-dir", default=None,
                    help="Override websites_cache/ location.")
    ap.add_argument("--loader-workers", type=int, default=8,
                    help="ThreadPool size for cache warmup. Default 8 (CPU-bound, trafilatura).")
    ap.add_argument("--no-clear-cache", action="store_true",
                    help="Skip cache clear (reuse existing warmed cache).")
    ap.add_argument("--no-warmup", action="store_true",
                    help="Skip cache warmup stage entirely (per-run scripts will populate cache lazily).")
    args = ap.parse_args()

    websites_dir = Path(args.websites)
    cache_dir = Path(args.cache_dir) if args.cache_dir else (REPO / "websites_cache")

    # Master dir
    ts_prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    master_tag = args.master_tag or args.tag
    master_dir = FINAL_RESULT_DIR / f"{ts_prefix}__spo_v1_v2_test_{master_tag}"
    master_dir.mkdir(parents=True, exist_ok=True)
    log_path = master_dir / "run_log.txt"
    log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
    print(f"[master] start ts={ts_prefix} master_dir={master_dir}", file=log_fh, flush=True)
    print(f"[master] args={vars(args)}", file=log_fh, flush=True)
    print(f"master_dir = {master_dir}")

    # ---------- Stage 1 — clear cache ----------
    if not args.no_clear_cache:
        n_removed = _clear_cache(cache_dir)
        print(f"[stage1] cleared {n_removed} files from {cache_dir}", file=log_fh, flush=True)
    else:
        print(f"[stage1] skipped (--no-clear-cache)", file=log_fh, flush=True)

    # ---------- Stage 2 — warm cache (parallel trafilatura) ----------
    if not args.no_warmup:
        cache_meta = _warm_cache(
            websites_dir=websites_dir,
            cache_dir=cache_dir,
            limit=args.limit,
            n_loader_workers=args.loader_workers,
            log_fh=log_fh,
        )
        with open(master_dir / "cache_warmup_meta.json", "w", encoding="utf-8") as f:
            json.dump(cache_meta, f, indent=2, ensure_ascii=False)
        print(f"[stage2] cache_warmup_meta.json written. n={cache_meta['n_articles']} "
              f"elapsed={cache_meta['elapsed_s']}s", file=log_fh, flush=True)
    else:
        cache_meta = {"skipped": True}
        print(f"[stage2] skipped (--no-warmup)", file=log_fh, flush=True)

    # ---------- Stage 3 — launch v1 + v2 in parallel ----------
    print(f"[stage3] launching v1 + v2 subprocesses", file=log_fh, flush=True)
    t_runs_start = time.perf_counter()
    p1 = _spawn_run("v1", args, master_dir)
    p2 = _spawn_run("v2", args, master_dir)
    print(f"[stage3] v1.pid={p1.pid} v2.pid={p2.pid}", file=log_fh, flush=True)

    # ---------- Stage 4 — wait + summarize ----------
    rc1 = p1.wait()
    t1_done = time.perf_counter() - t_runs_start
    print(f"[stage4] v1 done rc={rc1} elapsed_since_launch={t1_done:.1f}s", file=log_fh, flush=True)
    rc2 = p2.wait()
    t2_done = time.perf_counter() - t_runs_start
    print(f"[stage4] v2 done rc={rc2} elapsed_since_launch={t2_done:.1f}s", file=log_fh, flush=True)

    v1_dir = _find_run_dir("v1", args.tag, ts_prefix)
    v2_dir = _find_run_dir("v2", args.tag, ts_prefix)
    print(f"[stage4] v1_dir={v1_dir}", file=log_fh, flush=True)
    print(f"[stage4] v2_dir={v2_dir}", file=log_fh, flush=True)
    if v1_dir:
        (master_dir / "v1_dir.txt").write_text(str(v1_dir), encoding="utf-8")
    if v2_dir:
        (master_dir / "v2_dir.txt").write_text(str(v2_dir), encoding="utf-8")

    v1_meta = _summarize_run(v1_dir) if v1_dir else {"error": "v1_dir not found"}
    v2_meta = _summarize_run(v2_dir) if v2_dir else {"error": "v2_dir not found"}

    report_path = _write_comparison_report(master_dir, cache_meta, v1_meta, v2_meta)
    print(f"[stage4] comparison report written: {report_path}", file=log_fh, flush=True)
    print(f"[master] DONE rc1={rc1} rc2={rc2}", file=log_fh, flush=True)

    print(f"\n=== DONE ===")
    print(f"master_dir: {master_dir}")
    print(f"v1_dir: {v1_dir}")
    print(f"v2_dir: {v2_dir}")
    print(f"comparison_report: {report_path}")


if __name__ == "__main__":
    main()
