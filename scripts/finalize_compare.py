"""Generuje glanceable summary z runa compare_onestep_vs_twostep.

Format wzorowany na mateusz-g-json-vs-flat/benchmark_results — czytelne na oko:
  run_meta.json : started, resumed, last_checkpoint, elapsed_s, n_articles, ...
  timing.csv    : phase, n_articles, block_elapsed_s, per_article_s, throughput_url_h
  summary.txt   : key numbers + ETA dla 21M URL

Działa na bazie:
  - compare_meta.json["history"]  → wall time per faza (suma segmentów)
  - *.jsonl ts (pierwszy/ostatni) → ground-truth wall time
  - *.jsonl ok=True count        → n_articles per faza

Użycie:
  python3 scripts/finalize_compare.py final_results/<run-dir>
  python3 scripts/finalize_compare.py final_results/<run-dir> --target-urls 21000000
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).parent.parent


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


def _dedup_last(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        h = r.get("url_hash")
        if h:
            out[h] = r
    return out


def _ts_range(records: dict[str, dict]) -> tuple[str | None, str | None, float | None]:
    ts_list = []
    for r in records.values():
        if not r.get("ok"):
            continue
        ts = r.get("ts")
        if ts:
            try:
                ts_list.append(datetime.fromisoformat(ts))
            except ValueError:
                pass
    if not ts_list:
        return None, None, None
    a, b = min(ts_list), max(ts_list)
    return a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds"), (b - a).total_seconds()


def _fmt_hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--target-urls", type=int, default=21_000_000,
                        help="Docelowa liczba URL do produkcji (default 21M).")
    parser.add_argument("--prod-speedup", type=float, default=2.5,
                        help="Spodziewany speedup RTX 5090 vs DGX Spark (default 2.5×).")
    parser.add_argument("--cost-per-mtok-output", type=float, default=0.0,
                        help="$ per milion completion tokens (np. self-hosted = 0).")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not run_dir.exists():
        print(f"Brak katalogu: {run_dir}", file=sys.stderr)
        sys.exit(2)

    # ---- meta + history ----
    meta_path = run_dir / "compare_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass
    history = meta.get("history") or []

    # ---- JSONL ----
    onestep = _dedup_last(_read_jsonl(run_dir / "onestep.jsonl"))
    step1 = _dedup_last(_read_jsonl(run_dir / "entity_layer.jsonl"))
    step2 = _dedup_last(_read_jsonl(run_dir / "final.jsonl"))

    one_first, one_last, one_ts_wall = _ts_range(onestep)
    s1_first, s1_last, s1_ts_wall = _ts_range(step1)
    s2_first, s2_last, s2_ts_wall = _ts_range(step2)

    one_ok = sum(1 for r in onestep.values() if r.get("ok"))
    s1_ok = sum(1 for r in step1.values() if r.get("ok"))
    s2_ok = sum(1 for r in step2.values() if r.get("ok"))

    # Wall time z history (suma segmentów)
    def _hist_wall(phase: str) -> float:
        return sum(float(h.get("wall_s", 0) or 0) for h in history if h.get("phase") == phase)

    one_hist_wall = _hist_wall("onestep")
    two_hist_wall = _hist_wall("twostep")

    # Latencje + tokeny
    def _agg(records, key):
        vals = [float(r.get(key, 0) or 0) for r in records.values() if r.get("ok")]
        return vals

    def _tok(records, kind):
        vals = []
        for r in records.values():
            if not r.get("ok"):
                continue
            u = r.get("usage") or {}
            vals.append(int(u.get(kind, 0) or 0))
        return vals

    one_lat = _agg(onestep, "latency_s")
    s1_lat = _agg(step1, "latency_s")
    s2_lat = _agg(step2, "latency_s")
    one_out_tok = _tok(onestep, "completion_tokens")
    s1_out_tok = _tok(step1, "completion_tokens")
    s2_out_tok = _tok(step2, "completion_tokens")

    # ---- run_meta.json (uproszczony, glanceable) ----
    run_meta = {
        "run_dir": str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir),
        "limit": meta.get("limit"),
        "concurrency": meta.get("concurrency"),
        "random_sample": meta.get("random_sample"),
        "seed": meta.get("seed"),
        "n_segments": len(history),
        "first_started_at": history[0]["started_at"] if history else None,
        "last_ended_at": history[-1]["ended_at"] if history else None,
        "n_ok_onestep": one_ok,
        "n_ok_twostep_s1": s1_ok,
        "n_ok_twostep_s2": s2_ok,
        "wall_total_onestep_s_history": round(one_hist_wall, 1),
        "wall_total_twostep_s_history": round(two_hist_wall, 1),
        "wall_total_onestep_s_ts": round(one_ts_wall, 1) if one_ts_wall else None,
        "wall_total_twostep_s2_ts": round(s2_ts_wall, 1) if s2_ts_wall else None,
        "wall_first_record_onestep": one_first,
        "wall_last_record_onestep": one_last,
        "wall_first_record_twostep_s1": s1_first,
        "wall_last_record_twostep_s2": s2_last,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2))

    # ---- timing.csv ----
    rows = []
    def _row(phase, n, wall_s, lat_list, out_tok):
        per_article = wall_s / n if (n and wall_s) else None
        thr_h = (n / wall_s * 3600) if (n and wall_s) else None
        rows.append({
            "phase": phase,
            "n_articles": n,
            "block_elapsed_s": round(wall_s, 1) if wall_s else "",
            "per_article_s": round(per_article, 2) if per_article else "",
            "throughput_url_h": round(thr_h, 0) if thr_h else "",
            "lat_mean_s": round(fmean(lat_list), 2) if lat_list else "",
            "out_tok_mean": round(fmean(out_tok), 0) if out_tok else "",
            "out_tok_sum": sum(out_tok) if out_tok else "",
        })
    _row("onestep", one_ok, one_hist_wall, one_lat, one_out_tok)
    _row("twostep_step1", s1_ok, _hist_wall("twostep"), s1_lat, s1_out_tok)  # S1+S2 dzielą wall
    _row("twostep_step2", s2_ok, _hist_wall("twostep"), s2_lat, s2_out_tok)
    _row("twostep_combined", min(s1_ok, s2_ok), two_hist_wall,
         [a + b for a, b in zip(s1_lat, s2_lat)],
         [a + b for a, b in zip(s1_out_tok, s2_out_tok)])

    timing_path = run_dir / "timing.csv"
    with open(timing_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- summary.txt ----
    target = args.target_urls
    speedup = args.prod_speedup
    lines = [
        f"=== {run_dir.name} ===",
        f"  limit={meta.get('limit')}  concurrency={meta.get('concurrency')}  "
        f"random={meta.get('random_sample')}  seed={meta.get('seed')}",
        f"  segmentów: {len(history)}",
        "",
        "WALL TIME (suma segmentów z history):",
        f"  one-step : {_fmt_hms(one_hist_wall)} ({one_hist_wall:.0f}s)  → {one_ok} URL OK",
        f"  two-step : {_fmt_hms(two_hist_wall)} ({two_hist_wall:.0f}s)  → {s2_ok} URL OK",
        "",
        "WALL TIME (z `ts` w JSONL — first → last record, ground truth):",
        f"  one-step : {_fmt_hms(one_ts_wall) if one_ts_wall else '—'}",
        f"  two-step S1 : {_fmt_hms(s1_ts_wall) if s1_ts_wall else '—'}",
        f"  two-step S2 : {_fmt_hms(s2_ts_wall) if s2_ts_wall else '—'}",
        "",
        "PER-ARTICLE (sequential view = wall_s / n_ok):",
    ]
    if one_hist_wall and one_ok:
        lines.append(f"  one-step : {one_hist_wall/one_ok:.2f}s/URL  → {one_ok/one_hist_wall*3600:.0f} URL/h")
    if two_hist_wall and s2_ok:
        lines.append(f"  two-step : {two_hist_wall/s2_ok:.2f}s/URL  → {s2_ok/two_hist_wall*3600:.0f} URL/h")
    lines.append("")
    lines.append(f"ETA dla {target:,} URL (przy aktualnym throughput, na DGX Spark):")
    if one_hist_wall and one_ok:
        days = (target * one_hist_wall / one_ok) / 86400
        lines.append(f"  one-step : {days:.0f} dni Spark   →  {days/speedup:.0f} dni RTX 5090 ({speedup}× speedup)")
    if two_hist_wall and s2_ok:
        days = (target * two_hist_wall / s2_ok) / 86400
        lines.append(f"  two-step : {days:.0f} dni Spark   →  {days/speedup:.0f} dni RTX 5090 ({speedup}× speedup)")
    lines.append("")
    if args.cost_per_mtok_output > 0:
        lines.append("KOSZT (extrapolacja całkowitych completion tokens):")
        if one_out_tok and one_ok:
            t = sum(one_out_tok) / one_ok * target
            lines.append(f"  one-step : {t/1e6:.0f} M tok  ×  ${args.cost_per_mtok_output}/M  =  ${t/1e6*args.cost_per_mtok_output:.0f}")
        if (s1_out_tok or s2_out_tok) and s2_ok:
            t1 = sum(s1_out_tok) / s2_ok * target if s1_out_tok else 0
            t2 = sum(s2_out_tok) / s2_ok * target if s2_out_tok else 0
            lines.append(f"  two-step : {(t1+t2)/1e6:.0f} M tok  ×  ${args.cost_per_mtok_output}/M  =  ${(t1+t2)/1e6*args.cost_per_mtok_output:.0f}")
        lines.append("")
    lines.append("Pliki: run_meta.json (1 obiekt), timing.csv (per phase), summary.txt (ten plik).")

    summary_path = run_dir / "summary.txt"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    # Print
    print(summary_path.read_text())
    print(f"→ {timing_path.name}")
    print(f"→ {(run_dir / 'run_meta.json').name}")


if __name__ == "__main__":
    main()
