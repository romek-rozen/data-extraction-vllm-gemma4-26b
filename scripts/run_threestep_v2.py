"""Three-step v2 orchestrator: classify (binary, truncated) → fan-out (meta || entities).

Wszystko nowe — nie nadpisuje run_threestep.py ani run_step{1,2}.py.

Użycie:
    python3 scripts/run_threestep_v2.py --limit 500 --random --tag v2_500
    python3 scripts/run_threestep_v2.py --resume final_results/<ts>__threestep_v2_v2_500
    python3 scripts/run_threestep_v2.py --limit 50 --random --no-skip-junk  # debug
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
from lib.pipeline_threestep_v2 import (  # noqa: E402
    join_final_v2,
    make_junk_stub_final_v2,
    process_classify_v2,
    process_entities_v2,
    process_meta_v2,
)
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("threestep_v2")


def _make_phase_logger(name: str, log_path: Path) -> logging.Logger:
    """Per-faza logger pisany do osobnego pliku + propagowany do root (terminal)."""
    lg = logging.getLogger(f"threestep_v2.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = True  # leci też na stdout przez root logger
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(fh)
    return lg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--concurrency-classify", type=int, default=6)
    ap.add_argument("--concurrency-meta", type=int, default=3)
    ap.add_argument("--concurrency-entities", type=int, default=3)
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
        suffix = f"__threestep_v2_{args.tag}" if args.tag else "__threestep_v2"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"out_dir = {out_dir}")

    log_classify = _make_phase_logger("classify", out_dir / "classify.log")
    log_meta = _make_phase_logger("meta", out_dir / "meta.log")
    log_entities = _make_phase_logger("entities", out_dir / "entities.log")
    log_run = _make_phase_logger("run", out_dir / "run.log")
    log_run.info(f"START out_dir={out_dir}")

    rep_classify = JsonlReporter(out_dir / "classified.jsonl")
    rep_meta = JsonlReporter(out_dir / "meta.jsonl")
    rep_entities = JsonlReporter(out_dir / "entities.jsonl")
    rep_final = JsonlReporter(out_dir / "final.jsonl")
    done_classify = rep_classify.load_existing_hashes()
    done_meta = rep_meta.load_existing_hashes()
    done_entities = rep_entities.load_existing_hashes()
    done_final = rep_final.load_existing_hashes()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    sys_classify = load_system_prompt("step_junkclassify_v2_system")
    sys_meta = load_system_prompt("step_meta_v2_system")
    schema_meta = load_schema("schema_meta_v2")
    sys_ent = load_system_prompt("step_entities_v2_system")
    schema_ent = load_schema("schema_entities_v2")

    articles = load_articles(args.websites, limit=args.limit, random_sample=args.random, seed=args.seed)
    todo = [a for a in articles if a["url_hash"] not in done_final]
    logger.info(f"loaded={len(articles)}  todo={len(todo)}  done_final={len(done_final)}")

    # cache na classify (potrzebne przy resume)
    classify_cache: dict[str, dict] = {}
    if done_classify:
        for line in open(out_dir / "classified.jsonl", encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("ok") and r.get("url_hash"):
                    classify_cache[r["url_hash"]] = r
            except json.JSONDecodeError:
                continue

    state: dict[str, dict] = {}
    state_lock = threading.Lock()

    counters = {"classify_ok": 0, "classify_fail": 0, "junk": 0,
                "meta_ok": 0, "meta_fail": 0,
                "entities_ok": 0, "entities_fail": 0,
                "final_ok": 0, "final_fail": 0}
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

    q_cls: queue.Queue = queue.Queue()
    q_meta: queue.Queue = queue.Queue()
    q_ent: queue.Queue = queue.Queue()
    SENTINEL = None

    def try_finalize(h: str):
        with state_lock:
            s = state.get(h, {})
            if s.get("is_junk_short_circuit"):
                return
            if "article" not in s or "classify" not in s:
                return
            if "meta" not in s or "entities" not in s:
                return
            article = s["article"]; classify_rec = s["classify"]
            meta_rec = s["meta"]; ent_rec = s["entities"]
            state.pop(h, None)
        final = join_final_v2(article, classify_rec, meta_rec, ent_rec)
        rep_final.append(final)
        bump("final_ok" if final["ok"] else "final_fail")

    def fan_out_after_classify(article: dict, classify_rec: dict):
        """Po classify: jeśli junk → stub do final. W przeciwnym razie meta+entities."""
        is_junk = classify_rec.get("is_junk", False)
        if is_junk and not args.no_skip_junk:
            bump("junk")
            stub = make_junk_stub_final_v2(article, classify_rec)
            rep_final.append(stub)
            bump("final_ok")
            with state_lock:
                state[article["url_hash"]] = {"is_junk_short_circuit": True}
            return
        with state_lock:
            state[article["url_hash"]] = {"article": article, "classify": classify_rec}
        if article["url_hash"] not in done_meta:
            q_meta.put(article)
        else:
            with state_lock:
                state[article["url_hash"]]["meta"] = {"ok": True}
        if article["url_hash"] not in done_entities:
            q_ent.put(article)
        else:
            with state_lock:
                state[article["url_hash"]]["entities"] = {"ok": True}

    def classify_worker():
        while True:
            item = q_cls.get()
            if item is SENTINEL:
                q_cls.task_done()
                return
            article = item
            try:
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
                    continue
                bump("classify_ok")
                log_classify.info(f"OK {article['id']} is_junk={rec.get('is_junk')} lat={rec['latency_s']}s "
                                  f"(running ok={counters['classify_ok']} junk={counters['junk']})")
                fan_out_after_classify(article, rec)
            finally:
                q_cls.task_done()

    def meta_worker():
        while True:
            item = q_meta.get()
            if item is SENTINEL:
                q_meta.task_done()
                return
            article = item
            try:
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
                                  f"tok_out={rec['usage'].get('completion_tokens', 0)} "
                                  f"(running ok={counters['meta_ok']} fail={counters['meta_fail']})")
                with state_lock:
                    state.setdefault(article["url_hash"], {})["meta"] = rec
                try_finalize(article["url_hash"])
            finally:
                q_meta.task_done()

    def entities_worker():
        while True:
            item = q_ent.get()
            if item is SENTINEL:
                q_ent.task_done()
                return
            article = item
            try:
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
                                      f"tok_out={rec['usage'].get('completion_tokens', 0)} "
                                      f"(running ok={counters['entities_ok']} fail={counters['entities_fail']})")
                with state_lock:
                    state.setdefault(article["url_hash"], {})["entities"] = rec
                try_finalize(article["url_hash"])
            finally:
                q_ent.task_done()

    t_start = time.perf_counter()
    p_cls = ThreadPoolExecutor(max_workers=args.concurrency_classify, thread_name_prefix="cls")
    p_meta = ThreadPoolExecutor(max_workers=args.concurrency_meta, thread_name_prefix="meta")
    p_ent = ThreadPoolExecutor(max_workers=args.concurrency_entities, thread_name_prefix="ent")
    for _ in range(args.concurrency_classify):
        p_cls.submit(classify_worker)
    for _ in range(args.concurrency_meta):
        p_meta.submit(meta_worker)
    for _ in range(args.concurrency_entities):
        p_ent.submit(entities_worker)

    # producer
    for art in todo:
        if art["url_hash"] in done_classify and art["url_hash"] in classify_cache:
            fan_out_after_classify(art, classify_cache[art["url_hash"]])
        else:
            q_cls.put(art)

    q_cls.join()
    for _ in range(args.concurrency_classify):
        q_cls.put(SENTINEL)
    p_cls.shutdown(wait=True)

    q_meta.join()
    q_ent.join()
    for _ in range(args.concurrency_meta):
        q_meta.put(SENTINEL)
    for _ in range(args.concurrency_entities):
        q_ent.put(SENTINEL)
    p_meta.shutdown(wait=True)
    p_ent.shutdown(wait=True)

    dt = time.perf_counter() - t_start

    with open(out_dir / "timing.csv", "a", newline="", encoding="utf-8") as f:
        if f.tell() == 0:
            csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"]).writeheader()
        w = csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"])
        for row in timing_rows:
            w.writerow(row)

    run_meta = {
        "limit": args.limit,
        "concurrency_classify": args.concurrency_classify,
        "concurrency_meta": args.concurrency_meta,
        "concurrency_entities": args.concurrency_entities,
        "random_sample": args.random,
        "seed": args.seed,
        "skip_junk": not args.no_skip_junk,
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
  concurrency: classify={args.concurrency_classify} meta={args.concurrency_meta} entities={args.concurrency_entities}

WALL: {dt:.1f}s ({dt/3600:.2f} h)  → {n}/wall = {n/(dt or 1)*3600:.0f} URL/h

LICZNIKI:
  classify ok={counters['classify_ok']} fail={counters['classify_fail']}  (junk={counters['junk']})
  meta     ok={counters['meta_ok']} fail={counters['meta_fail']}
  entities ok={counters['entities_ok']} fail={counters['entities_fail']}
  final    ok={counters['final_ok']} fail={counters['final_fail']}

JUNK%: {counters['junk']/max(counters['classify_ok'],1)*100:.2f}%
"""
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    log_run.info(summary)
    log_run.info(f"DONE  wall={dt:.1f}s  out_dir={out_dir}")


if __name__ == "__main__":
    main()
