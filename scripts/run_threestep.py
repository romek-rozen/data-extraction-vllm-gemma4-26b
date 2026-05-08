"""Three-step orchestrator (D7c): classify → (meta || entities) z kolejkami.

Każdy etap to osobny ThreadPoolExecutor czytający z queue.Queue. Junk wykryty przez
classifier idzie krótką ścieżką do final.jsonl bez wywołań meta/entities.

Idempotencja per faza po url_hash (pliki classified.jsonl / meta.jsonl /
entities.jsonl / final.jsonl). Re-użycie:
- prompts/step_classify_system.md + prompts/schema_classify.json (NEW, Step 0)
- prompts/step1_system_v6.md + prompts/schema_step1_v6.json (entities, ignorujemy
  zwrócone category/language — bierzemy z classifier'a)
- prompts/step2_system.md + prompts/schema_step2.json (meta SEO)

Użycie:
    python3 scripts/run_threestep.py --limit 500 --random --tag p1_500
    python3 scripts/run_threestep.py --resume final_results/<ts>__threestep_p1_500
    python3 scripts/run_threestep.py --limit 500 --random --no-skip-junk  # debug: nie pomijaj junk
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
from lib.pipeline_threestep import (  # noqa: E402
    join_final,
    make_junk_stub_final,
    process_classify,
    process_step1,
    process_step2,
)
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("threestep")

# Lekki classifier: krótki output, niska temperatura wystarczy (deterministyczna klasyfikacja).
SAMPLING_CLASSIFY = {"temperature": 0.5, "top_p": 0.95, "top_k": 64, "repetition_penalty": 1.0}
MAX_TOKENS_CLASSIFY = 64  # output to ~20 tok ({"language":"pl","category":"X"}); bufor 64 daje powietrze


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--concurrency-classify", type=int, default=4)
    ap.add_argument("--concurrency-meta", type=int, default=3)
    ap.add_argument("--concurrency-entities", type=int, default=3)
    ap.add_argument("--tag", default="")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--websites", default=str(WEBSITES_DIR))
    ap.add_argument("--out-dir", default=None, help="Override output dir; default final_results/<ts>__threestep_<tag>")
    ap.add_argument("--resume", default=None, help="Resume z istniejącego katalogu (URL z OK są pomijane per faza)")
    ap.add_argument("--no-skip-junk", action="store_true",
                    help="Nie pomijaj junk — meta+entities lecą i tak (sanity check)")
    args = ap.parse_args()

    # Output dir
    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.exists():
            sys.exit(f"--resume: katalog {out_dir} nie istnieje")
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = f"__threestep_{args.tag}" if args.tag else "__threestep"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"out_dir = {out_dir}")

    # Reportery (idempotencja per faza po url_hash, only_ok=True)
    rep_classify = JsonlReporter(out_dir / "classified.jsonl")
    rep_entities = JsonlReporter(out_dir / "entities.jsonl")
    rep_meta = JsonlReporter(out_dir / "meta.jsonl")
    rep_final = JsonlReporter(out_dir / "final.jsonl")
    done_classify = rep_classify.load_existing_hashes()
    done_entities = rep_entities.load_existing_hashes()
    done_meta = rep_meta.load_existing_hashes()
    done_final = rep_final.load_existing_hashes()

    # Klient + prompty
    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    sys_classify = load_system_prompt("step_classify_system")
    schema_classify = load_schema("schema_classify")
    sys_step1 = load_system_prompt("step1_system_v6")
    schema_step1 = load_schema("schema_step1_v6")
    sys_step2 = load_system_prompt("step2_system")
    schema_step2 = load_schema("schema_step2")

    # Articles
    articles = load_articles(args.websites, limit=args.limit, random_sample=args.random, seed=args.seed)
    todo = [a for a in articles if a["url_hash"] not in done_final]
    logger.info(f"loaded={len(articles)}  todo={len(todo)}  done_final={len(done_final)}")

    # Stan: dla każdego URL śledzimy classify_record + entities_record + meta_record
    state: dict[str, dict] = {}
    state_lock = threading.Lock()

    # Liczniki
    counters = {"classify_ok": 0, "classify_fail": 0, "junk": 0,
                "entities_ok": 0, "entities_fail": 0,
                "meta_ok": 0, "meta_fail": 0,
                "final_ok": 0, "final_fail": 0}
    cnt_lock = threading.Lock()

    # Timing per request (do timing.csv)
    timing_rows: list[dict] = []
    timing_lock = threading.Lock()

    def bump(key: str, delta: int = 1):
        with cnt_lock:
            counters[key] += delta

    def add_timing(phase: str, url_hash: str, latency: float, ok: bool, attempts: int):
        with timing_lock:
            timing_rows.append({
                "phase": phase, "url_hash": url_hash, "latency_s": round(latency, 3),
                "ok": ok, "attempts": attempts,
            })

    # Kolejki
    q_classify: queue.Queue = queue.Queue()
    q_entities: queue.Queue = queue.Queue()
    q_meta: queue.Queue = queue.Queue()

    # Sentinele dla każdego pool
    SENTINEL = None

    def try_finalize(url_hash: str):
        """Jeśli zarówno entities jak i meta są obecne w state → join + write final."""
        with state_lock:
            s = state.get(url_hash, {})
            if "classify" not in s or "article" not in s:
                return
            if s.get("is_junk_short_circuit"):
                return  # już zapisany jako junk-stub
            if "entities" not in s or "meta" not in s:
                return
            article = s["article"]
            classify_rec = s["classify"]
            ent_rec = s["entities"]
            meta_rec = s["meta"]
            # zwolnij pamięć
            state.pop(url_hash, None)
        final = join_final(article, classify_rec, ent_rec, meta_rec)
        rep_final.append(final)
        bump("final_ok" if final["ok"] else "final_fail")

    def classify_worker():
        while True:
            item = q_classify.get()
            if item is SENTINEL:
                q_classify.task_done()
                return
            article = item
            try:
                rec = process_classify(client, sys_classify, schema_classify, article,
                                       max_tokens=MAX_TOKENS_CLASSIFY, sampling=SAMPLING_CLASSIFY)
                rep_classify.append(rec)
                add_timing("classify", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
                if not rec["ok"]:
                    bump("classify_fail")
                    logger.warning(f"CLASSIFY FAIL {article['id']}: {rec['error']}")
                    # bez classify nie ma sensu robić meta/entities — zapisujemy fail final
                    final_fail = {
                        "url_hash": article["url_hash"], "id": article["id"], "url": article["url"],
                        "ok": False, "error": "classify_failed", "is_junk": False,
                        "ts": datetime.now().isoformat(timespec="seconds"),
                    }
                    rep_final.append(final_fail)
                    bump("final_fail")
                    continue
                bump("classify_ok")
                is_junk = rec.get("is_junk", False)
                if is_junk and not args.no_skip_junk:
                    bump("junk")
                    stub = make_junk_stub_final(article, rec)
                    rep_final.append(stub)
                    bump("final_ok")
                    with state_lock:
                        state[article["url_hash"]] = {"is_junk_short_circuit": True}
                    continue
                # non-junk (lub --no-skip-junk) → fan-out do meta + entities
                with state_lock:
                    state[article["url_hash"]] = {"article": article, "classify": rec}
                if article["url_hash"] not in done_entities:
                    q_entities.put((article, rec))
                else:
                    # już mamy z poprzedniego runa — wczytaj
                    pass
                if article["url_hash"] not in done_meta:
                    q_meta.put((article, rec))
            finally:
                q_classify.task_done()

    def entities_worker():
        while True:
            item = q_entities.get()
            if item is SENTINEL:
                q_entities.task_done()
                return
            article, classify_rec = item
            try:
                rec = process_step1(client, sys_step1, schema_step1, article,
                                    max_tokens=MAX_TOKENS_STEP1, sampling=SAMPLING_STEP1)
                # entities-only zapis: zachowujemy `entities` z rec, reszta to bookkeeping
                ent_rec = {
                    "url_hash": rec["url_hash"], "id": rec["id"], "ok": rec["ok"], "error": rec["error"],
                    "latency_s": rec["latency_s"], "usage": rec["usage"],
                    "finish_reason": rec.get("finish_reason"), "attempts": rec.get("attempts", 1),
                    "ts": rec["ts"],
                    "entities": rec.get("entities", []),
                    "entities_raw_count": rec.get("entities_raw_count", 0),
                }
                rep_entities.append(ent_rec)
                add_timing("entities", article["url_hash"], ent_rec["latency_s"], ent_rec["ok"], ent_rec.get("attempts", 1))
                bump("entities_ok" if ent_rec["ok"] else "entities_fail")
                if not ent_rec["ok"]:
                    logger.warning(f"ENTITIES FAIL {article['id']}: {ent_rec['error']}")
                with state_lock:
                    state.setdefault(article["url_hash"], {})["entities"] = ent_rec
                try_finalize(article["url_hash"])
            finally:
                q_entities.task_done()

    def meta_worker():
        while True:
            item = q_meta.get()
            if item is SENTINEL:
                q_meta.task_done()
                return
            article, classify_rec = item
            try:
                # process_step2 wymaga entity_record z polami {ok, language, category, entities}.
                # Mamy classify_rec — encje pójdą puste (meta-prompt korzysta z encji jako wskazówek;
                # przy three-step entities lecą równolegle, więc nie czekamy na nie).
                pseudo_entity_rec = {
                    "ok": True,
                    "language": classify_rec.get("language") or "en",
                    "category": classify_rec.get("category") or "Other themes",
                    "entities": [],  # bez wskazówek encji — tradeoff za parallelism
                }
                rec = process_step2(client, sys_step2, schema_step2, article, pseudo_entity_rec,
                                    max_tokens=MAX_TOKENS_STEP2, sampling=SAMPLING_STEP2)
                meta_rec = {
                    "url_hash": rec["url_hash"], "id": rec["id"], "ok": rec["ok"], "error": rec["error"],
                    "latency_s": rec["latency_s"], "usage": rec["usage"],
                    "finish_reason": rec.get("finish_reason"), "attempts": rec.get("attempts", 1),
                    "ts": rec["ts"],
                    "title": rec.get("title", ""),
                    "meta_description": rec.get("meta_description", ""),
                    "h1": rec.get("h1", ""),
                    "article_summary": rec.get("article_summary", ""),
                }
                rep_meta.append(meta_rec)
                add_timing("meta", article["url_hash"], meta_rec["latency_s"], meta_rec["ok"], meta_rec.get("attempts", 1))
                bump("meta_ok" if meta_rec["ok"] else "meta_fail")
                if not meta_rec["ok"]:
                    logger.warning(f"META FAIL {article['id']}: {meta_rec['error']}")
                with state_lock:
                    state.setdefault(article["url_hash"], {})["meta"] = meta_rec
                try_finalize(article["url_hash"])
            finally:
                q_meta.task_done()

    # Start pulli
    t_start = time.perf_counter()
    pool_classify = ThreadPoolExecutor(max_workers=args.concurrency_classify, thread_name_prefix="classify")
    pool_entities = ThreadPoolExecutor(max_workers=args.concurrency_entities, thread_name_prefix="entities")
    pool_meta = ThreadPoolExecutor(max_workers=args.concurrency_meta, thread_name_prefix="meta")
    for _ in range(args.concurrency_classify):
        pool_classify.submit(classify_worker)
    for _ in range(args.concurrency_entities):
        pool_entities.submit(entities_worker)
    for _ in range(args.concurrency_meta):
        pool_meta.submit(meta_worker)

    # Producent — articles do classify
    for art in todo:
        if art["url_hash"] in done_classify:
            # classify już mamy z poprzedniego runa — odzyskaj rekord i fan-out bezpośrednio
            with open(out_dir / "classified.jsonl", encoding="utf-8") as f:
                cached = None
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get("url_hash") == art["url_hash"] and r.get("ok"):
                            cached = r
                    except json.JSONDecodeError:
                        continue
            if cached is None:
                q_classify.put(art)
                continue
            # Re-route z cache
            if cached.get("is_junk") and not args.no_skip_junk:
                if art["url_hash"] not in done_final:
                    rep_final.append(make_junk_stub_final(art, cached))
                    bump("final_ok")
                continue
            with state_lock:
                state[art["url_hash"]] = {"article": art, "classify": cached}
            if art["url_hash"] not in done_entities:
                q_entities.put((art, cached))
            else:
                with state_lock:
                    state[art["url_hash"]]["entities"] = {"ok": True, "entities": []}  # placeholder; final z entities z cache trzeba doczytać
            if art["url_hash"] not in done_meta:
                q_meta.put((art, cached))
            else:
                with state_lock:
                    state[art["url_hash"]]["meta"] = {"ok": True}
        else:
            q_classify.put(art)

    # Sygnalizacja końca classify
    q_classify.join()
    for _ in range(args.concurrency_classify):
        q_classify.put(SENTINEL)
    pool_classify.shutdown(wait=True)

    # Po classify wiemy ile rekordów trafiło do meta/entities — czekamy aż się skończą
    q_entities.join()
    q_meta.join()
    for _ in range(args.concurrency_entities):
        q_entities.put(SENTINEL)
    for _ in range(args.concurrency_meta):
        q_meta.put(SENTINEL)
    pool_entities.shutdown(wait=True)
    pool_meta.shutdown(wait=True)

    dt = time.perf_counter() - t_start

    # timing.csv
    with open(out_dir / "timing.csv", "a", newline="", encoding="utf-8") as f:
        if f.tell() == 0:
            csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"]).writeheader()
        w = csv.DictWriter(f, fieldnames=["phase", "url_hash", "latency_s", "ok", "attempts"])
        for row in timing_rows:
            w.writerow(row)

    # run_meta.json
    run_meta = {
        "run_dir": str(out_dir.relative_to(out_dir.parent.parent) if out_dir.is_relative_to(out_dir.parent.parent) else out_dir),
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

    # summary.txt
    n = len(todo) or 1
    summary = f"""=== {out_dir.name} ===
  limit={args.limit}  random={args.random}  seed={args.seed}  skip_junk={not args.no_skip_junk}
  concurrency: classify={args.concurrency_classify} meta={args.concurrency_meta} entities={args.concurrency_entities}

WALL: {dt:.1f}s ({dt/3600:.2f} h)  → {n}/wall = {n/(dt or 1)*3600:.0f} URL/h

LICZNIKI:
  classify ok={counters['classify_ok']} fail={counters['classify_fail']}  (junk={counters['junk']})
  entities ok={counters['entities_ok']} fail={counters['entities_fail']}
  meta     ok={counters['meta_ok']} fail={counters['meta_fail']}
  final    ok={counters['final_ok']} fail={counters['final_fail']}

JUNK%: {counters['junk']/max(counters['classify_ok'],1)*100:.2f}%
"""
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    logger.info(summary)
    logger.info(f"DONE  wall={dt:.1f}s  out_dir={out_dir}")


if __name__ == "__main__":
    main()
