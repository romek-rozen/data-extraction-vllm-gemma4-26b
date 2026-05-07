"""Pipeline log viewer — filter po poziomie, search, tail-N, auto-refresh."""

from __future__ import annotations

import re
import time
from pathlib import Path

import streamlit as st

from dashboard.data_loader import RESULTS_BASE

LEVEL_RE = re.compile(r"\b(INFO|WARNING|ERROR|DEBUG|CRITICAL)\b")


def _tail(path: Path, n: int) -> list[str]:
    """Czyta ostatnie n linii bez ładowania całego pliku do pamięci."""
    if not path.exists():
        return []
    if n <= 0:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            read = min(block, size)
            size -= read
            f.seek(size)
            data = f.read(read) + data
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def render(filters: dict, data: dict):
    st.title("Pipeline log")

    runs = filters["runs"]
    if not runs:
        st.info("Brak runów (sprawdź filtr Run w sidebar).")
        return

    run = st.selectbox("Run", runs, index=0)
    run_dir = RESULTS_BASE / run

    log_files = sorted(run_dir.glob("*.log"))
    if not log_files:
        st.warning(
            f"Brak plików `*.log` w `{run_dir}`.\n\n"
            "Spodziewane: `pipeline.log` (run_full), `onestep.log` / `twostep.log` (compare_onestep_vs_twostep)."
        )
        return

    log_names = [p.name for p in log_files]
    default_idx = log_names.index("pipeline.log") if "pipeline.log" in log_names else 0
    sel_log = st.radio("Log file", log_names, index=default_idx, horizontal=True)
    log_path = run_dir / sel_log

    size_kb = log_path.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_path.stat().st_mtime))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Plik", f"{size_kb:.1f} KB")
    c2.metric("Modified", mtime)

    c3_in, c4_in = st.columns(2)
    tail_n = c3_in.number_input("Ostatnie N linii (0 = wszystkie)", min_value=0, value=500, step=100)
    auto_refresh = c4_in.checkbox("Auto-refresh (co 5s)", value=False)

    levels = st.multiselect(
        "Poziomy", ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"],
        default=["WARNING", "ERROR", "CRITICAL", "INFO"],
    )
    search = st.text_input("Search (regex/substring, case-insensitive)", value="")

    lines = _tail(log_path, tail_n)

    if levels:
        lines = [l for l in lines if (LEVEL_RE.search(l).group(1) if LEVEL_RE.search(l) else "INFO") in levels]
    if search:
        try:
            pat = re.compile(search, re.IGNORECASE)
            lines = [l for l in lines if pat.search(l)]
        except re.error:
            lines = [l for l in lines if search.lower() in l.lower()]

    n_warn = sum(1 for l in lines if "WARNING" in l)
    n_err = sum(1 for l in lines if "ERROR" in l or "CRITICAL" in l)
    c3.metric("WARNINGs (po filtrze)", n_warn)
    c4.metric("ERRORs (po filtrze)", n_err)

    st.code("\n".join(lines) if lines else "(pusto po filtrze)", language="log")

    st.download_button(
        "Pobierz cały log",
        data=log_path.read_bytes(),
        file_name=f"{run}_{sel_log}",
        mime="text/plain",
    )

    if auto_refresh:
        time.sleep(5)
        st.rerun()
