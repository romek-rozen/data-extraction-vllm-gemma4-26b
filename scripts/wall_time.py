"""Liczy realny wall time runa z timestampów w JSONL.

Ground truth — nie zależy od `compare_meta.json` ani logów. Każdy rekord ma `ts`
(ISO timestamp dopisany w `lib/pipeline.py` / `lib/pipeline_onestep.py`).

Output: tabela per-plik z first/last ts, span (=wall time), N records, throughput.

Użycie:
    python3 scripts/wall_time.py final_results/<run-dir>
    python3 scripts/wall_time.py final_results/<run-dir>/onestep.jsonl
    python3 scripts/wall_time.py final_results/*__compare_onestep__*  # glob
"""

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

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


def _fmt_hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def analyze_jsonl(path: Path) -> dict | None:
    """Zwróć słownik z metrykami wall time z pojedynczego JSONL."""
    rows = _read_jsonl(path)
    if not rows:
        return None
    ts_list = []
    lat_sum = 0.0
    n_ok = 0
    for r in rows:
        if not r.get("ok"):
            continue
        ts = r.get("ts")
        if ts:
            try:
                ts_list.append(datetime.fromisoformat(ts))
            except ValueError:
                pass
        lat_sum += float(r.get("latency_s") or 0)
        n_ok += 1

    out = {
        "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "n_ok": n_ok,
        "lat_sum_s": round(lat_sum, 1),
    }
    if ts_list:
        first = min(ts_list)
        last = max(ts_list)
        wall = (last - first).total_seconds()
        out.update({
            "first_ts": first.isoformat(timespec="seconds"),
            "last_ts": last.isoformat(timespec="seconds"),
            "wall_s": round(wall, 1),
            "wall_hms": _fmt_hms(wall),
            "throughput_url_h": round(n_ok / wall * 3600, 0) if wall > 0 else 0,
            "n_with_ts": len(ts_list),
        })
    else:
        out["wall_s"] = None
        out["wall_hms"] = "(brak ts w rekordach — uruchom nowy run po dodaniu pola `ts`)"
        out["throughput_url_h"] = None
    return out


def analyze_dir(d: Path) -> list[dict]:
    """Przeanalizuj wszystkie 3 pliki JSONL w katalogu compare runa."""
    out = []
    for fname in ("entity_layer.jsonl", "final.jsonl", "onestep.jsonl"):
        p = d / fname
        if p.exists():
            r = analyze_jsonl(p)
            if r:
                out.append(r)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+",
                        help="JSONL plik(i) lub katalog(i) z final_results/<run>/")
    args = parser.parse_args()

    rows: list[dict] = []
    for p_arg in args.paths:
        p = Path(p_arg)
        if not p.exists():
            print(f"⚠️ {p} — nie istnieje", file=sys.stderr)
            continue
        if p.is_dir():
            rows.extend(analyze_dir(p))
        elif p.suffix == ".jsonl":
            r = analyze_jsonl(p)
            if r:
                rows.append(r)
        else:
            print(f"⚠️ {p} — pomijam (nie .jsonl ani katalog)", file=sys.stderr)

    if not rows:
        print("Brak danych.")
        return

    # Pretty print
    cols = ["file", "n_ok", "wall_hms", "wall_s", "lat_sum_s", "throughput_url_h"]
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    sep = "  "
    print(sep.join(c.ljust(widths[c]) for c in cols))
    print(sep.join("-" * widths[c] for c in cols))
    for r in rows:
        print(sep.join(str(r.get(c, "—")).ljust(widths[c]) for c in cols))

    print()
    print("Wyjaśnienie:")
    print("  wall_hms / wall_s = czas między PIERWSZYM a OSTATNIM rekordem (ground truth z `ts`).")
    print("  lat_sum_s         = suma per-request latencji (gdyby concurrency=1).")
    print("  throughput_url_h  = n_ok / wall × 3600  (realny URL/h tej fazy).")
    print("  Jeśli wall_hms 'brak ts' — rekordy zapisane przed dodaniem pola `ts`. Nowe runy będą OK.")


if __name__ == "__main__":
    main()
