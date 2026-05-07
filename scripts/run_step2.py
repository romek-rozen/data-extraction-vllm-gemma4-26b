"""Step 2: SEO meta generation z entity_layer.jsonl → final.jsonl.

Wymaga zrobionego Step 1 (entity_layer.jsonl). Łączy artykuł z context
(language, category, entities) i generuje SEO meta w języku artykułu.

Użycie:
    python3 scripts/run_step2.py --limit 100
    python3 scripts/run_step2.py --concurrency 4
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    MAX_TOKENS_STEP2,
    RESULT_DIR,
    SAMPLING_STEP2,
    VLLM_BASE_URL,
    VLLM_MODEL,
    WEBSITES_DIR,
)
from lib.data_loader import load_articles  # noqa: E402
from lib.pipeline import process_step2  # noqa: E402
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("step2")


def process_one(client, system, schema, article, entity_record):
    return process_step2(client, system, schema, article, entity_record,
                         max_tokens=MAX_TOKENS_STEP2, sampling=SAMPLING_STEP2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--entity-layer", default=str(RESULT_DIR / "entity_layer.jsonl"))
    parser.add_argument("--out", default=str(RESULT_DIR / "final.jsonl"))
    parser.add_argument("--websites", default=str(WEBSITES_DIR))
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--random", action="store_true",
                        help="Weź losową próbkę --limit URL (musi być spójne z run_step1).")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed dla --random — MUSI być ten sam co w run_step1.")
    args = parser.parse_args()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    system = load_system_prompt("step2_system")
    schema = load_schema("schema_step2")
    reporter = JsonlReporter(args.out)
    existing = set() if args.no_skip else reporter.load_existing_hashes()

    # wczytaj entity layer (Step 1 output) — dedup po url_hash, zostaje OSTATNI
    entity_reporter = JsonlReporter(args.entity_layer)
    entity_records = {r["url_hash"]: r for r in entity_reporter.load_records()}
    if not entity_records:
        logger.error(f"Pusty entity layer: {args.entity_layer}. Uruchom najpierw run_step1.py")
        sys.exit(1)

    articles = load_articles(args.websites, limit=args.limit,
                             random_sample=args.random, seed=args.seed)
    todo = []
    for a in articles:
        if a["url_hash"] in existing:
            continue
        if a["url_hash"] not in entity_records:
            logger.warning(f"Brak Step 1 dla {a['id']} — skip")
            continue
        todo.append((a, entity_records[a["url_hash"]]))

    logger.info(f"Do przetworzenia: {len(todo)}")

    t_start = time.perf_counter()
    n_ok = n_fail = 0

    if args.concurrency == 1:
        for i, (art, ent) in enumerate(todo, 1):
            rec = process_one(client, system, schema, art, ent)
            reporter.append(rec)
            if rec["ok"]:
                n_ok += 1
            else:
                n_fail += 1
                logger.warning(f"FAIL {art['id']}: {rec.get('error')}")
            if i % 10 == 0:
                logger.info(f"{i}/{len(todo)}  ok={n_ok} fail={n_fail}")
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(process_one, client, system, schema, a, e): a["id"]
                       for a, e in todo}
            done = 0
            for fut in as_completed(futures):
                rec = fut.result()
                reporter.append(rec)
                done += 1
                if rec["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
                if done % 10 == 0:
                    logger.info(f"{done}/{len(todo)}  ok={n_ok} fail={n_fail}")

    dt = time.perf_counter() - t_start
    logger.info(f"DONE  ok={n_ok}  fail={n_fail}  total={n_ok + n_fail}  time={dt:.1f}s  ({dt/max(n_ok+n_fail,1):.2f} s/req)")


if __name__ == "__main__":
    main()
