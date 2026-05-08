"""SPO v1 orchestrator: classify → entities_spo (two-step bootstrap discovery).

Architektura uproszczona względem four-step:
- Stage 1: classify (junk binary, truncated input, ~0.2-0.5s)
- Stage 2: entities_spo (full text → encje kanoniczne z is_central + SPO triples)

Pomijamy meta i sponsored — interesuje nas tylko junk filter + ekstrakcja
encji kanonicznych z trójkami SPO. Cel: bootstrap distribution predykatów
free-form, bez closed enum (closed vocab v2 dopiero z analizy danych).

Single ThreadPoolExecutor + 2 kolejki priorytetowe:
- classify → najwyższy priorytet (drain ASAP)
- entities_spo → bierze artykuły non-junk po classify

Reuse: lib.spo_pipeline_v1 (process_classify_v2, process_entities_spo,
make_junk_stub_final_spo, join_final_spo).

Użycie:
    python3 scripts/run_spo_v1.py --limit 5 --concurrency 4 --tag spo_smoke
    python3 scripts/run_spo_v1.py --limit 0 --concurrency 8 --tag full_bootstrap
    python3 scripts/run_spo_v1.py --resume final_results/<ts>__spo_v1_<tag>
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import queue
import subprocess
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
from lib.streaming_loader import stream_articles_async  # noqa: E402
from lib.junk_pre_filter import is_definite_url_junk, build_junk_stub  # noqa: E402
from lib.pipeline_fourstep_v1 import process_meta_v2, process_sponsored_v1  # noqa: E402
from lib.prompt_loader import load_schema, load_system_prompt  # noqa: E402
from lib.reporter import JsonlReporter  # noqa: E402
from lib.spo_pipeline_v3 import (  # noqa: E402
    join_final_v3 as join_final_spo,
    make_junk_stub_final_spo,
    process_classify_v2,
    process_entities_spo_v3 as process_entities_spo,
)
from lib.vllm_client import VLLMClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spo_v1")

# Sponsored: niska temp (deterministyczna klasyfikacja) — patrz fourstep_v1.
SAMPLING_SPONSORED = {"temperature": 0.4, "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.0}
MAX_TOKENS_SPONSORED = 256


def _make_phase_logger(name: str, log_path: Path) -> logging.Logger:
    lg = logging.getLogger(f"spo_v1.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = True
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(fh)
    return lg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="0 = wszystkie z websites/")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="Liczba workerów w pool. Default 8 (dla pełnych runów over-night).")
    ap.add_argument("--tag", default="")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--websites", default=str(WEBSITES_DIR))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--no-skip-junk", action="store_true",
                    help="Wymuś entities_spo nawet dla junk URL (debug).")
    ap.add_argument("--no-summary", action="store_true",
                    help="Pomiń auto-call scripts/spo_summary_v1.py po runie.")
    ap.add_argument("--no-streaming", action="store_true",
                    help="Wyłącz streaming loader (default ON dla skalowalności).")
    ap.add_argument("--loader-workers", type=int, default=2,
                    help="Liczba workerów producent-loadera (parsowanie trafilaturą równolegle). Default 2 — wyższe wartości ryzykują heap corruption w lxml 6.x.")
    ap.add_argument("--cache-dir", default=None,
                    help="Katalog cache markdown (default: websites_cache/ w PROJECT_ROOT).")
    args = ap.parse_args()

    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.exists():
            sys.exit(f"--resume: katalog {out_dir} nie istnieje")
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = f"__spo_v1_{args.tag}" if args.tag else "__spo_v1"
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tee stdout+stderr → out_dir/stdout.log (zamiast /tmp). Łapie też tracebacki
    # i wszystko co nie idzie przez logger.
    class _Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s); st.flush()
                except Exception:
                    pass
        def flush(self):
            for st in self.streams:
                try: st.flush()
                except Exception: pass

    _stdout_file = open(out_dir / "stdout.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, _stdout_file)
    sys.stderr = _Tee(sys.__stderr__, _stdout_file)
    # Python logging trzyma referencję do oryginalnego stderr z basicConfig — Tee nie zadziała
    # dla logger.*. Dorzucamy FileHandler do root loggera, łapie wszystkie logger.info/.warning
    # ze wszystkich modułów (lib, scripts).
    _root_fh = logging.FileHandler(out_dir / "stdout.log", mode="a", encoding="utf-8")
    _root_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(_root_fh)
    logger.info(f"out_dir = {out_dir}")

    log_classify = _make_phase_logger("classify", out_dir / "classify.log")
    log_entities = _make_phase_logger("entities_spo", out_dir / "entities_spo.log")
    log_meta = _make_phase_logger("meta", out_dir / "meta.log")
    log_sponsored = _make_phase_logger("sponsored", out_dir / "sponsored.log")
    log_run = _make_phase_logger("run", out_dir / "run.log")
    log_run.info(f"START out_dir={out_dir}  concurrency={args.concurrency}  websites={args.websites}")

    rep_classify = JsonlReporter(out_dir / "classified.jsonl")
    rep_entities = JsonlReporter(out_dir / "entities.jsonl")
    rep_spo = JsonlReporter(out_dir / "spo.jsonl")
    rep_combined = JsonlReporter(out_dir / "entities_spo.jsonl")  # combined, dla resume kompatybilności
    rep_meta = JsonlReporter(out_dir / "meta.jsonl")
    rep_sponsored = JsonlReporter(out_dir / "sponsored.jsonl")
    rep_final = JsonlReporter(out_dir / "final.jsonl")
    spo_raw_path = out_dir / "spo_raw.txt"
    spo_raw_lock = threading.Lock()
    done_classify = rep_classify.load_existing_hashes()
    done_entities = rep_entities.load_existing_hashes()
    done_meta = rep_meta.load_existing_hashes()
    done_sponsored = rep_sponsored.load_existing_hashes()
    done_final = rep_final.load_existing_hashes()

    client = VLLMClient(base_url=VLLM_BASE_URL, model=VLLM_MODEL)
    sys_classify = load_system_prompt("step_junkclassify_v3_system")
    # v3 rich-JSON: spo_entities_v3 (cram entities + rich SPO triples in one call) +
    # spo_schema_v3 (xgrammar enforces structure → 0 parse errors).
    sys_spo = load_system_prompt("spo_entities_v3_system")
    schema_spo = load_schema("spo_schema_v3")
    sys_meta = load_system_prompt("step_meta_v2_system")
    schema_meta = load_schema("schema_meta_v2")
    sys_spon = load_system_prompt("step_sponsored_v2_system")
    schema_spon = load_schema("schema_sponsored_v2")

    # Random sample seed → zapisz do out_dir żeby resume mógł wczytać
    seed_path = out_dir / "sample_seed.txt"
    if args.random and not seed_path.exists():
        seed_path.write_text(str(args.seed), encoding="utf-8")
    elif args.resume and seed_path.exists():
        try:
            args.seed = int(seed_path.read_text(encoding="utf-8").strip())
            logger.info(f"resume: wczytano seed={args.seed} z {seed_path}")
        except Exception:
            pass

    use_streaming = not args.no_streaming
    if use_streaming:
        article_iter = stream_articles_async(
            args.websites,
            limit=args.limit,
            random_sample=args.random,
            seed=args.seed,
            n_loader_workers=args.loader_workers,
            queue_maxsize=200,
            cache_dir=args.cache_dir,
        )
        n_articles_known = None
        logger.info(f"streaming loader: workers={args.loader_workers} cache_dir={args.cache_dir or 'websites_cache (default)'}")
    else:
        articles = load_articles(args.websites, limit=args.limit, random_sample=args.random, seed=args.seed)
        article_iter = articles
        n_articles_known = len(articles)
        logger.info(f"non-streaming load: loaded={n_articles_known}  done_final={len(done_final)}")

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
        "entities_ok": 0, "entities_fail": 0,
        "meta_ok": 0, "meta_fail": 0,
        "sponsored_ok": 0, "sponsored_fail": 0,
        "sponsored_true": 0,
        "final_ok": 0, "final_fail": 0,
        "pending": 0,
        "triples_total": 0,
        "entities_total": 0,
        "central_total": 0,
        "s_unmatched_total": 0,
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

    # q_classify bounded — producer throttluje się gdy workery nie nadążają (RAM stabilny przy 21M URL).
    q_classify: queue.Queue = queue.Queue(maxsize=args.concurrency * 8)
    q_entities: queue.Queue = queue.Queue()
    q_meta: queue.Queue = queue.Queue()
    q_sponsored: queue.Queue = queue.Queue()
    producer_done = threading.Event()
    shutdown = threading.Event()

    def try_finalize(h: str):
        with state_lock:
            s = state.get(h, {})
            if s.get("is_junk_short_circuit"):
                return
            # Wszystkie 4 etapy muszą być rozstrzygnięte (sukces ALBO fail).
            if "article" not in s or "classify" not in s:
                return
            if "entities_spo" not in s or "meta" not in s or "sponsored" not in s:
                return
            article = s["article"]
            classify_rec = s["classify"]
            ent_rec = s["entities_spo"]
            meta_rec = s["meta"]
            spon_rec = s["sponsored"]
            state.pop(h, None)
        final = join_final_spo(article, classify_rec, ent_rec, meta_rec, spon_rec)
        rep_final.append(final)
        bump("final_ok" if final["ok"] else "final_fail")
        if final.get("sponsored"):
            bump("sponsored_true")
        bump("pending", -1)
        if ent_rec and ent_rec.get("ok"):
            bump("entities_total", len(final.get("entities", [])))
            bump("triples_total", len(final.get("triples", [])))
            bump("central_total", final.get("n_central", 0))
            bump("s_unmatched_total", final.get("triples_s_unmatched", 0))

    def fan_out_after_classify(article: dict, classify_rec: dict):
        is_junk = classify_rec.get("is_junk", False)
        if is_junk and not args.no_skip_junk:
            bump("junk")
            stub = make_junk_stub_final_spo(article, classify_rec)
            rep_final.append(stub)
            bump("final_ok")
            with state_lock:
                state[article["url_hash"]] = {"is_junk_short_circuit": True}
            bump("pending", -1)
            return
        h = article["url_hash"]
        with state_lock:
            state[h] = {"article": article, "classify": classify_rec}
        # 3-way fan-out: entities_spo + meta + sponsored (wszystkie niezależne LLM calls).
        if h not in done_entities:
            q_entities.put(article)
        else:
            with state_lock:
                state[h]["entities_spo"] = {"ok": True}
        if h not in done_meta:
            q_meta.put(article)
        else:
            with state_lock:
                state[h]["meta"] = {"ok": True}
        if h not in done_sponsored:
            q_sponsored.put(article)
        else:
            with state_lock:
                state[h]["sponsored"] = {"ok": True}
        try_finalize(h)  # przy resume wszystko może być done

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
                "entities": [], "triples": [],
                "language": "", "category": "", "title": "", "meta_description": "",
                "h1": "", "article_summary": "",
                "sponsored": False, "sponsored_subtype": None, "sponsored_justification": "",
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
            bump("final_fail")
            bump("pending", -1)
            return
        bump("classify_ok")
        log_classify.info(f"OK {article['id']} is_junk={rec.get('is_junk')} lat={rec['latency_s']}s "
                          f"(running ok={counters['classify_ok']} junk={counters['junk']})")
        fan_out_after_classify(article, rec)

    def handle_entities_spo(article):
        # v3 rich-JSON cram budget: 60 ent × ~25 tok + 40 rich triples × ~100 tok +
        # central_entities + primary_topic + envelope ≈ 4400. Bump to 4500 for safety.
        rec = process_entities_spo(client, sys_spo, schema_spo, article,
                                   max_tokens=4500, sampling=SAMPLING_STEP1)
        # Combined (legacy)
        rep_combined.append(rec)
        # Split: entities.jsonl + spo.jsonl — czytelne dla downstream
        ent_rec = {
            "url_hash": rec["url_hash"], "id": rec["id"],
            "ok": rec["ok"], "error": rec["error"],
            "latency_s": rec["latency_s"], "ts": rec["ts"],
            "entities": rec.get("entities", []),
            "n_central": rec.get("n_central", 0),
            "entities_raw_count": rec.get("entities_raw_count", 0),
        }
        spo_rec = {
            "url_hash": rec["url_hash"], "id": rec["id"],
            "ok": rec["ok"], "error": rec["error"],
            "latency_s": rec["latency_s"], "ts": rec["ts"],
            # v3 rich SPO fields preserved verbatim — primary_topic + central_entities +
            # full triple records (subject_type, relation_type, predicate_phrase,
            # object_type, object_kind, evidence_span, confidence).
            "primary_topic": rec.get("primary_topic", ""),
            "central_entities": rec.get("central_entities", []),
            "triples": rec.get("triples", []),
            "triples_raw_count": rec.get("triples_raw_count", 0),
            "triples_s_unmatched": rec.get("triples_s_unmatched", 0),
            "triples_o_unmatched": rec.get("triples_o_unmatched", 0),
        }
        rep_entities.append(ent_rec)
        rep_spo.append(spo_rec)
        # spo_raw.txt — one JSON line per article with the rich triples block
        # (replaces the legacy `s|p|o\n` reconstruction; rich fields can't be flattened).
        if rec.get("ok") and rec.get("triples"):
            raw_obj = {
                "url_hash": rec["url_hash"],
                "id": rec["id"],
                "url": article.get("url"),
                "primary_topic": rec.get("primary_topic", ""),
                "central_entities": rec.get("central_entities", []),
                "triples": rec.get("triples", []),
            }
            with spo_raw_lock, open(spo_raw_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(raw_obj, ensure_ascii=False) + "\n")
        add_timing("entities_spo", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        bump("entities_ok" if rec["ok"] else "entities_fail")
        if not rec["ok"]:
            log_entities.warning(f"FAIL {article['id']}: {rec['error']}")
        else:
            log_entities.info(
                f"OK {article['id']} n_ent={len(rec.get('entities', []))} "
                f"n_central={rec.get('n_central', 0)} n_triples={len(rec.get('triples', []))} "
                f"s_unm={rec.get('triples_s_unmatched', 0)} lat={rec['latency_s']}s "
                f"tok_in={rec['usage'].get('prompt_tokens', 0)} tok_out={rec['usage'].get('completion_tokens', 0)} "
                f"attempts={rec.get('attempts', 1)} "
                f"(running ok={counters['entities_ok']} fail={counters['entities_fail']})"
            )
        with state_lock:
            state.setdefault(article["url_hash"], {})["entities_spo"] = rec
        try_finalize(article["url_hash"])

    def handle_meta(article):
        rec = process_meta_v2(client, sys_meta, schema_meta, article,
                              max_tokens=MAX_TOKENS_STEP2, sampling=SAMPLING_STEP2)
        rep_meta.append(rec)
        add_timing("meta", article["url_hash"], rec["latency_s"], rec["ok"], rec.get("attempts", 1))
        bump("meta_ok" if rec["ok"] else "meta_fail")
        if not rec["ok"]:
            log_meta.warning(f"FAIL {article['id']}: {rec['error']}")
        else:
            log_meta.info(
                f"OK {article['id']} cat={rec.get('category')!r} lang={rec.get('language')!r} "
                f"lat={rec['latency_s']}s "
                f"tok_in={rec['usage'].get('prompt_tokens', 0)} tok_out={rec['usage'].get('completion_tokens', 0)} "
                f"(running ok={counters['meta_ok']} fail={counters['meta_fail']})"
            )
        with state_lock:
            state.setdefault(article["url_hash"], {})["meta"] = rec
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
            log_sponsored.info(
                f"OK {article['id']} sponsored={rec.get('sponsored')} "
                f"subtype={rec.get('sponsored_subtype')!r} "
                f"just={rec.get('sponsored_justification', '')[:80]!r} lat={rec['latency_s']}s "
                f"(running ok={counters['sponsored_ok']} fail={counters['sponsored_fail']})"
            )
        with state_lock:
            state.setdefault(article["url_hash"], {})["sponsored"] = rec
        try_finalize(article["url_hash"])

    def worker():
        """Drain-first: entities_spo + meta + sponsored (longest-queue-first), fallback na classify."""
        toggle = [0]  # round-robin tiebreaker po (entities, meta, sponsored)
        while True:
            if shutdown.is_set():
                return
            # P1: 3 etapy "in-flight" — load-balanced
            sizes = {
                "entities": q_entities.qsize(),
                "meta": q_meta.qsize(),
                "sponsored": q_sponsored.qsize(),
            }
            if any(v > 0 for v in sizes.values()):
                max_size = max(sizes.values())
                cands = [k for k, v in sizes.items() if v == max_size and v > 0]
                if len(cands) == 1:
                    pick = cands[0]
                else:
                    order = ["entities", "meta", "sponsored"]
                    start = toggle[0] % 3
                    pick = next(
                        (order[(start + i) % 3] for i in range(3) if order[(start + i) % 3] in cands),
                        cands[0],
                    )
                    toggle[0] = (toggle[0] + 1) % 3
                qmap = {"entities": q_entities, "meta": q_meta, "sponsored": q_sponsored}
                handler_map = {
                    "entities": handle_entities_spo, "meta": handle_meta, "sponsored": handle_sponsored,
                }
                try:
                    art = qmap[pick].get_nowait()
                    try:
                        handler_map[pick](art)
                    finally:
                        qmap[pick].task_done()
                    continue
                except queue.Empty:
                    pass  # race, spadnij niżej do classify
            # P2: classify (nowy materiał)
            try:
                art = q_classify.get(timeout=0.1)
                try:
                    handle_classify(art)
                finally:
                    q_classify.task_done()
                continue
            except queue.Empty:
                if producer_done.is_set():
                    with cnt_lock:
                        if counters["pending"] == 0:
                            return
                continue

    t_start = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=args.concurrency, thread_name_prefix="w")
    futures = [pool.submit(worker) for _ in range(args.concurrency)]

    n_seen = 0
    n_skipped_done = 0
    n_pre_filter_junk = 0
    for art in article_iter:
        n_seen += 1
        if art["url_hash"] in done_final:
            n_skipped_done += 1
            continue
        bump("pending", 1)
        # Pre-classifier deterministic regex — łapie tag/author/page/search URLs bez LLM
        is_pre_junk, pre_reason = is_definite_url_junk(
            url=art.get("url"), path=art.get("path"), query=None,
        )
        if is_pre_junk and not args.no_skip_junk and art["url_hash"] not in done_classify:
            stub = build_junk_stub(art, reason=pre_reason)
            rep_classify.append(stub)
            bump("classify_ok")
            n_pre_filter_junk += 1
            log_classify.info(f"PRE-FILTER junk {art['id']} reason={pre_reason} url={art.get('url','')[:80]}")
            fan_out_after_classify(art, stub)
            continue
        if art["url_hash"] in done_classify and art["url_hash"] in classify_cache:
            fan_out_after_classify(art, classify_cache[art["url_hash"]])
        else:
            q_classify.put(art)
    producer_done.set()
    if use_streaming:
        try:
            logger.info(f"loader stats: {article_iter.stats.as_dict()}")
        except Exception:
            pass
    logger.info(f"producer done: seen={n_seen} skipped_already_done={n_skipped_done} pre_filter_junk={n_pre_filter_junk}")

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

    loader_stats = {}
    if use_streaming:
        try:
            loader_stats = article_iter.stats.as_dict()
        except Exception:
            pass
    run_meta = {
        "pipeline": "spo_v1_full",
        "limit": args.limit,
        "concurrency": args.concurrency,
        "pattern": "classify_then_4way_entities_spo+meta+sponsored",
        "random_sample": args.random,
        "seed": args.seed,
        "skip_junk": not args.no_skip_junk,
        "websites": args.websites,
        "wall_s": round(dt, 1),
        "started_at": datetime.fromtimestamp(time.time() - dt).isoformat(timespec="seconds"),
        "ended_at": datetime.now().isoformat(timespec="seconds"),
        "counters": counters,
        "n_articles_seen": n_seen,
        "n_skipped_already_done": n_skipped_done,
        "n_pre_filter_junk": n_pre_filter_junk,
        "classifier_prompt": "step_junkclassify_v3_system",
        "use_streaming": use_streaming,
        "loader_workers": args.loader_workers if use_streaming else None,
        "cache_dir": args.cache_dir,
        "loader_stats": loader_stats,
    }
    with open(out_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    n = (n_seen - n_skipped_done) or 1
    classify_ok = counters['classify_ok'] or 1
    summary = f"""=== {out_dir.name} ===
  pipeline=spo_v1_full (classify → entities_spo + meta + sponsored, 4-way fan-out)
  limit={args.limit}  random={args.random}  seed={args.seed}  skip_junk={not args.no_skip_junk}
  concurrency={args.concurrency}
  websites={args.websites}

WALL: {dt:.1f}s ({dt/3600:.2f} h)  → {n}/wall = {n/(dt or 1)*3600:.0f} URL/h  ({dt/n:.2f} s/URL)

LICZNIKI:
  classify     ok={counters['classify_ok']} fail={counters['classify_fail']}  (junk={counters['junk']})
  entities_spo ok={counters['entities_ok']} fail={counters['entities_fail']}
  meta         ok={counters['meta_ok']} fail={counters['meta_fail']}
  sponsored    ok={counters['sponsored_ok']} fail={counters['sponsored_fail']}  (sponsored_true={counters['sponsored_true']})
  final        ok={counters['final_ok']} fail={counters['final_fail']}

JUNK%:                {counters['junk']/classify_ok*100:.2f}%
SPONSORED%:           {counters['sponsored_true']/max(counters['sponsored_ok'],1)*100:.2f}% (z non-junk OK)
ENTITIES total:       {counters['entities_total']}
CENTRAL total:        {counters['central_total']}  (avg/article = {counters['central_total']/max(counters['entities_ok'],1):.2f})
TRIPLES total:        {counters['triples_total']}  (avg/article = {counters['triples_total']/max(counters['entities_ok'],1):.2f})
TRIPLES s unmatched:  {counters['s_unmatched_total']}  ({counters['s_unmatched_total']/max(counters['triples_total'],1)*100:.2f}% z triples)
"""
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    log_run.info(summary)
    log_run.info(f"DONE  wall={dt:.1f}s  out_dir={out_dir}")

    # Auto-summary (analiza predicates etc.) — może dawać błędy, nie crashujemy runu
    if not args.no_summary:
        try:
            log_run.info(f"Generating SPO summary → {out_dir}/SUMMARY.md")
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "spo_summary_v1.py"), "--out-dir", str(out_dir)],
                check=False, timeout=300,
            )
        except Exception as e:
            log_run.warning(f"spo_summary_v1 failed: {e}")


if __name__ == "__main__":
    main()
