"""Porównanie: one-step vs two-step na tym samym zbiorze URL.

Mierzy:
- prędkość: total wall time, mean/p50/p95 latency per URL, output tokens, attempts
- jakość: zgodność language, zgodność category, jaccard encji (name+type),
  obecność i długości pól SEO (title/meta_desc/h1/article_summary), success rate.

Output:
- final_results/<ts>__compare_onestep/{onestep.jsonl, entity_layer.jsonl, final.jsonl, report.md}

Uwagi:
- Domyślnie limit=20 (małe próbki — bezpieczne pierwsze podejście).
- Skrypt NIE startuje vLLM. Wymaga działającego serwera (scripts/start_vllm.sh).
- Idempotentny: przy --resume <dir> wznawia istniejący run.
- Można odpalić tylko jedną z dwóch ścieżek przez --only onestep / --only twostep
  (przydatne, gdy chcemy mierzyć wpływ prefix-cache: jedną ścieżkę kasujemy i
  uruchamiamy ponownie). Default: obie.

Użycie:
    python3 scripts/compare_onestep_vs_twostep.py --limit 20 --concurrency 4
    python3 scripts/compare_onestep_vs_twostep.py --limit 20 --random --seed 7 --tag rand7
    python3 scripts/compare_onestep_vs_twostep.py --limit 50 --concurrency 8 --tag baseline
    python3 scripts/compare_onestep_vs_twostep.py --resume final_results/2026-05-07_18-00-00__compare_onestep
    python3 scripts/compare_onestep_vs_twostep.py --limit 20 --only onestep

Sampling:
    --random + --seed N → losuje N URL-i z całego websites/, REPRODUCIBLE
    (ten sam seed = ten sam zestaw). Seed jest zapisywany w compare_meta.json,
    więc --resume i --analyze-only odtwarzają go automatycznie. One-step i
    two-step zawsze używają TEGO SAMEGO seeda — porównanie na identycznych URL.
"""

import argparse
import json
import logging
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from lib.config import DEFAULT_CONCURRENCY, FINAL_RESULT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("compare")


# ---------- helpers ----------

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _dedup_last(records: list[dict]) -> list[dict]:
    """Zostaw OSTATNI rekord per url_hash (zgodnie z reporter.load_records)."""
    by_hash: dict[str, dict] = {}
    for r in records:
        h = r.get("url_hash")
        if h:
            by_hash[h] = r
    return list(by_hash.values())


def _stat(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "sum": 0.0}
    s = sorted(values)
    n = len(s)
    p = lambda q: s[min(int(q * n), n - 1)]
    return {
        "n": n,
        "mean": round(statistics.fmean(s), 3),
        "p50": round(p(0.50), 3),
        "p95": round(p(0.95), 3),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
        "sum": round(sum(s), 3),
    }


def _entity_set(rec: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for e in rec.get("entities") or []:
        name = (e.get("name") or "").strip().lower()
        typ = e.get("type") or ""
        if name:
            out.add((name, typ))
    return out


def _len_or_none(s) -> int | None:
    return len(s) if isinstance(s, str) else None


def _usage_total_out(rec: dict) -> int:
    u = rec.get("usage") or {}
    return int(u.get("completion_tokens", 0) or 0)


def _usage_total_in(rec: dict) -> int:
    u = rec.get("usage") or {}
    return int(u.get("prompt_tokens", 0) or 0)


# ---------- runners ----------

def _count_ok(path: Path) -> int:
    """Liczba rekordów ok=True w JSONL (po dedupie po url_hash — ostatni wygrywa)."""
    if not path.exists():
        return 0
    by_hash: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = rec.get("url_hash")
            if h:
                by_hash[h] = rec
    return sum(1 for r in by_hash.values() if r.get("ok"))


def _record_segment(
    meta_path: Path,
    phase: str,
    started_at: str,
    ended_at: str,
    wall_s: float,
    n_records_before: int,
    n_records_after: int,
    rc: int,
) -> None:
    """Append wpis o segmencie wykonania do meta["history"] (atomowo)."""
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            meta = {}
    history = meta.setdefault("history", [])
    history.append({
        "phase": phase,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_s": round(wall_s, 2),
        "ok_records_before": n_records_before,
        "ok_records_after": n_records_after,
        "ok_processed_in_segment": max(0, n_records_after - n_records_before),
        "rc": rc,
    })
    meta_path.write_text(json.dumps(meta, indent=2))


def _total_wall_from_history(meta: dict, phase: str) -> float:
    """Suma wall_s wszystkich segmentów dla danej fazy."""
    return sum(
        float(h.get("wall_s", 0) or 0)
        for h in (meta.get("history") or [])
        if h.get("phase") == phase
    )


def _segments_count(meta: dict, phase: str) -> int:
    return sum(1 for h in (meta.get("history") or []) if h.get("phase") == phase)


def step(name: str, cmd: list[str], log_file: Path) -> tuple[int, float]:
    logger.info(f"=== {name} ===")
    logger.info("RUN: " + " ".join(cmd))
    t0 = time.perf_counter()
    # log file: 'a' żeby nie nadpisywać przy resume
    with open(log_file, "a") as f:
        f.write(f"\n=== Run started at {datetime.now().isoformat()} ===\n")
        f.flush()
        rc = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
    dt = time.perf_counter() - t0
    logger.info(f"{name} done in {dt:.1f}s (rc={rc})")
    return rc, dt


def run_twostep(out_dir: Path, limit: int, concurrency: int, no_skip: bool,
                random_sample: bool, seed: int, meta_path: Path) -> float:
    """Uruchom two-step pipeline. Wall time tego segmentu zapisany do meta['history']."""
    log = out_dir / "twostep.log"
    final_jsonl = out_dir / "final.jsonl"
    n_before = _count_ok(final_jsonl)
    started = datetime.now().isoformat(timespec="seconds")
    cmd = ["python3", "-u", "scripts/run_pipeline.py",
           "--limit", str(limit),
           "--concurrency", str(concurrency),
           "--out-dir", str(out_dir)]
    if random_sample:
        cmd += ["--random", "--seed", str(seed)]
    if no_skip:
        cmd.append("--no-skip")
    rc, dt = step("Two-step pipeline", cmd, log)
    ended = datetime.now().isoformat(timespec="seconds")
    n_after = _count_ok(final_jsonl)
    _record_segment(meta_path, "twostep", started, ended, dt, n_before, n_after, rc)
    if rc != 0:
        logger.error(f"Two-step FAILED — patrz {log}")
    return dt


def run_onestep(out_dir: Path, limit: int, concurrency: int, no_skip: bool,
                random_sample: bool, seed: int, meta_path: Path) -> float:
    log = out_dir / "onestep.log"
    onestep_jsonl = out_dir / "onestep.jsonl"
    n_before = _count_ok(onestep_jsonl)
    started = datetime.now().isoformat(timespec="seconds")
    cmd = ["python3", "-u", "scripts/run_onestep.py",
           "--limit", str(limit),
           "--concurrency", str(concurrency),
           "--out", str(onestep_jsonl)]
    if random_sample:
        cmd += ["--random", "--seed", str(seed)]
    if no_skip:
        cmd.append("--no-skip")
    rc, dt = step("One-step pipeline", cmd, log)
    ended = datetime.now().isoformat(timespec="seconds")
    n_after = _count_ok(onestep_jsonl)
    _record_segment(meta_path, "onestep", started, ended, dt, n_before, n_after, rc)
    if rc != 0:
        logger.error(f"One-step FAILED — patrz {log}")
    return dt


# ---------- analysis ----------

def _load_meta(out_dir: Path) -> dict:
    p = out_dir / "compare_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def analyze(out_dir: Path, twostep_wall: float, onestep_wall: float,
            random_sample: bool = False, seed: int = 42) -> Path:
    onestep = _dedup_last(_read_jsonl(out_dir / "onestep.jsonl"))
    twostep_step1 = _dedup_last(_read_jsonl(out_dir / "entity_layer.jsonl"))
    twostep_step2 = _dedup_last(_read_jsonl(out_dir / "final.jsonl"))

    twostep_step1_by_hash = {r["url_hash"]: r for r in twostep_step1}
    twostep_step2_by_hash = {r["url_hash"]: r for r in twostep_step2}
    onestep_by_hash = {r["url_hash"]: r for r in onestep}

    common = sorted(set(onestep_by_hash) & set(twostep_step2_by_hash))

    # ---- speed ----
    onestep_lat = [r["latency_s"] for r in onestep if r.get("ok")]
    step1_lat = [r["latency_s"] for r in twostep_step1 if r.get("ok")]
    step2_lat = [r["latency_s"] for r in twostep_step2 if r.get("ok")]
    twostep_combined_lat: list[float] = []
    for h in common:
        s1 = twostep_step1_by_hash[h]
        s2 = twostep_step2_by_hash[h]
        if s1.get("ok") and s2.get("ok"):
            twostep_combined_lat.append(float(s1["latency_s"]) + float(s2["latency_s"]))

    onestep_out_tok = [_usage_total_out(r) for r in onestep if r.get("ok")]
    onestep_in_tok = [_usage_total_in(r) for r in onestep if r.get("ok")]
    step1_out_tok = [_usage_total_out(r) for r in twostep_step1 if r.get("ok")]
    step1_in_tok = [_usage_total_in(r) for r in twostep_step1 if r.get("ok")]
    step2_out_tok = [_usage_total_out(r) for r in twostep_step2 if r.get("ok")]
    step2_in_tok = [_usage_total_in(r) for r in twostep_step2 if r.get("ok")]

    onestep_ok = sum(1 for r in onestep if r.get("ok"))
    onestep_fail = len(onestep) - onestep_ok
    step1_ok = sum(1 for r in twostep_step1 if r.get("ok"))
    step1_fail = len(twostep_step1) - step1_ok
    step2_ok = sum(1 for r in twostep_step2 if r.get("ok"))
    step2_fail = len(twostep_step2) - step2_ok

    # ---- quality (na common subset) ----
    n = len(common)
    lang_match = 0
    cat_match = 0
    jaccard_vals: list[float] = []
    intersect_vals: list[int] = []
    onestep_ent_counts: list[int] = []
    twostep_ent_counts: list[int] = []
    title_lens_one: list[int] = []
    title_lens_two: list[int] = []
    md_lens_one: list[int] = []
    md_lens_two: list[int] = []
    h1_lens_one: list[int] = []
    h1_lens_two: list[int] = []
    sum_lens_one: list[int] = []
    sum_lens_two: list[int] = []
    missing_meta_one = 0
    missing_meta_two = 0

    for h in common:
        one = onestep_by_hash[h]
        two = twostep_step2_by_hash[h]
        if not (one.get("ok") and two.get("ok")):
            continue

        if (one.get("language") or "") == (two.get("language") or ""):
            lang_match += 1
        if (one.get("category") or "") == (two.get("category") or ""):
            cat_match += 1

        a = _entity_set(one)
        b = _entity_set(two)
        if a or b:
            jaccard_vals.append(len(a & b) / len(a | b))
            intersect_vals.append(len(a & b))
        onestep_ent_counts.append(len(a))
        twostep_ent_counts.append(len(b))

        for fld, lst_one, lst_two in [
            ("title", title_lens_one, title_lens_two),
            ("meta_description", md_lens_one, md_lens_two),
            ("h1", h1_lens_one, h1_lens_two),
            ("article_summary", sum_lens_one, sum_lens_two),
        ]:
            v1 = _len_or_none(one.get(fld))
            v2 = _len_or_none(two.get(fld))
            if v1 is None:
                missing_meta_one += 1
            else:
                lst_one.append(v1)
            if v2 is None:
                missing_meta_two += 1
            else:
                lst_two.append(v2)

    # ---- composing report ----
    def _row(label, one, two):
        if isinstance(one, dict):
            return f"| {label} | {one['mean']:.2f} / {one['p50']:.2f} / {one['p95']:.2f} | {two['mean']:.2f} / {two['p50']:.2f} / {two['p95']:.2f} |"
        return f"| {label} | {one} | {two} |"

    onestep_lat_s = _stat(onestep_lat)
    step1_lat_s = _stat(step1_lat)
    step2_lat_s = _stat(step2_lat)
    two_combined_lat_s = _stat(twostep_combined_lat)

    speedup_per_url = (
        two_combined_lat_s["mean"] / onestep_lat_s["mean"]
        if onestep_lat_s["mean"] > 0 else 0.0
    )
    speedup_wall = (twostep_wall / onestep_wall) if onestep_wall > 0 else 0.0

    report = out_dir / "report.md"
    lines: list[str] = []
    A = lines.append
    # Decision criteria — D7b
    CRIT_SPEEDUP = 1.5
    CRIT_CAT = 0.90
    CRIT_LANG = 0.95
    CRIT_JACC = 0.5
    cat_rate = cat_match / max(n, 1)
    lang_rate = lang_match / max(n, 1)
    jacc_mean = (sum(jaccard_vals) / len(jaccard_vals)) if jaccard_vals else 0.0

    one_fail_rate = onestep_fail / max(len(onestep), 1)
    two_total = max(len(set(twostep_step1_by_hash) | set(twostep_step2_by_hash)), 1)
    two_ok_combined = sum(
        1 for h in (set(twostep_step1_by_hash) | set(twostep_step2_by_hash))
        if twostep_step1_by_hash.get(h, {}).get("ok")
        and twostep_step2_by_hash.get(h, {}).get("ok")
    )
    two_fail_rate = (two_total - two_ok_combined) / two_total

    thr_one = (onestep_ok / onestep_wall * 3600) if onestep_wall > 0 else 0.0
    thr_two = (two_ok_combined / twostep_wall * 3600) if twostep_wall > 0 else 0.0

    A(f"# One-step vs Two-step — porównanie")
    A("")
    A(f"- Sample selection: **{'random' if random_sample else 'first-N (sorted)'}**"
      + (f", seed=`{seed}`" if random_sample else ""))
    A(f"- Sample size (common OK): **{n}**")
    A(f"- One-step records: ok={onestep_ok}  fail={onestep_fail}  fail_rate={100*one_fail_rate:.1f}%")
    A(f"- Two-step Step 1: ok={step1_ok}  fail={step1_fail}")
    A(f"- Two-step Step 2: ok={step2_ok}  fail={step2_fail}")
    A(f"- Two-step combined OK (both steps ok): {two_ok_combined}/{two_total}  "
      f"fail_rate={100*two_fail_rate:.1f}%")
    A("")
    A("## Verdict (D7b decision rule)")
    A("")
    A("| Criterion | Target | Actual | Pass? |")
    A("|---|---|---|---|")
    rows_v = [
        ("Speedup wall (two/one)", f"≥ {CRIT_SPEEDUP:.1f}×", f"{speedup_wall:.2f}×",
         speedup_wall >= CRIT_SPEEDUP),
        ("Speedup per-URL (two/one)", f"≥ {CRIT_SPEEDUP:.1f}×", f"{speedup_per_url:.2f}×",
         speedup_per_url >= CRIT_SPEEDUP),
        ("Category match", f"≥ {int(100*CRIT_CAT)}%", f"{100*cat_rate:.1f}%",
         cat_rate >= CRIT_CAT),
        ("Language match", f"≥ {int(100*CRIT_LANG)}%", f"{100*lang_rate:.1f}%",
         lang_rate >= CRIT_LANG),
        ("Entity Jaccard mean", f"≥ {CRIT_JACC:.2f}", f"{jacc_mean:.3f}",
         jacc_mean >= CRIT_JACC),
        ("Fail rate one ≤ two", "—",
         f"{100*one_fail_rate:.1f}% ≤ {100*two_fail_rate:.1f}%",
         one_fail_rate <= two_fail_rate),
    ]
    for label, target, actual, ok in rows_v:
        A(f"| {label} | {target} | {actual} | {'✅' if ok else '❌'} |")
    all_pass = all(r[3] for r in rows_v)
    speed_pass = rows_v[0][3] and rows_v[1][3]
    quality_pass = rows_v[2][3] and rows_v[3][3] and rows_v[4][3]
    if all_pass:
        A("")
        A("**→ One-step jest kandydatem na prod-default.** Wszystkie kryteria spełnione.")
    elif speed_pass and not quality_pass:
        A("")
        A("**→ Two-step zostaje defaultem.** One-step szybsze, ale traci na jakości.")
    elif quality_pass and not speed_pass:
        A("")
        A("**→ Two-step zostaje defaultem.** Jakość OK, ale brak istotnego speedupu.")
    else:
        A("")
        A("**→ Two-step zostaje defaultem.** One-step nie spełnia ani speedu, ani jakości.")
    A("")
    A("## Speed")
    A("")
    A("**Wall time** = subprocess wall time z tego skryptu (start runnera → koniec). "
      "Obejmuje load_articles, batchowanie, finalizację. **Sumowane przez wszystkie segmenty** "
      "(każdy run / resume = osobny segment dopisany do `compare_meta.json` → `history`). "
      "Model nie zwraca wall time — to nasz zewnętrzny pomiar.")
    A("")
    meta_now = _load_meta(out_dir)
    n_two_segm = _segments_count(meta_now, "twostep")
    n_one_segm = _segments_count(meta_now, "onestep")
    A(f"- one-step wall: **{onestep_wall:.1f}s** ({n_one_segm} seg) → throughput **{thr_one:.0f} URL/h**")
    A(f"- two-step wall: **{twostep_wall:.1f}s** ({n_two_segm} seg) → throughput **{thr_two:.0f} URL/h**")
    A(f"- ratio two/one (im wyżej tym one-step szybszy): **{speedup_wall:.2f}×**")
    A("")
    if meta_now.get("history"):
        A("### Historia segmentów")
        A("")
        A("| # | phase | started → ended | wall (s) | przetworzono | rc |")
        A("|---|---|---|---|---|---|")
        for i, h in enumerate(meta_now["history"], 1):
            span = f"{h.get('started_at','?')} → {h.get('ended_at','?')}"
            A(f"| {i} | {h.get('phase','?')} | {span} | "
              f"{h.get('wall_s', 0):.1f} | "
              f"+{h.get('ok_processed_in_segment', 0)} (→{h.get('ok_records_after', 0)}) | "
              f"{h.get('rc', '?')} |")
        A("")
        A("`przetworzono` = `+nowe_ok_w_tym_segmencie (→nowy_łączny_count_ok)`. Pozwala wyliczyć "
          "ile artykułów ten konkretny resume domyślił (użyteczne do per-segment throughputu).")
    A("")
    A("- **Per-URL latency (mean / p50 / p95) [s]:**")
    A("")
    A(f"| Phase | mean / p50 / p95 |")
    A(f"|---|---|")
    A(f"| one-step | {onestep_lat_s['mean']:.2f} / {onestep_lat_s['p50']:.2f} / {onestep_lat_s['p95']:.2f} |")
    A(f"| two-step Step 1 | {step1_lat_s['mean']:.2f} / {step1_lat_s['p50']:.2f} / {step1_lat_s['p95']:.2f} |")
    A(f"| two-step Step 2 | {step2_lat_s['mean']:.2f} / {step2_lat_s['p50']:.2f} / {step2_lat_s['p95']:.2f} |")
    A(f"| two-step combined | {two_combined_lat_s['mean']:.2f} / {two_combined_lat_s['p50']:.2f} / {two_combined_lat_s['p95']:.2f} |")
    A("")
    A(f"- Per-URL speedup (two-step combined / one-step): **{speedup_per_url:.2f}×**")
    A("")
    A("- **Token usage (per-URL mean):**")
    A("")
    A(f"| Phase | prompt_tok mean | completion_tok mean | sum |")
    A(f"|---|---|---|---|")
    A(f"| one-step | {_stat(onestep_in_tok)['mean']:.0f} | {_stat(onestep_out_tok)['mean']:.0f} | {_stat(onestep_in_tok)['sum']:.0f} / {_stat(onestep_out_tok)['sum']:.0f} |")
    A(f"| two-step Step 1 | {_stat(step1_in_tok)['mean']:.0f} | {_stat(step1_out_tok)['mean']:.0f} | {_stat(step1_in_tok)['sum']:.0f} / {_stat(step1_out_tok)['sum']:.0f} |")
    A(f"| two-step Step 2 | {_stat(step2_in_tok)['mean']:.0f} | {_stat(step2_out_tok)['mean']:.0f} | {_stat(step2_in_tok)['sum']:.0f} / {_stat(step2_out_tok)['sum']:.0f} |")
    A("")
    A("## Quality (na common OK subset)")
    A("")
    A(f"- language match: **{lang_match}/{n}** ({100*lang_match/max(n,1):.1f}%)")
    A(f"- category match: **{cat_match}/{n}** ({100*cat_match/max(n,1):.1f}%)")
    A("")
    A(f"- entities count (one-step):  mean={statistics.fmean(onestep_ent_counts) if onestep_ent_counts else 0:.1f}  ")
    A(f"- entities count (two-step):  mean={statistics.fmean(twostep_ent_counts) if twostep_ent_counts else 0:.1f}  ")
    A(f"- entity Jaccard (name+type, lowercased): mean={statistics.fmean(jaccard_vals) if jaccard_vals else 0:.3f}  median={statistics.median(jaccard_vals) if jaccard_vals else 0:.3f}")
    A(f"- intersection size (mean): {statistics.fmean(intersect_vals) if intersect_vals else 0:.1f}")
    A("")
    A("- SEO meta lengths (mean chars; missing field counts):")
    A("")
    A(f"| Field | one-step len | two-step len | missing one / two |")
    A(f"|---|---|---|---|")
    def _avg(xs): return f"{statistics.fmean(xs):.0f}" if xs else "—"
    A(f"| title           | {_avg(title_lens_one)} | {_avg(title_lens_two)} | — |")
    A(f"| meta_description| {_avg(md_lens_one)} | {_avg(md_lens_two)} | — |")
    A(f"| h1              | {_avg(h1_lens_one)} | {_avg(h1_lens_two)} | — |")
    A(f"| article_summary | {_avg(sum_lens_one)} | {_avg(sum_lens_two)} | — |")
    A(f"| (total missing across all 4 fields) | — | — | {missing_meta_one} / {missing_meta_two} |")
    A("")
    A("## Sample diff (do eyeballa)")
    A("")
    sample = common[: min(5, len(common))]
    for h in sample:
        one = onestep_by_hash[h]
        two = twostep_step2_by_hash[h]
        A(f"### {one.get('url')}")
        A(f"- lang: one={one.get('language')!r} two={two.get('language')!r}")
        A(f"- category: one={one.get('category')!r} two={two.get('category')!r}")
        A(f"- title (one): {one.get('title')!r}")
        A(f"- title (two): {two.get('title')!r}")
        A(f"- meta_description (one): {one.get('meta_description')!r}")
        A(f"- meta_description (two): {two.get('meta_description')!r}")
        a = _entity_set(one); b = _entity_set(two)
        A(f"- entities only in one-step ({len(a-b)}): {sorted(list(a-b))[:8]}")
        A(f"- entities only in two-step ({len(b-a)}): {sorted(list(b-a))[:8]}")
        A("")

    A("## Wnioski (do uzupełnienia po runie)")
    A("")
    A("- Czy speedup wall > 1.5×? Czy per-URL speedup > 1.5×?")
    A("- Czy category match > 90%? language match > 95%?")
    A("- Czy entity Jaccard > 0.5 albo intersection size porównywalny do mean count?")
    A("- Czy SEO meta lengths są w okolicy targetu (title ~50-60, meta_desc 140-160)?")
    A("- Czy fail rate one-step nie jest istotnie wyższy niż two-step (truncate / parse error)?")

    report.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Report: {report}")
    return report


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20,
                        help="Liczba URL w teście (default 20 — mała próbka).")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--tag", default=None,
                        help="Sufiks katalogu wyników (final_results/<ts>__compare_onestep__<tag>/)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--resume", default=None,
                        help="Wznów istniejący run-dir (idempotentny — JSONL po url_hash).")
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--random", action="store_true",
                        help="Losowa próbka zamiast pierwszych N (po sortowaniu). "
                             "Seed zapisywany do compare_meta.json — przy --resume użyty ten sam zestaw URL.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed dla --random (default 42). Wspólny dla one-step i two-step — gwarantuje "
                             "porównanie na tych samych URL.")
    parser.add_argument("--only", choices=["both", "onestep", "twostep"], default="both")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Pomiń uruchamianie pipeline'ów, zrób tylko analizę z istniejącego --resume dir.")
    args = parser.parse_args()

    if args.resume:
        out_dir = Path(args.resume)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        if not out_dir.exists():
            logger.error(f"Resume dir nie istnieje: {out_dir}")
            sys.exit(2)
    elif args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = "__compare_onestep" + (f"__{args.tag}" if args.tag else "")
        out_dir = FINAL_RESULT_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output: {out_dir}")

    # ---- meta + seed handling (resume = ten sam zestaw URL) ----
    meta_path = out_dir / "compare_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            meta = {}
    else:
        meta = {}

    use_random = bool(args.random)
    use_seed = int(args.seed)
    saved_random = meta.get("random_sample")
    saved_seed = meta.get("seed")
    if saved_random is not None:
        # Mamy wcześniejszy run — wymuś te same parametry samplingu
        if args.random and (saved_random != bool(args.random) or saved_seed != args.seed):
            logger.error(
                f"Konflikt sample config: zapisane random={saved_random} seed={saved_seed}, "
                f"podane random={args.random} seed={args.seed}. "
                f"Aby wymusić, usuń {meta_path} (i pliki wynikowe)."
            )
            sys.exit(2)
        use_random = bool(saved_random)
        use_seed = int(saved_seed) if saved_seed is not None else use_seed
        logger.info(f"Resume sample config z compare_meta.json: random={use_random} seed={use_seed}")
    else:
        meta["random_sample"] = use_random
        meta["seed"] = use_seed
        meta_path.write_text(json.dumps(meta, indent=2))

    if not args.analyze_only:
        # Kolejność: najpierw two-step potem one-step (lub odwrotnie — bez znaczenia
        # dla pomiaru per-URL latency; wall time każda ścieżka mierzona oddzielnie).
        # Każdy segment dopisuje się do meta["history"] — resume kumuluje się prawidłowo.
        if args.only in ("both", "twostep"):
            run_twostep(out_dir, args.limit, args.concurrency, args.no_skip,
                        random_sample=use_random, seed=use_seed, meta_path=meta_path)
        if args.only in ("both", "onestep"):
            run_onestep(out_dir, args.limit, args.concurrency, args.no_skip,
                        random_sample=use_random, seed=use_seed, meta_path=meta_path)

    # przeczytaj meta na nowo (segmenty mogły dodać się w runnerach)
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass

    # Kompatybilność wstecz: stare runy mają flat twostep_wall_s/onestep_wall_s
    # bez history. Dolej je jako pojedynczy segment placeholderem (raz).
    legacy_two = float(meta.get("twostep_wall_s") or 0.0)
    legacy_one = float(meta.get("onestep_wall_s") or 0.0)
    history = meta.get("history") or []
    has_two = any(h.get("phase") == "twostep" for h in history)
    has_one = any(h.get("phase") == "onestep" for h in history)
    if legacy_two and not has_two:
        history.append({
            "phase": "twostep", "started_at": "?", "ended_at": "?",
            "wall_s": round(legacy_two, 2),
            "ok_records_before": 0,
            "ok_records_after": _count_ok(out_dir / "final.jsonl"),
            "ok_processed_in_segment": _count_ok(out_dir / "final.jsonl"),
            "rc": 0, "legacy": True,
        })
    if legacy_one and not has_one:
        history.append({
            "phase": "onestep", "started_at": "?", "ended_at": "?",
            "wall_s": round(legacy_one, 2),
            "ok_records_before": 0,
            "ok_records_after": _count_ok(out_dir / "onestep.jsonl"),
            "ok_processed_in_segment": _count_ok(out_dir / "onestep.jsonl"),
            "rc": 0, "legacy": True,
        })
    meta["history"] = history

    # Total wall (sum segments) — to jest "ile zajęło wygenerowanie N artykułów łącznie".
    twostep_wall = _total_wall_from_history(meta, "twostep")
    onestep_wall = _total_wall_from_history(meta, "onestep")
    meta["twostep_wall_s_total"] = round(twostep_wall, 2)
    meta["onestep_wall_s_total"] = round(onestep_wall, 2)
    meta["twostep_segments"] = _segments_count(meta, "twostep")
    meta["onestep_segments"] = _segments_count(meta, "onestep")
    # Zachowaj też flat keys dla wstecznej kompatybilności dashboardu
    meta["twostep_wall_s"] = round(twostep_wall, 2)
    meta["onestep_wall_s"] = round(onestep_wall, 2)
    meta["limit"] = args.limit
    meta["concurrency"] = args.concurrency
    meta["random_sample"] = use_random
    meta["seed"] = use_seed
    meta_path.write_text(json.dumps(meta, indent=2))

    logger.info(
        f"Total wall — twostep: {twostep_wall:.1f}s ({meta['twostep_segments']} segm) · "
        f"onestep: {onestep_wall:.1f}s ({meta['onestep_segments']} segm)"
    )

    analyze(out_dir, twostep_wall=twostep_wall, onestep_wall=onestep_wall,
            random_sample=use_random, seed=use_seed)
    logger.info("=== DONE ===")


if __name__ == "__main__":
    main()
