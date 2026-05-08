"""Three-step v3 — wzorzec A (single pool + 3 priority queues).

Różnice vs v2:
- ZAMIAST 3 osobnych ThreadPoolExecutor (1+3+4=8 stałe sloty per faza)
  używamy JEDNEGO `ThreadPoolExecutor(max_workers=N)` (default 6, dopasowane do
  Spark — vLLM dławi się na 8) i 3 kolejek `queue.Queue`.
- Każdy worker w pętli wybiera task z najwyższego priorytetu, pulling order:
    classify → meta → entities
  Po opróżnieniu classify queue, wszystkie 6 workerów przechodzą na meta+entities.
  Brak idle slotów — pełne wysycenie GPU do końca runu.
- Klasyczny retry-with-feedback dla meta/entities już jest w `vllm_client.chat_json`
  (`max_retries_quality=2`, auto-reduce temp + bump max_tokens). Dla classifier'a
  dodano network-retry w `call_junk_classifier_binary` (v2 lib update).

Reuse: `lib/pipeline_threestep_v2.py` (process_classify_v2, process_meta_v2,
process_entities_v2, join_final_v2, make_junk_stub_final_v2). Nie modyfikujemy
istniejących plików v2.

Użycie:
    python3 scripts/run_threestep_v3.py --limit 500 --random --tag v3_500
    python3 scripts/run_threestep_v3.py --limit 500 --random --concurrency 6 --tag v3_500_c6
    python3 scripts/run_threestep_v3.py --resume final_results/<ts>__threestep_v3_<tag>
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
logger = logging.getLogger("threestep_v3")


def _make_phase_logger(name: str, log_path: Path) -> logging.Logger:
    lg = logging.getLogger(f"threestep_v3.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = True
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(fh)
    return lg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=6,
                    help="Liczba workerów w jednym pool (Spark dławi się na 8 → default 6)")
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
        suffix = f"__threestep_v3_{args.tag}" if args.tag else "__threestep_v3"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"out_dir = {out_dir}")

    log_classify = _make_phase_logger("classify", out_dir / "classify.log")
    log_meta = _make_phase_logger("meta", out_dir / "meta.log")
    log_entities = _make_phase_logger("entities", out_dir / "entities.log")
    log_run = _make_phase_logger("run", out_dir / "run.log")
    log_run.info(f"START out_dir={out_dir}  concurrency={args.concurrency}")

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

    # Cache classify (resume)
    classify_cache: dict[str, dict] = {}
    if done_classify:
        for line in open(out_dir / "classified.jsonl", encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("ok") and r.get("url_hash"):
                    classify_cache[r["url_hash"]] = r
            except json.JSONDecodeError:
                continue

    # Stan: per url_hash { article, classify, meta, entities, is_junk_short_circuit }
    state: dict[str, dict] = {}
    state_lock = threading.Lock()

    counters = {"classify_ok": 0, "classify_fail": 0, "junk": 0,
                "meta_ok": 0, "meta_fail": 0,
                "entities_ok": 0, "entities_fail": 0,
                "final_ok": 0, "final_fail": 0,
                "pending": 0}  # liczba aktywnych URL (jeszcze nie zfinalizowanych)
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

    # 3 kolejki
    q_classify: queue.Queue = queue.Queue()
    q_meta: queue.Queue = queue.Queue()
    q_entities: queue.Queue = queue.Queue()

    producer_done = threading.Event()
    shutdown = threading.Event()

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
        bump("pending", -1)

    def fan_out_after_classify(article: dict, classify_rec: dict):
        is_junk = classify_rec.get("is_junk", False)
        if is_junk and not args.no_skip_junk:
            bump("junk")
            stub = make_junk_stub_final_v2(article, classify_rec)
            rep_final.append(stub)
            bump("final_ok")
            with state_lock:
                state[article["url_hash"]] = {"is_junk_short_circuit": True}
            bump("pending", -1)
            return
        with state_lock:
            state[article["url_hash"]] = {"article": article, "classify": classify_rec}
        # meta i entities — wrzuć do ich kolejek (lub oznacz jako already-done przy resume)
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
        # po fan-out możemy mieć 0 zadań do dorzucenia (resume) — sprawdź finalize
        try_finalize(article["url_hash"])

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

    def worker():
        """Wzorzec A — naprawiony: classify ma priorytet, meta+entities równo (round-robin).

        Bug v3 c6: priorytet meta > entities sprawiał, że workery nigdy nie sięgały po
        entities dopóki q_meta nie było puste. Meta queue była cały czas zasilana z
        classify → entities startowało dopiero po opróżnieniu meta = sekwencyjnie zamiast
        równolegle. Fix: po opróżnieniu q_classify wybierz tę z meta/entities, której
        kolejka jest dłuższa (load-balance). Przy remisie naprzemiennie.
        """
        toggle = [False]  # mutable closure flag dla naprzemiennego wyboru przy remisie
        while True:
            if shutdown.is_set():
                return
            # P1: classify (drain ASAP — szybki)
            try:
                art = q_classify.get_nowait()
                try:
                    handle_classify(art)
                finally:
                    q_classify.task_done()
                continue
            except queue.Empty:
                pass
            # P2: meta vs entities — load-balanced
            qm_size = q_meta.qsize()
            qe_size = q_entities.qsize()
            pick_meta = (qm_size > qe_size) or (qm_size == qe_size and toggle[0] and qm_size > 0)
            if qm_size == 0 and qe_size == 0:
                # obie puste — czekamy na entities z timeoutem (wystarczy jedna blokująca pętla)
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
            if pick_meta:
                try:
                    art = q_meta.get_nowait()
                    try:
                        handle_meta(art)
                    finally:
                        q_meta.task_done()
                    toggle[0] = not toggle[0]
                    continue
                except queue.Empty:
                    pass
            # default: entities
            try:
                art = q_entities.get_nowait()
                try:
                    handle_entities(art)
                finally:
                    q_entities.task_done()
                toggle[0] = not toggle[0]
                continue
            except queue.Empty:
                pass
            # fallback (np. tymczasowa pustka, nowe taski jeszcze nie wpadły)
            time.sleep(0.05)

    t_start = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=args.concurrency, thread_name_prefix="w")
    futures = [pool.submit(worker) for _ in range(args.concurrency)]

    # Producent — wpisuje wszystkie URL na klucze classify queue
    for art in todo:
        bump("pending", 1)
        if art["url_hash"] in done_classify and art["url_hash"] in classify_cache:
            fan_out_after_classify(art, classify_cache[art["url_hash"]])
        else:
            q_classify.put(art)
    producer_done.set()

    # Czekaj na workerów
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
        "pattern": "A_single_pool_priority_queues",
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
  pattern=wzorzec A (single pool {args.concurrency}, priority pull classify>meta>entities)

WALL: {dt:.1f}s ({dt/3600:.2f} h)  → {n}/wall = {n/(dt or 1)*3600:.0f} URL/h  ({dt/n:.2f} s/URL)

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
