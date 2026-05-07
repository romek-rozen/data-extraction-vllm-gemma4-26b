"""Phase 3: A/B test parametrów samplingu na Step 1 i Step 2.

Konfigi (z INSTRUCTIONS_FROM_CLAUDE.md):

Step 1:
  A: temp=1.0, top_p=0.95, top_k=64  (Google default — baseline)
  B: temp=0.7, top_p=0.9,  top_k=50  (conservative)
  C: temp=0.3, top_p=0.9,  top_k=40  (aggressive low — "deterministic extraction" theory)

Step 2:
  A: temp=1.0, top_p=0.95, top_k=64  (Google default — baseline)
  B: temp=0.8, top_p=0.9,  top_k=50  (slightly lower)
  C: temp=0.5, top_p=0.9,  top_k=40  (conservative — mechanical meta)

Output:
  result/phase3_step{1,2}_<config>.jsonl
  result/phase3_step{1,2}_<config>_x{N}.jsonl  (consistency runs)

Użycie:
    # A/B/C dla Step 1 na 100 URL (zużywa entity_layer.jsonl jako baseline)
    python3 scripts/ab_sampling.py --step 1 --limit 100 --concurrency 4

    # A/B/C dla Step 2 na 100 URL (musi mieć result/entity_layer.jsonl)
    python3 scripts/ab_sampling.py --step 2 --limit 100 --concurrency 4

    # Consistency: 3× ten sam pierwszy URL z każdym configiem
    python3 scripts/ab_sampling.py --step 1 --limit 1 --runs 3
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import (  # noqa: E402
    MAX_TOKENS_STEP1,
    MAX_TOKENS_STEP2,
    RESULT_DIR,
    VLLM_BASE_URL,
    VLLM_MODEL,
    WEBSITES_DIR,
)
from lib.data_loader import load_articles  # noqa: E402
from lib.pipeline import process_step1, process_step2  # noqa: E402
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ab")


CONFIGS_STEP1 = {
    "A": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "repetition_penalty": 1.0},
    "B": {"temperature": 0.7, "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.0},
    "C": {"temperature": 0.3, "top_p": 0.9, "top_k": 40, "repetition_penalty": 1.0},
}
CONFIGS_STEP2 = {
    "A": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "repetition_penalty": 1.0},
    "B": {"temperature": 0.8, "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.0},
    "C": {"temperature": 0.5, "top_p": 0.9, "top_k": 40, "repetition_penalty": 1.0},
}


def run_step1(client, system, schema, articles, sampling, out_path, concurrency):
    reporter = JsonlReporter(out_path)
    n_ok = n_fail = 0
    t0 = time.perf_counter()
    if concurrency == 1:
        for art in articles:
            rec = process_step1(client, system, schema, art, MAX_TOKENS_STEP1, sampling)
            reporter.append(rec)
            if rec["ok"]:
                n_ok += 1
            else:
                n_fail += 1
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(process_step1, client, system, schema, a, MAX_TOKENS_STEP1, sampling)
                       for a in articles]
            for fut in as_completed(futures):
                rec = fut.result()
                reporter.append(rec)
                if rec["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
    dt = time.perf_counter() - t0
    return n_ok, n_fail, dt


def run_step2(client, system, schema, articles, entity_records, sampling, out_path, concurrency):
    reporter = JsonlReporter(out_path)
    n_ok = n_fail = 0
    t0 = time.perf_counter()
    todo = [(a, entity_records[a["url_hash"]]) for a in articles
            if a["url_hash"] in entity_records]
    if concurrency == 1:
        for art, ent in todo:
            rec = process_step2(client, system, schema, art, ent, MAX_TOKENS_STEP2, sampling)
            reporter.append(rec)
            if rec["ok"]:
                n_ok += 1
            else:
                n_fail += 1
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(process_step2, client, system, schema, a, e, MAX_TOKENS_STEP2, sampling)
                       for a, e in todo]
            for fut in as_completed(futures):
                rec = fut.result()
                reporter.append(rec)
                if rec["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
    dt = time.perf_counter() - t0
    return n_ok, n_fail, dt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, choices=[1, 2], required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--runs", type=int, default=1, help="Liczba przebiegów per config (consistency test)")
    parser.add_argument("--configs", default="A,B,C", help="Comma-separated config IDs")
    parser.add_argument("--websites", default=str(WEBSITES_DIR))
    parser.add_argument("--entity-layer", default=str(RESULT_DIR / "entity_layer.jsonl"),
                        help="Source dla Step 2 — output Step 1 z głównego runa")
    args = parser.parse_args()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    configs = CONFIGS_STEP1 if args.step == 1 else CONFIGS_STEP2
    selected = [c for c in args.configs.split(",") if c in configs]
    logger.info(f"Step {args.step}, configs: {selected}, limit: {args.limit}, runs: {args.runs}")

    articles = load_articles(args.websites, limit=args.limit)
    logger.info(f"Articles: {len(articles)}")

    if args.step == 1:
        system = load_system_prompt("step1_system")
        schema = load_schema("schema_step1")
        for cfg_id in selected:
            sampling = configs[cfg_id]
            for run_idx in range(1, args.runs + 1):
                suffix = f"_x{run_idx}" if args.runs > 1 else ""
                out = RESULT_DIR / f"phase3_step1_{cfg_id}{suffix}.jsonl"
                if out.exists():
                    out.unlink()  # czyste pliki dla każdego runa
                logger.info(f"=== Step 1 config {cfg_id} run {run_idx}/{args.runs} ===  sampling={sampling}")
                n_ok, n_fail, dt = run_step1(client, system, schema, articles, sampling, out, args.concurrency)
                logger.info(f"  ok={n_ok} fail={n_fail} time={dt:.1f}s ({dt/max(n_ok+n_fail,1):.2f} s/req)  → {out.name}")
    else:
        system = load_system_prompt("step2_system")
        schema = load_schema("schema_step2")
        entity_reporter = JsonlReporter(args.entity_layer)
        entity_records = {r["url_hash"]: r for r in entity_reporter.load_records()}
        if not entity_records:
            logger.error(f"Pusty entity layer: {args.entity_layer}. Uruchom najpierw run_step1.py")
            sys.exit(1)
        logger.info(f"Entity layer: {len(entity_records)} URL")

        for cfg_id in selected:
            sampling = configs[cfg_id]
            for run_idx in range(1, args.runs + 1):
                suffix = f"_x{run_idx}" if args.runs > 1 else ""
                out = RESULT_DIR / f"phase3_step2_{cfg_id}{suffix}.jsonl"
                if out.exists():
                    out.unlink()
                logger.info(f"=== Step 2 config {cfg_id} run {run_idx}/{args.runs} ===  sampling={sampling}")
                n_ok, n_fail, dt = run_step2(client, system, schema, articles, entity_records, sampling, out, args.concurrency)
                logger.info(f"  ok={n_ok} fail={n_fail} time={dt:.1f}s ({dt/max(n_ok+n_fail,1):.2f} s/req)  → {out.name}")

    logger.info("DONE")


if __name__ == "__main__":
    main()
