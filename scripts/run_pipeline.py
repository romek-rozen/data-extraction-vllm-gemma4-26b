"""Pełen pipeline two-step: Step 1 → Step 2.

Wrapper nad run_step1 + run_step2. Idempotentny — można wznowić.

Użycie:
    python3 scripts/run_pipeline.py --limit 200
    python3 scripts/run_pipeline.py --limit 200 --concurrency 4
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pipeline")

ROOT = Path(__file__).parent.parent


def run(cmd: list[str]) -> int:
    logger.info("RUN: " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--no-skip", action="store_true")
    args = parser.parse_args()

    common = ["--limit", str(args.limit), "--concurrency", str(args.concurrency)]
    if args.no_skip:
        common.append("--no-skip")

    rc1 = run(["python3", "-u", "scripts/run_step1.py", *common])
    if rc1 != 0:
        logger.error(f"Step 1 failed (rc={rc1})")
        sys.exit(rc1)

    rc2 = run(["python3", "-u", "scripts/run_step2.py", *common])
    if rc2 != 0:
        logger.error(f"Step 2 failed (rc={rc2})")
        sys.exit(rc2)

    logger.info("Pipeline DONE")


if __name__ == "__main__":
    main()
