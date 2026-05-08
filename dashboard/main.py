"""Streamlit dashboard — analiza wyników two-step pipeline.

Uruchamianie:
    streamlit run dashboard/main.py --server.address 0.0.0.0 --server.port 8501
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(page_title="Two-Step Pipeline Dashboard", layout="wide")

from dashboard.data_loader import load_results, RESULTS_BASE  # noqa: E402
from dashboard.components.filters import render_filters  # noqa: E402
from dashboard.views import (  # noqa: E402
    run_summary, articles, entities, categories,
    compare_runs, compare_onestep, analytics, drift, logs, sponsored,
)

PAGES = {
    "run_summary": ("Run Summary", run_summary),
    "articles": ("Eksplorator artykułów", articles),
    "entities": ("Encje (typy)", entities),
    "categories": ("Kategorie artykułów", categories),
    "sponsored": ("🎯 Sponsored Detection", sponsored),
    "compare_runs": ("Porównanie runów", compare_runs),
    "compare_onestep": ("One-step vs Two-step", compare_onestep),
    "analytics": ("📈 Analiza jakości", analytics),
    "drift": ("⏱️ Drift tempa", drift),
    "logs": ("Pipeline log", logs),
}


def main():
    st.sidebar.title("Two-Step Pipeline")

    page_key = st.query_params.get("page", "run_summary")
    if page_key not in PAGES:
        page_key = "run_summary"

    keys = list(PAGES.keys())
    labels = [PAGES[k][0] for k in keys]
    idx = keys.index(page_key)

    selected_label = st.sidebar.radio("Widok", labels, index=idx)
    selected_key = keys[labels.index(selected_label)]
    if selected_key != page_key:
        st.query_params["page"] = selected_key
        st.rerun()

    data = load_results()

    if not data["runs"]:
        st.title("Two-Step Pipeline Dashboard")
        st.warning(f"Brak wyników w `{RESULTS_BASE}`.")
        st.markdown(
            """
Uruchom pipeline:
```bash
python -u scripts/run_full.py --out-dir final_results/<run-name> --limit 0 --concurrency 8
```
            """
        )
        return

    filters = render_filters(
        data["step1"], data["step2"], data["runs"],
        onestep=data.get("onestep"),
    )

    module = PAGES[selected_key][1]
    module.render(filters, data)


if __name__ == "__main__":
    main()
