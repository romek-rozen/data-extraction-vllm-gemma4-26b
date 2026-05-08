"""Four-step v1 orchestrator: classify → (meta || entities || sponsored).

Single ThreadPoolExecutor + 4 kolejki priorytetowe:
- classify → najwyższy priorytet (drain ASAP, szybki ~0.2s)
- meta, entities, sponsored → równo (round-robin / longest-queue-first)

Po classify dla non-junk fan-out na 3 równoległe LLM calls. Junk → krótka ścieżka
do final.jsonl bez 3 dodatkowych wywołań.

Reuse: lib.pipeline_fourstep_v1 (process_classify_v2, process_meta_v2,
process_entities_v2, process_sponsored_v1, join_final_v4).

Użycie:
    python3 scripts/run_fourstep_v1.py --limit 500 --random --tag v4_500
    python3 scripts/run_fourstep_v1.py --limit 500 --random --concurrency 6 --tag v4_500_c6
    python3 scripts/run_fourstep_v1.py --resume final_results/<ts>__fourstep_v1_<tag>
    python3 scripts/run_fourstep_v1.py --limit 0 --tag intymne --websites websites_intymnehistorie
"""

import argparse
import csv
import json
import logging
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import (  # noqa: E402
    FINAL_RESULT_DIR,
    MAX_TOKENS_STEP1,
    MAX_TOKENS_STEP2,
    SAMPLING_STEP1,
    SAMPLING_STEP2,
    VLLM_BASE_URL,
    VLLM_MODEL,
    WEBSITES_DIR,
)
from lib.data_loader import load_articles  # noqa: E402
from lib.pipeline_fourstep_v1 import (  # noqa: E402
    join_final_v4,
    make_junk_stub_final_v4,
    process_classify_v2,
    process_entities_v2,
    process_meta_v2,
    process_sponsored_v1,
)
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fourstep_v1")

# Sponsored: niska temp (deterministyczna klasyfikacja), output ~30-50 tok
# (sponsored bool + subtype + justification 120 chars).
SAMPLING_SPONSORED = {"temperature": 0.4, "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.0}
MAX_TOKENS_SPONSORED = 256  # Justification max 120 chars + envelope JSON, bufor 256


def _make_phase_logger(name: str, log_path: Path) -> logging.Logger:
    lg = logging.getLogger(f"fourstep_v1.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = True
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(fh)
    return lg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="0 = wszystkie z websites/")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="Liczba workerów w pool (Spark dławi się na 8 → default 6)")
    ap.add_argument("--tag", default="")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--websites", default=str(WEBSITES_DIR))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--no-skip-junk", action="store_true")
    args = ap.parse_args()

    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.exists():
            sys.exit(f"--resume: katalog {out_dir} nie istnieje")
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = f"__fourstep_v1_{args.tag}" if args.tag else "__fourstep_v1"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"out_dir = {out_dir}")

    log_classify = _make_phase_logger("classify", out_dir / "classify.log")
    log_meta = _make_phase_logger("meta", out_dir / "meta.log")
    log_entities = _make_phase_logger("entities", out_dir / "entities.log")
    log_sponsored = _make_phase_logger("sponsored", out_dir / "sponsored.log")
    log_run = _make_phase_logger("run", out_dir / "run.log")
    log_run.info(f"START out_dir={out_dir}  concurrency={args.concurrency}  websites={args.websites}")

    rep_classify = JsonlReporter(out_dir / "classified.jsonl")
    rep_meta = JsonlReporter(out_dir / "meta.jsonl")
    rep_entities = JsonlReporter(out_dir / "entities.jsonl")
    rep_sponsored = JsonlReporter(out_dir / "sponsored.jsonl")
    rep_final = JsonlReporter(out_dir / "final.jsonl")
    done_classify = rep_classify.load_existing_hashes()
    done_meta = rep_meta.load_existing_hashes()
    done_entities = rep_entities.load_existing_hashes()
    done_sponsored = rep_sponsored.load_existing_hashes()
    done_final = rep_final.load_existing_hashes()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    sys_classify = load_system_prompt("step_junkclassify_v2_system")
    sys_meta = load_system_prompt("step_meta_v2_system")
    schema_meta = load_schema("schema_meta_v2")
    sys_ent = load_system_prompt("step_entities_v2_system")
    schema_ent = load_schema("schema_entities_v2")
    sys_spon = load_system_prompt("step_sponsored_v1_system")
    schema_spon = load_schema("schema_sponsored_v1")

    articles = load_articles(args.websites, limit=args.limit, random_sample=args.random, seed=args.seed)
    todo = [a for a in articles if a["url_hash"] not in done_final]
    logger.info(f"loaded={len(articles)}  todo={len(todo)}  done_final={len(done_final)}")

    # Cache classify dla resume
    classify_cache: dict[str, dict] = {}
    if done_classify and (out_dir / "classified.jsonl").exists():
        for line in open(out_dir / "classified.jsonl", encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("ok") and r.get("url_hash"):
                    classify_cache[r["url_hash"]] = r
            except json.JSONDecodeError:
                continue

    state: dict[str, dict] = {}
    state_lock = threading.Lock()

    counters = {
        "classify_ok": 0, "classify_fail": 0, "junk": 0,
        "meta_ok": 0, "meta_fail": 0,
        "entities_ok": 0, "entities_fail": 0,
        "sponsored_ok": 0, "sponsored_fail": 0,
        "sponsored_true": 0,
        "final_ok": 0, "final_fail": 0,
        "pending": 0,
    }
    cnt_lock = threading.Lock()

    timing_rows: list[dict] = []
    timing_lock = threading.Lock()

    def bump(k: str, d: int = 1):
        with cnt_lock:
            counters[k] += d

    def add_timing(phase: str, h: str, lat: float, ok: bool, attempts: int):
        with timing_lock:
            timing_rows.append({"phase": phase, "url_hash": h, "latency_s": round(lat, 3),
                                "ok": ok, "attempts": attempts})

    q_classify: queue.Queue = queue.Queue()
    q_meta: queue.Queue = queue.Queue()
    q_entities: queue.Queue = queue.Queue()
    q_sponsored: queue.Queue = queue.Queue()
    producer_done = threading.Event()
    shutdown = threading.Event()

    def try_finalize(h: str):
        with state_lock:
            s = state.get(h, {})
            if s.get("is_junk_short_circuit"):
                return
            if "article" not in s or "classify" not in s:
                return
            if "meta" not in s or "entities" not in s or "sponsored" not in s:
                return
            article = s["article"]; classify_rec = s["classify"]
            meta_rec = s["meta"]; ent_rec = s["entities"]; spon_rec = s["sponsored"]
            state.pop(h, None)
        final = join_final_v4(article, classify_rec, meta_rec, ent_rec, spon_rec)
        rep_final.append(final)
        bump("final_ok" if final["ok"] else "final_fail")
        if final.get("sponsored"):
            bump("sponsored_true")
        bump("pending", -1)

    def fan_out_after_classify(article: dict, classify_rec: dict):
        is_junk = classify_rec.get("is_junk", False)
        if is_junk and not args.no_skip_junk:
            bump("junk")
            stub = make_junk_stub_final_v4(article, classify_rec)
            rep_final.append(stub)
            bump("final_ok")
            with state_lock:
                state[article["url_hash"]] = {"is_junk_short_circuit": True}
            bump("pending", -1)
            return
        with state_lock:
            state[article["url_hash"]] = {"article": article, "classify": classify_rec}
        # 3-way fan-out (meta, entities, sponsored). Każdy ma osobną kolejkę.
        if article["url_hash"] not in done_meta:
            q_meta.put(article)
        else:
            with state_lock:
                state[article["url_hash"]]["meta"] = {"ok": True}
        if article["url_hash"] not in done_entities:
            q_entities.put(article)
        else:
            with state_lock:
                state[article["url_hash"]]["entities"] = {"ok": True}
        if article["url_hash"] not in done_sponsored:
            q_sponsored.put(article)
        else:
            with state_lock:
                state[article["url_hash"]]["sponsored"] = {"ok": True}
        try_finalize(article["url_hash"])  # przy resume wszystko może być done

    def handle_classify(article):
        rec = process_classify_v2(VLLM_BASE_URL, VLLM_MODEL, sys_classify, article)
        rep_classify.append(rec)
        add_timing("classify", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        if not rec["ok"]:
            bump("classify_fail")
            log_classify.warning(f"FAIL {article['id']}: {rec['error']}")
            rep_final.append({
                "url_hash": article["url_hash"], "id": article["id"], "url": article["url"],
                "ok": False, "error": "classify_failed", "is_junk": False,
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
            bump("final_fail")
            bump("pending", -1)
            return
        bump("classify_ok")
        log_classify.info(f"OK {article['id']} is_junk={rec.get('is_junk')} lat={rec['latency_s']}s "
                          f"(running ok={counters['classify_ok']} junk={counters['junk']})")
        fan_out_after_classify(article, rec)

    def handle_meta(article):
        rec = process_meta_v2(client, sys_meta, schema_meta, article,
                              max_tokens=MAX_TOKENS_STEP2, sampling=SAMPLING_STEP2)
        rep_meta.append(rec)
        add_timing("meta", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        bump("meta_ok" if rec["ok"] else "meta_fail")
        if not rec["ok"]:
            log_meta.warning(f"FAIL {article['id']}: {rec['error']}")
        else:
            log_meta.info(f"OK {article['id']} cat={rec.get('category')} lang={rec.get('language')} "
                          f"lat={rec['latency_s']}s tok_in={rec['usage'].get('prompt_tokens', 0)} "
                          f"tok_out={rec['usage'].get('completion_tokens', 0)} attempts={rec.get('attempts',1)} "
                          f"(running ok={counters['meta_ok']} fail={counters['meta_fail']})")
        with state_lock:
            state.setdefault(article["url_hash"], {})["meta"] = rec
        try_finalize(article["url_hash"])

    def handle_entities(article):
        rec = process_entities_v2(client, sys_ent, schema_ent, article,
                                  max_tokens=MAX_TOKENS_STEP1, sampling=SAMPLING_STEP1)
        rep_entities.append(rec)
        add_timing("entities", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        bump("entities_ok" if rec["ok"] else "entities_fail")
        if not rec["ok"]:
            log_entities.warning(f"FAIL {article['id']}: {rec['error']}")
        else:
            log_entities.info(f"OK {article['id']} n_entities={len(rec.get('entities', []))} "
                              f"lat={rec['latency_s']}s tok_in={rec['usage'].get('prompt_tokens', 0)} "
                              f"tok_out={rec['usage'].get('completion_tokens', 0)} attempts={rec.get('attempts',1)} "
                              f"(running ok={counters['entities_ok']} fail={counters['entities_fail']})")
        with state_lock:
            state.setdefault(article["url_hash"], {})["entities"] = rec
        try_finalize(article["url_hash"])

    def handle_sponsored(article):
        rec = process_sponsored_v1(client, sys_spon, schema_spon, article,
                                   max_tokens=MAX_TOKENS_SPONSORED, sampling=SAMPLING_SPONSORED)
        rep_sponsored.append(rec)
        add_timing("sponsored", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        bump("sponsored_ok" if rec["ok"] else "sponsored_fail")
        if not rec["ok"]:
            log_sponsored.warning(f"FAIL {article['id']}: {rec['error']}")
        else:
            log_sponsored.info(f"OK {article['id']} sponsored={rec.get('sponsored')} "
                               f"subtype={rec.get('sponsored_subtype')!r} "
                               f"just={rec.get('sponsored_justification', '')[:80]!r} "
                               f"lat={rec['latency_s']}s "
                               f"(running ok={counters['sponsored_ok']} fail={counters['sponsored_fail']})")
        with state_lock:
            state.setdefault(article["url_hash"], {})["sponsored"] = rec
        try_finalize(article["url_hash"])

    def worker():
        """Wzorzec A — single pool, priority pull classify > {meta, entities, sponsored} z load-balancingiem.

        Po opróżnieniu classify queue, 3 kolejki konsumenckie (meta/entities/sponsored)
        są obsługiwane fairly — bierzemy z najdłuższej (catching-up). Przy remisie
        round-robin (toggle) żeby nie głodzić żadnej.
        """
        toggle = [0]  # 0=meta, 1=entities, 2=sponsored
        while True:
            if shutdown.is_set():
                return
            # P1: classify
            try:
                art = q_classify.get_nowait()
                try:
                    handle_classify(art)
                finally:
                    q_classify.task_done()
                continue
            except queue.Empty:
                pass
            # P2: load-balanced wybór z {meta, entities, sponsored}
            sizes = {"meta": q_meta.qsize(), "entities": q_entities.qsize(), "sponsored": q_sponsored.qsize()}
            if sizes["meta"] == 0 and sizes["entities"] == 0 and sizes["sponsored"] == 0:
                # Wszystkie puste — czekaj z timeoutem na cokolwiek (entities)
                try:
                    art = q_entities.get(timeout=0.1)
                    try:
                        handle_entities(art)
                    finally:
                        q_entities.task_done()
                    continue
                except queue.Empty:
                    if producer_done.is_set():
                        with cnt_lock:
                            if counters["pending"] == 0:
                                return
                    continue
            # Wybór: longest queue first, przy remisie round-robin
            max_size = max(sizes.values())
            candidates = [k for k, v in sizes.items() if v == max_size and v > 0]
            if len(candidates) == 1:
                pick = candidates[0]
            else:
                # round-robin po toggle
                order = ["meta", "entities", "sponsored"]
                start = toggle[0] % 3
                pick = None
                for i in range(3):
                    candidate = order[(start + i) % 3]
                    if candidate in candidates:
                        pick = candidate
                        break
                toggle[0] = (toggle[0] + 1) % 3
            qmap = {"meta": q_meta, "entities": q_entities, "sponsored": q_sponsored}
            handler_map = {"meta": handle_meta, "entities": handle_entities, "sponsored": handle_sponsored}
            try:
                art = qmap[pick].get_nowait()
                try:
                    handler_map[pick](art)
                finally:
                    qmap[pick].task_done()
                continue
            except queue.Empty:
                # Race: ktoś inny zabrał. Spróbuj ponownie krótko.
                time.sleep(0.02)

    t_start = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=args.concurrency, thread_name_prefix="w")
    futures = [pool.submit(worker) for _ in range(args.concurrency)]

    for art in todo:
        bump("pending", 1)
        if art["url_hash"] in done_classify and art["url_hash"] in classify_cache:
            fan_out_after_classify(art, classify_cache[art["url_hash"]])
        else:
            q_classify.put(art)
    producer_done.set()

    for f in futures:
        f.result()
    pool.shutdown(wait=True)

    dt = time.perf_counter() - t_start

    with open(out_dir / "timing.csv", "a", newline="", encoding="utf-8") as f:
        if f.tell() == 0:
            csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"]).writeheader()
        w = csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"])
        for row in timing_rows:
            w.writerow(row)

    run_meta = {
        "limit": args.limit,
        "concurrency": args.concurrency,
        "pattern": "A_single_pool_4q_priority_load_balanced",
        "random_sample": args.random,
        "seed": args.seed,
        "skip_junk": not args.no_skip_junk,
        "websites": args.websites,
        "wall_s": round(dt, 1),
        "started_at": datetime.fromtimestamp(time.time() - dt).isoformat(timespec="seconds"),
        "ended_at": datetime.now().isoformat(timespec="seconds"),
        "counters": counters,
        "n_articles": len(articles),
        "n_todo": len(todo),
    }
    with open(out_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    n = len(todo) or 1
    summary = f"""=== {out_dir.name} ===
  limit={args.limit}  random={args.random}  seed={args.seed}  skip_junk={not args.no_skip_junk}
  pattern=wzorzec A (single pool {args.concurrency}, 4 priority queues classify > meta/entities/sponsored load-balanced)
  websites={args.websites}

WALL: {dt:.1f}s ({dt/3600:.2f} h)  → {n}/wall = {n/(dt or 1)*3600:.0f} URL/h  ({dt/n:.2f} s/URL)

LICZNIKI:
  classify  ok={counters['classify_ok']} fail={counters['classify_fail']}  (junk={counters['junk']})
  meta      ok={counters['meta_ok']} fail={counters['meta_fail']}
  entities  ok={counters['entities_ok']} fail={counters['entities_fail']}
  sponsored ok={counters['sponsored_ok']} fail={counters['sponsored_fail']}  (sponsored_true={counters['sponsored_true']})
  final     ok={counters['final_ok']} fail={counters['final_fail']}

JUNK%:      {counters['junk']/max(counters['classify_ok'],1)*100:.2f}%
SPONSORED%: {counters['sponsored_true']/max(counters['sponsored_ok'],1)*100:.2f}% (z non-junk OK)
"""
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    log_run.info(summary)
    log_run.info(f"DONE  wall={dt:.1f}s  out_dir={out_dir}")


if __name__ == "__main__":
    main()
