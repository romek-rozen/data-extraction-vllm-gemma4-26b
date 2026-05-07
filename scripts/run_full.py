"""End-to-end orchestrator: snapshot before → pipeline → snapshot after → analiza.

Tworzy katalog wyjściowy, robi pełen run, generuje raport. Wszystko w jednym
poleceniu — idealne do tmux.

Domyślny output: final_results/<YYYY-MM-DD_HH-MM-SS>/ (pełna ścieżka absolutna).
Można nadpisać --out-dir albo dodać sufiks --tag <name> (final_results/<ts>__<tag>/).

Użycie:
    python3 scripts/run_full.py --limit 0 --concurrency 8                  # auto timestamp
    python3 scripts/run_full.py --limit 0 --concurrency 8 --tag v6_b       # final_results/<ts>__v6_b/
    python3 scripts/run_full.py --limit 50 --concurrency 4 --out-dir foo   # custom
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("full")

ROOT = Path(__file__).parent.parent
DEFAULT_RESULTS_DIR = ROOT / "final_results"


def step(name: str, cmd: list[str], log_file: Path | None = None) -> int:
    logger.info(f"=== {name} ===")
    logger.info("RUN: " + " ".join(cmd))
    if log_file:
        with open(log_file, "w") as f:
            r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        logger.error(f"{name} FAILED (rc={r.returncode})")
    return r.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 = wszystkie URL")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out-dir", default=None,
                        help="Custom katalog wyjściowy (override). Default: final_results/<timestamp>/")
    parser.add_argument("--tag", default=None,
                        help="Sufiks dla auto-timestamp dir, np. --tag v6 → final_results/<ts>__v6/")
    parser.add_argument("--samples", type=int, default=15, help="Liczba sample'i w raporcie")
    parser.add_argument("--no-skip", action="store_true")
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"{ts}__{args.tag}" if args.tag else ts
        out_dir = DEFAULT_RESULTS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {out_dir}")

    metrics_before = out_dir / "metrics_before.txt"
    metrics_after = out_dir / "metrics_after.txt"
    metrics_delta = out_dir / "metrics_delta.txt"
    pipeline_log = out_dir / "pipeline.log"
    entity_layer = out_dir / "entity_layer.jsonl"
    final = out_dir / "final.jsonl"
    summary = out_dir / "summary.md"

    # 1. snapshot before
    rc = step("Snapshot metrics BEFORE",
              ["python3", "scripts/snapshot_metrics.py", "before"],
              log_file=metrics_before)
    if rc != 0:
        logger.warning("snapshot before failed — continuing")

    # 2. pipeline (Step 1 + Step 2)
    pipeline_cmd = ["python3", "-u", "scripts/run_pipeline.py",
                    "--limit", str(args.limit),
                    "--concurrency", str(args.concurrency),
                    "--out-dir", str(out_dir)]
    if args.no_skip:
        pipeline_cmd.append("--no-skip")
    rc = step("Pipeline (Step 1 + Step 2)", pipeline_cmd, log_file=pipeline_log)
    if rc != 0:
        logger.error("Pipeline FAILED — see " + str(pipeline_log))
        sys.exit(rc)

    # 3. snapshot after + diff
    step("Snapshot metrics AFTER",
         ["python3", "scripts/snapshot_metrics.py", "after"],
         log_file=metrics_after)
    step("Metrics DELTA",
         ["python3", "scripts/snapshot_metrics.py", "diff",
          str(metrics_before), str(metrics_after)],
         log_file=metrics_delta)

    # 4. analiza
    step("Analiza wyników",
         ["python3", "scripts/analyze_phase2.py",
          "--entity-layer", str(entity_layer),
          "--final", str(final),
          "--out", str(summary),
          "--samples", str(args.samples)])

    logger.info("=== ALL DONE ===")
    logger.info(f"Output:")
    logger.info(f"  - {entity_layer.name}      (Step 1 wyniki)")
    logger.info(f"  - {final.name}              (Step 2 wyniki)")
    logger.info(f"  - {summary.name}            (raport)")
    logger.info(f"  - {metrics_delta.name}      (cache hit rate)")
    logger.info(f"  - {pipeline_log.name}       (full log)")


if __name__ == "__main__":
    main()
