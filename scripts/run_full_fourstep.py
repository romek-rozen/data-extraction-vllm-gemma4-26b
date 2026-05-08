"""End-to-end orchestrator dla four-step pipeline (v4): junk + meta + entities + sponsored.

Cel: jedno polecenie żeby:
1. sprawdzić że vLLM żyje
2. uruchomić full four-step pipeline
3. po skończeniu wyświetlić podsumowanie + per-domain top junk/sponsored

Działa jak `scripts/run_full.py` (dla two-step) — ale wraperuje
`scripts/run_fourstep_v1.py` z presetami i prettyprint summary.

Presety:
    --preset smoke           # 5 URL random — sanity test (~30s)
    --preset small           # 100 URL random
    --preset medium          # 500 URL random
    --preset large           # 1000 URL random
    --preset full            # 0 = wszystkie URL z websites/

Lub bezpośrednio --limit i --random.

Przykłady:

    # Smoke 5 URL — szybki test
    python3 scripts/run_full_fourstep.py --preset smoke

    # 1000 URL random, conc=6, w tmux
    tmux new -s benchmark
    python3 scripts/run_full_fourstep.py --preset large --tag v4_1000_test
    # Ctrl+B D

    # Pełen run na własnym scrape
    python3 scripts/run_full_fourstep.py --preset full \\
        --websites websites_intymnehistorie/ --tag intymne

    # Resume po crashu
    python3 scripts/run_full_fourstep.py --resume final_results/<dir>

    # Bez junk-skip (wszystkie URL przechodzą przez meta/entities/sponsored)
    python3 scripts/run_full_fourstep.py --preset small --no-skip-junk
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import FINAL_RESULT_DIR, VLLM_BASE_URL, WEBSITES_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("full4")
ROOT = Path(__file__).parent.parent

PRESETS = {
    "smoke":  {"limit": 5,    "random": True,  "concurrency": 4},
    "small":  {"limit": 100,  "random": True,  "concurrency": 6},
    "medium": {"limit": 500,  "random": True,  "concurrency": 6},
    "large":  {"limit": 1000, "random": True,  "concurrency": 6},
    "full":   {"limit": 0,    "random": False, "concurrency": 6},
}


def check_vllm_alive(timeout: float = 5.0) -> bool:
    try:
        r = requests.get(f"{VLLM_BASE_URL.rstrip('/')}/models", timeout=timeout)
        if r.ok:
            data = r.json()
            models = [m.get("id") for m in data.get("data", [])]
            logger.info(f"✓ vLLM alive at {VLLM_BASE_URL}, models: {models}")
            return True
    except Exception as e:
        logger.error(f"✗ vLLM unreachable at {VLLM_BASE_URL}: {e}")
    return False


def run_pipeline(args, out_dir: Path, log_file: Path | None) -> int:
    cmd = [
        sys.executable, "-u", str(ROOT / "scripts" / "run_fourstep_v1.py"),
        "--limit", str(args.limit),
        "--concurrency", str(args.concurrency),
        "--seed", str(args.seed),
        "--websites", args.websites,
        "--out-dir", str(out_dir),
    ]
    if args.random:
        cmd.append("--random")
    if args.no_skip_junk:
        cmd.append("--no-skip-junk")
    logger.info(f"RUN: {' '.join(cmd)}")
    if log_file:
        with open(log_file, "w") as f:
            r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


def print_summary(out_dir: Path) -> None:
    """Wyświetl summary.txt + per-domain top 10 junk + top 10 sponsored."""
    summary = out_dir / "summary.txt"
    if summary.exists():
        print()
        print("=" * 76)
        print(summary.read_text(encoding="utf-8").rstrip())
        print("=" * 76)

    # per-domain analysis (z final.jsonl + sponsored.jsonl)
    final_p = out_dir / "final.jsonl"
    spon_p = out_dir / "sponsored.jsonl"
    if not final_p.exists():
        return

    hash2dom = {}
    junk_cnt = defaultdict(lambda: {"total": 0, "junk": 0})
    for line in open(final_p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        h = r.get("url_hash")
        d = r.get("domain", "?")
        hash2dom[h] = d
        junk_cnt[d]["total"] += 1
        if r.get("is_junk"):
            junk_cnt[d]["junk"] += 1

    spon_cnt = defaultdict(lambda: {"total": 0, "spon": 0})
    if spon_p.exists():
        for line in open(spon_p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("ok"):
                continue
            d = hash2dom.get(r["url_hash"], "?")
            spon_cnt[d]["total"] += 1
            if r.get("sponsored"):
                spon_cnt[d]["spon"] += 1

    print()
    print("Top domeny po junk% (≥3 URL):")
    rows = sorted(
        ((d, v) for d, v in junk_cnt.items() if v["total"] >= 3),
        key=lambda x: (x[1]["junk"] / x[1]["total"], x[1]["total"]), reverse=True
    )
    print(f'  {"domain":40s}  total  junk  junk%')
    for d, v in rows[:10]:
        pct = v["junk"] / v["total"] * 100
        print(f'  {d[:40]:40s}  {v["total"]:5d}  {v["junk"]:4d}  {pct:5.1f}%')

    print()
    print("Top domeny po sponsored% (≥3 URL non-junk):")
    rows = sorted(
        ((d, v) for d, v in spon_cnt.items() if v["total"] >= 3),
        key=lambda x: (x[1]["spon"] / x[1]["total"], x[1]["total"]), reverse=True
    )
    print(f'  {"domain":40s}  non-junk  spons  spons%')
    for d, v in rows[:10]:
        pct = v["spon"] / v["total"] * 100
        print(f'  {d[:40]:40s}  {v["total"]:8d}  {v["spon"]:5d}  {pct:5.1f}%')

    print()
    print(f"Pełne wyniki: {out_dir}")
    print(f"  classified.jsonl  {final_p.parent.joinpath('classified.jsonl').stat().st_size if final_p.parent.joinpath('classified.jsonl').exists() else 0:>10} bytes")
    print(f"  meta.jsonl        {final_p.parent.joinpath('meta.jsonl').stat().st_size if final_p.parent.joinpath('meta.jsonl').exists() else 0:>10} bytes")
    print(f"  entities.jsonl    {final_p.parent.joinpath('entities.jsonl').stat().st_size if final_p.parent.joinpath('entities.jsonl').exists() else 0:>10} bytes")
    print(f"  sponsored.jsonl   {final_p.parent.joinpath('sponsored.jsonl').stat().st_size if final_p.parent.joinpath('sponsored.jsonl').exists() else 0:>10} bytes")
    print(f"  final.jsonl       {final_p.stat().st_size:>10} bytes")
    print()
    print("Dashboard:")
    print(f"  http://10.10.0.3:8501/?page=junk-analysis")
    print(f"  http://10.10.0.3:8501/?page=sponsored")


def main():
    ap = argparse.ArgumentParser(
        description="End-to-end fourstep pipeline (junk + meta + entities + sponsored)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Przykłady:", 1)[1] if "Przykłady:" in __doc__ else "",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--preset", choices=list(PRESETS.keys()),
                     help="Gotowy preset: smoke/small/medium/large/full")
    src.add_argument("--limit", type=int,
                     help="Liczba URL (override preset). 0 = wszystkie.")

    ap.add_argument("--concurrency", type=int,
                    help="Liczba workerów (Spark dławi się na 8 → default 6)")
    ap.add_argument("--random", action="store_true",
                    help="Random sample (zamiast pierwszych N alfabetycznie)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tag", default="",
                    help="Sufiks dir, np. --tag v4_1000 → final_results/<ts>__fourstep_v1_v4_1000/")
    ap.add_argument("--websites", default=str(WEBSITES_DIR),
                    help="Katalog wejściowy (np. websites/, websites_intymnehistorie/)")
    ap.add_argument("--out-dir", default=None,
                    help="Custom katalog wyjściowy (override default timestamp)")
    ap.add_argument("--resume", default=None,
                    help="Resume z istniejącego katalogu")
    ap.add_argument("--no-skip-junk", action="store_true",
                    help="Wszystkie URL idą przez meta/entities/sponsored (debug, bez junk early-exit)")
    ap.add_argument("--no-summary", action="store_true",
                    help="Nie drukuj podsumowania na końcu")
    ap.add_argument("--skip-vllm-check", action="store_true",
                    help="Pomiń check vLLM (dla testów offline)")

    args = ap.parse_args()

    # Aplikacja preset jeśli używany
    if args.preset:
        p = PRESETS[args.preset]
        if args.limit is None:
            args.limit = p["limit"]
        if not args.random:
            args.random = p["random"]
        if args.concurrency is None:
            args.concurrency = p["concurrency"]
        logger.info(f"Preset '{args.preset}': limit={args.limit} random={args.random} concurrency={args.concurrency}")
    else:
        if args.limit is None:
            args.limit = 100
        if args.concurrency is None:
            args.concurrency = 6

    # vLLM health check
    if not args.skip_vllm_check:
        if not check_vllm_alive():
            logger.error("vLLM nie żyje. Uruchom: bash scripts/start_vllm.sh")
            return 2

    # Output dir
    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.exists():
            logger.error(f"--resume: katalog {out_dir} nie istnieje")
            return 2
        logger.info(f"RESUME: {out_dir}")
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = f"__fourstep_v1_{args.tag}" if args.tag else "__fourstep_v1"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"out_dir = {out_dir}")

    # Run
    t0 = time.perf_counter()
    rc = run_pipeline(args, out_dir, log_file=None)
    dt = time.perf_counter() - t0
    logger.info(f"Pipeline rc={rc}  wall={dt:.1f}s ({dt/3600:.2f}h)")

    if rc == 0 and not args.no_summary:
        print_summary(out_dir)

    return rc


if __name__ == "__main__":
    sys.exit(main())
