"""End-to-end orchestrator: snapshot before → pipeline → snapshot after → analiza.

Tworzy katalog wyjściowy, robi pełen run, generuje raport. Wszystko w jednym
poleceniu — idealne do tmux.

Domyślny output: final_results/<YYYY-MM-DD_HH-MM-SS>/ (pełna ścieżka absolutna).
Można nadpisać --out-dir albo dodać sufiks --tag <name> (final_results/<ts>__<tag>/).

Resume:
    --resume               # dokończ NAJNOWSZY run z final_results/
    --resume <dir>         # dokończ konkretny run
Pipeline jest idempotentny po url_hash — istniejące rekordy są pomijane.

Użycie:
    python3 scripts/run_full.py --limit 0 --concurrency 8                  # auto timestamp
    python3 scripts/run_full.py --limit 0 --concurrency 8 --tag v6_b       # final_results/<ts>__v6_b/
    python3 scripts/run_full.py --limit 50 --concurrency 4 --out-dir foo   # custom
    python3 scripts/run_full.py --resume                                   # wznów najnowszy
    python3 scripts/run_full.py --resume final_results/2026-05-07_15-30-05__v6_full_2267
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import DEFAULT_CONCURRENCY  # noqa: E402

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
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--out-dir", default=None,
                        help="Custom katalog wyjściowy (override). Default: final_results/<timestamp>/")
    parser.add_argument("--tag", default=None,
                        help="Sufiks dla auto-timestamp dir, np. --tag v6 → final_results/<ts>__v6/")
    parser.add_argument("--resume", nargs="?", const="__latest__", default=None,
                        help="Wznów istniejący run. Bez argumentu = najnowszy z final_results/. "
                             "Z argumentem = konkretny katalog.")
    parser.add_argument("--samples", type=int, default=15, help="Liczba sample'i w raporcie")
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--random", action="store_true",
                        help="Losowa próbka --limit URL zamiast pierwszych N. Seed zapisywany "
                             "do <out_dir>/sample_seed.txt — przy --resume jest wczytywany "
                             "automatycznie żeby zachować ten sam zestaw URL.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed dla --random (default 42).")
    args = parser.parse_args()

    if args.resume:
        if args.resume == "__latest__":
            candidates = sorted(
                (p for p in DEFAULT_RESULTS_DIR.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if DEFAULT_RESULTS_DIR.exists() else []
            if not candidates:
                logger.error(f"Brak katalogów do wznowienia w {DEFAULT_RESULTS_DIR}")
                sys.exit(2)
            out_dir = candidates[0]
            logger.info(f"Resume najnowszego: {out_dir.name}")
        else:
            out_dir = Path(args.resume)
            if not out_dir.is_absolute():
                out_dir = ROOT / out_dir
            if not out_dir.exists():
                logger.error(f"Katalog do wznowienia nie istnieje: {out_dir}")
                sys.exit(2)
        # Pokaż ile jest do dokończenia
        ent = out_dir / "entity_layer.jsonl"
        fin = out_dir / "final.jsonl"
        ent_lines = sum(1 for _ in ent.open()) if ent.exists() else 0
        fin_lines = sum(1 for _ in fin.open()) if fin.exists() else 0
        logger.info(f"Resume status: entity_layer.jsonl={ent_lines} rec, final.jsonl={fin_lines} rec")
    elif args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"{ts}__{args.tag}" if args.tag else ts
        out_dir = DEFAULT_RESULTS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {out_dir}")

    # Sample seed handling — zapis przy first run, odczyt przy --resume.
    # Bez tego losowy sample przy resume rozjedzie się i pipeline zacznie
    # przetwarzać URL-e których nie ma w entity_layer/final.
    seed_file = out_dir / "sample_seed.txt"
    use_random = args.random
    use_seed = args.seed
    if seed_file.exists():
        # Wcześniejszy run używał randomu — wczytaj ten sam seed
        try:
            saved = seed_file.read_text().strip()
            saved_seed = int(saved)
            if args.resume or not args.random:
                use_random = True
                use_seed = saved_seed
                logger.info(f"Wczytano sample_seed.txt: --random --seed {saved_seed}")
            elif args.random and args.seed != saved_seed:
                logger.error(
                    f"Konflikt seedów: zapisany={saved_seed}, podany={args.seed}. "
                    f"Użyj --resume bez --seed, albo wystartuj nowy katalog."
                )
                sys.exit(2)
        except ValueError:
            logger.warning(f"Nieparsowalny sample_seed.txt: {seed_file}")
    elif args.random:
        seed_file.write_text(f"{args.seed}\n")
        logger.info(f"Zapisano sample_seed.txt: seed={args.seed}")

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
    if use_random:
        pipeline_cmd += ["--random", "--seed", str(use_seed)]
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
