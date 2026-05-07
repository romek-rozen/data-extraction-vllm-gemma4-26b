"""One-step runner: ekstrakcja encji + język + kategoria + SEO meta w jednym zapytaniu.

Test porównawczy do two-step (run_pipeline.py). Idempotentny po url_hash.

Użycie:
    python3 scripts/run_onestep.py --limit 20 --concurrency 4
    python3 scripts/run_onestep.py --limit 20 --out final_results/onestep_20.jsonl
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from lib.config import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    NUM_PREDICT,
    RESULT_DIR,
    SAMPLING_STEP1,
    VLLM_BASE_URL,
    VLLM_MODEL,
    WEBSITES_DIR,
)
from lib.data_loader import load_articles  # noqa: E402
from lib.pipeline_onestep import process_onestep  # noqa: E402
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("onestep")


# One-step łączy obowiązki Step 1 (output ~750 tok worst case) + Step 2 (~350 tok).
# Bufor 4096 (NUM_PREDICT) z headroomem; retry-with-feedback łapie ewentualne overflow'y.
MAX_TOKENS_ONESTEP = NUM_PREDICT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--out", default=str(RESULT_DIR / "onestep.jsonl"))
    parser.add_argument("--websites", default=str(WEBSITES_DIR))
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--random", action="store_true",
                        help="Losowa próbka zamiast pierwszych N (po sortowaniu). Reproducible przez --seed.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed dla --random (default 42). Ten sam seed = ten sam zestaw URL.")
    parser.add_argument("--system", default="step_onestep_system",
                        help="Nazwa pliku system promptu (bez .md) w prompts/")
    parser.add_argument("--schema", default="schema_onestep",
                        help="Nazwa pliku schema (bez .json) w prompts/")
    args = parser.parse_args()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    system = load_system_prompt(args.system)
    schema = load_schema(args.schema)
    reporter = JsonlReporter(args.out)
    existing = set() if args.no_skip else reporter.load_existing_hashes()
    if existing:
        logger.info(f"Skip {len(existing)} URL już w {args.out}")

    articles = load_articles(args.websites, limit=args.limit,
                             random_sample=args.random, seed=args.seed)
    todo = [a for a in articles if a["url_hash"] not in existing]
    logger.info(f"Do przetworzenia: {len(todo)}/{len(articles)}")

    t_start = time.perf_counter()
    n_ok = n_fail = 0

    def _one(art):
        return process_onestep(client, system, schema, art,
                               max_tokens=MAX_TOKENS_ONESTEP, sampling=SAMPLING_STEP1)

    if args.concurrency == 1:
        for i, art in enumerate(todo, 1):
            rec = _one(art)
            reporter.append(rec)
            n_ok += int(rec["ok"])
            n_fail += int(not rec["ok"])
            if not rec["ok"]:
                logger.warning(f"FAIL {art['id']}: {rec['error']}")
            if i % 10 == 0:
                logger.info(f"{i}/{len(todo)}  ok={n_ok} fail={n_fail}")
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(_one, a): a for a in todo}
            done = 0
            for fut in as_completed(futures):
                rec = fut.result()
                reporter.append(rec)
                done += 1
                n_ok += int(rec["ok"])
                n_fail += int(not rec["ok"])
                if not rec["ok"]:
                    logger.warning(f"FAIL {rec['id']}: {rec['error']}")
                if done % 10 == 0:
                    logger.info(f"{done}/{len(todo)}  ok={n_ok} fail={n_fail}")

    dt = time.perf_counter() - t_start
    n_total = n_ok + n_fail
    logger.info(
        f"DONE  ok={n_ok}  fail={n_fail}  total={n_total}  "
        f"time={dt:.1f}s  ({dt/max(n_total,1):.2f} s/req)"
    )


if __name__ == "__main__":
    main()
