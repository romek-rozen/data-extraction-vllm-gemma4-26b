"""Step 1: ekstrakcja encji + język + kategoria → entity_layer.jsonl.

Idempotentny — pomija URL już przetworzone (klucz: url_hash).

Użycie:
    python3 scripts/run_step1.py --limit 100
    python3 scripts/run_step1.py --limit 100 --concurrency 4
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
    RESULT_DIR,
    SAMPLING_STEP1,
    VLLM_BASE_URL,
    VLLM_MODEL,
    WEBSITES_DIR,
)
from lib.data_loader import load_articles  # noqa: E402
from lib.pipeline import process_step1  # noqa: E402
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("step1")


def process_one(client: VLLMClient, system: str, schema: dict, article: dict) -> dict:
    return process_step1(client, system, schema, article,
                         max_tokens=MAX_TOKENS_STEP1, sampling=SAMPLING_STEP1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Równoległe requesty do vLLM (serwer batchuje natywnie)")
    parser.add_argument("--out", default=str(RESULT_DIR / "entity_layer.jsonl"))
    parser.add_argument("--websites", default=str(WEBSITES_DIR))
    parser.add_argument("--no-skip", action="store_true",
                        help="Wymuś rerun nawet dla już przetworzonych URL")
    args = parser.parse_args()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    system = load_system_prompt("step1_system")
    schema = load_schema("schema_step1")
    reporter = JsonlReporter(args.out)
    existing = set() if args.no_skip else reporter.load_existing_hashes()
    if existing:
        logger.info(f"Skip {len(existing)} URL już w {args.out}")

    articles = load_articles(args.websites, limit=args.limit)
    todo = [a for a in articles if a["url_hash"] not in existing]
    logger.info(f"Do przetworzenia: {len(todo)}/{len(articles)}")

    t_start = time.perf_counter()
    n_ok = n_fail = 0

    if args.concurrency == 1:
        for i, art in enumerate(todo, 1):
            rec = process_one(client, system, schema, art)
            reporter.append(rec)
            if rec["ok"]:
                n_ok += 1
            else:
                n_fail += 1
                logger.warning(f"FAIL {art['id']}: {rec['error']}")
            if i % 10 == 0:
                logger.info(f"{i}/{len(todo)}  ok={n_ok} fail={n_fail}")
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(process_one, client, system, schema, a): a for a in todo}
            done = 0
            for fut in as_completed(futures):
                rec = fut.result()
                reporter.append(rec)
                done += 1
                if rec["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
                    logger.warning(f"FAIL {rec['id']}: {rec['error']}")
                if done % 10 == 0:
                    logger.info(f"{done}/{len(todo)}  ok={n_ok} fail={n_fail}")

    dt = time.perf_counter() - t_start
    logger.info(f"DONE  ok={n_ok}  fail={n_fail}  total={n_ok + n_fail}  time={dt:.1f}s  ({dt/max(n_ok+n_fail,1):.2f} s/req)")


if __name__ == "__main__":
    main()
