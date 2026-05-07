"""Skan final_results/<run>/ → DataFrame Step1 + Step2 + summary/metryki.

Każdy podkatalog `final_results/` to jeden run. Per run:
- entity_layer.jsonl — Step 1 (encje + kategoria + język + latencja/tokeny)
- final.jsonl       — Step 2 (title, meta_description, h1, article_summary)
- summary.md, metrics_*.txt — opcjonalne, na żądanie

Wynik: dict z DataFrame'ami i metadanymi runu.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
RESULTS_BASE = ROOT / "final_results"

# Reuse mapowania typów z pipeline'u (51 typów Azure NER).
sys.path.insert(0, str(ROOT))
from lib.pipeline import TYPE_TO_CATEGORY  # noqa: E402

KNOWN_TYPES = set(TYPE_TO_CATEGORY.keys())


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _normalize_step1(rows: list[dict], run: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.json_normalize(rows, max_level=1)
    df["run"] = run
    df["entities_count"] = [len(r.get("entities") or []) for r in rows]
    df["entities"] = [r.get("entities") or [] for r in rows]
    df["prompt_tokens"] = df.get("usage.prompt_tokens", 0)
    df["completion_tokens"] = df.get("usage.completion_tokens", 0)
    return df


def _normalize_step2(rows: list[dict], run: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.json_normalize(rows, max_level=1)
    df["run"] = run
    for col, default in [
        ("title", ""),
        ("meta_description", ""),
        ("h1", ""),
        ("article_summary", ""),
    ]:
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna("")
        df[f"{col}_len"] = df[col].astype(str).str.len()
    df["prompt_tokens"] = df.get("usage.prompt_tokens", 0)
    df["completion_tokens"] = df.get("usage.completion_tokens", 0)
    return df


@st.cache_data(ttl=10, show_spinner=False)
def load_results() -> dict:
    """Skanuje RESULTS_BASE i zwraca zunifikowane dane."""
    if not RESULTS_BASE.exists():
        return {
            "step1": pd.DataFrame(),
            "step2": pd.DataFrame(),
            "runs": [],
            "summaries": {},
            "metrics": {},
        }

    step1_frames: list[pd.DataFrame] = []
    step2_frames: list[pd.DataFrame] = []
    runs: list[str] = []
    summaries: dict[str, str] = {}
    metrics: dict[str, dict[str, str]] = {}

    for run_dir in sorted(RESULTS_BASE.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("_") or run_dir.name.startswith("."):
            continue

        s1 = _read_jsonl(run_dir / "entity_layer.jsonl")
        s2 = _read_jsonl(run_dir / "final.jsonl")
        if not s1 and not s2:
            continue

        runs.append(run_dir.name)
        if s1:
            step1_frames.append(_normalize_step1(s1, run_dir.name))
        if s2:
            step2_frames.append(_normalize_step2(s2, run_dir.name))

        summary_path = run_dir / "summary.md"
        if summary_path.exists():
            summaries[run_dir.name] = summary_path.read_text(encoding="utf-8")

        metrics[run_dir.name] = {}
        for name in ("metrics_before.txt", "metrics_after.txt", "metrics_delta.txt"):
            mp = run_dir / name
            if mp.exists():
                metrics[run_dir.name][name] = mp.read_text(encoding="utf-8")

    step1 = pd.concat(step1_frames, ignore_index=True) if step1_frames else pd.DataFrame()
    step2 = pd.concat(step2_frames, ignore_index=True) if step2_frames else pd.DataFrame()

    return {
        "step1": step1,
        "step2": step2,
        "runs": runs,
        "summaries": summaries,
        "metrics": metrics,
    }


def explode_entities(step1: pd.DataFrame) -> pd.DataFrame:
    """Rozwija encje do długiego DataFrame: jeden wiersz = jedna encja."""
    if step1.empty:
        return pd.DataFrame(columns=["run", "url_hash", "url", "domain", "category_article",
                                     "name", "type", "category", "strength", "off_list"])
    rows = []
    for _, r in step1.iterrows():
        for e in r.get("entities") or []:
            t = e.get("type", "")
            rows.append({
                "run": r.get("run"),
                "url_hash": r.get("url_hash"),
                "url": r.get("url"),
                "domain": r.get("domain"),
                "category_article": r.get("category"),
                "language": r.get("language"),
                "name": e.get("name", ""),
                "type": t,
                "category": e.get("category", ""),
                "strength": e.get("strength", ""),
                "off_list": t not in KNOWN_TYPES,
            })
    return pd.DataFrame(rows)


def merge_steps(step1: pd.DataFrame, step2: pd.DataFrame) -> pd.DataFrame:
    """Merge Step1 + Step2 po (run, url_hash). Suffixes _s1/_s2."""
    if step1.empty:
        return step2.copy()
    if step2.empty:
        return step1.copy()
    return step1.merge(
        step2,
        on=["run", "url_hash"],
        how="outer",
        suffixes=("_s1", "_s2"),
    )
