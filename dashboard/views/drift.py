"""Drift tempa — czy model zwalnia w trakcie runa.

Sortuje rekordy po `ts` (timestamp wykonania) i pokazuje:
- rolling median latency wzdłuż osi sekwencyjnej
- tabelę chunków per N rekordów (median/p95 latency, throughput URL/h)
- drift metric: (last_chunk - first_chunk) / first_chunk * 100%

Sygnatura drift:
- <5%   → ✅ stabilne
- 5-15% → ⚠️ akceptowalne (rozważ daily restart vLLM)
- >15%  → ❌ memory leak / KV cache fragmentation
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


SOURCES = [
    ("Step 1 (entity_layer)", "step1"),
    ("Step 2 (final)", "step2"),
    ("One-step (onestep)", "onestep"),
]


def _avg_concurrency(df: pd.DataFrame) -> float:
    """Średnia liczba równolegle aktywnych requestów (overlap [start, ts])."""
    if df.empty or "latency_s" not in df.columns:
        return 0.0
    end = df["ts_dt"].astype("int64") / 1e9
    start = end - df["latency_s"].astype(float)
    events = []
    for s, e in zip(start, end):
        events.append((s, +1))
        events.append((e, -1))
    events.sort()
    cur = 0
    samples = []
    for _, delta in events:
        cur += delta
        if delta == +1:
            samples.append(cur)
    return sum(samples) / len(samples) if samples else 0.0


def _prepare(df: pd.DataFrame, run: str) -> pd.DataFrame:
    """Filtruj run, zostaw ok=True, sortuj po ts, dodaj seq."""
    if df.empty or "ts" not in df.columns:
        return pd.DataFrame()
    d = df[df["run"] == run].copy()
    if "ok" in d.columns:
        d = d[d["ok"] == True]  # noqa: E712
    if d.empty or "latency_s" not in d.columns:
        return pd.DataFrame()
    d["ts_dt"] = pd.to_datetime(d["ts"], errors="coerce")
    d = d.dropna(subset=["ts_dt"])
    if d.empty:
        return pd.DataFrame()
    d = d.sort_values("ts_dt").reset_index(drop=True)
    d["seq"] = range(1, len(d) + 1)
    return d


def _chunk_table(d: pd.DataFrame, chunks: int = 10) -> pd.DataFrame:
    n = len(d)
    if n < chunks:
        chunks = max(1, n // 50) or 1
    size = max(1, n // chunks)
    rows = []
    for i in range(chunks):
        a = i * size
        b = (i + 1) * size if i < chunks - 1 else n
        chunk = d.iloc[a:b]
        if chunk.empty:
            continue
        wall_s = (chunk["ts_dt"].iloc[-1] - chunk["ts_dt"].iloc[0]).total_seconds()
        thr = round(len(chunk) / wall_s * 3600, 0) if wall_s > 0 else None
        rows.append({
            "chunk": f"{a + 1}-{b}",
            "n": len(chunk),
            "median_lat_s": round(chunk["latency_s"].median(), 2),
            "p95_lat_s": round(chunk["latency_s"].quantile(0.95), 2),
            "wall_s": round(wall_s, 1),
            "throughput_url_h": thr,
        })
    return pd.DataFrame(rows)


MIN_CHUNK_SIZE_FOR_VERDICT = 100  # poniżej tego mediana per chunk jest zbyt szumna


def _drift_metric(table: pd.DataFrame) -> tuple[float, str, str]:
    """Drift = nachylenie regresji liniowej median(lat) po chunkach, znormalizowane do %.

    Zamiast first vs last (który łapie pojedyncze outliery), liczymy trend
    przez wszystkie chunki. Bardziej odporne na szum.
    """
    if len(table) < 3:
        return 0.0, "ℹ️ za mało chunków", "Potrzeba min 3 chunków do trendu."

    min_n = int(table["n"].min())
    if min_n < MIN_CHUNK_SIZE_FOR_VERDICT:
        # Próbka za mała — pokazujemy liczby, ale nie wydajemy werdyktu
        first = table["median_lat_s"].iloc[0]
        last = table["median_lat_s"].iloc[-1]
        delta = (last - first) / first * 100 if first > 0 else 0
        return (
            delta,
            "ℹ️ za mała próbka",
            f"Chunki po {min_n} rekordów — mediana skacze ±10-15% przez statystyczny szum, "
            f"nie regresję modelu. Werdykt po ≥{MIN_CHUNK_SIZE_FOR_VERDICT}/chunk "
            f"(czyli ≥{MIN_CHUNK_SIZE_FOR_VERDICT * len(table)} rekordów ok=True total).",
        )

    # Regresja liniowa: y = a*x + b, gdzie x = indeks chunka, y = median latency
    import numpy as np
    x = np.arange(len(table), dtype=float)
    y = table["median_lat_s"].to_numpy(dtype=float)
    a, b = np.polyfit(x, y, 1)
    # Procentowe nachylenie: ile rośnie latency od pierwszego do ostatniego chunka (w %)
    y_first = a * x[0] + b
    y_last = a * x[-1] + b
    drift = (y_last - y_first) / y_first * 100 if y_first > 0 else 0

    # R² — czy trend jest realny czy szum
    y_pred = a * x + b
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    if r2 < 0.3:
        return drift, "✅ stabilne (szum)", (
            f"Trend liniowy słaby (R²={r2:.2f}) — chunki skaczą losowo, brak monotonicznej regresji. "
            "Memory leak by dawał R²≥0.7 i monotoniczny wzrost."
        )

    if abs(drift) < 5:
        return drift, "✅ stabilne", f"Trend liniowy płaski (R²={r2:.2f}). Latencja stała przez run."
    if abs(drift) < 15:
        return drift, "⚠️ lekki drift", (
            f"Monotoniczny trend (R²={r2:.2f}). Rozważ daily restart vLLM przy długich runach."
        )
    return drift, "❌ regresja", (
        f"Mocny monotoniczny trend (R²={r2:.2f}, nachylenie {a:+.3f}s/chunk). "
        "Memory leak / KV cache fragmentation. Debug przed prod 1M."
    )


def _rolling_chart(d: pd.DataFrame, window: int):
    import plotly.graph_objects as go

    rolling = d["latency_s"].rolling(window=window, min_periods=max(1, window // 4)).median()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["seq"], y=d["latency_s"],
        mode="markers", name="latency (per record)",
        marker=dict(size=3, opacity=0.25, color="#888"),
    ))
    fig.add_trace(go.Scatter(
        x=d["seq"], y=rolling,
        mode="lines", name=f"rolling median (w={window})",
        line=dict(color="#1f77b4", width=2),
    ))
    fig.update_layout(
        xaxis_title="record sequence (sorted by ts)",
        yaxis_title="latency (s)",
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    return fig


def render(filters: dict, data: dict):
    st.title("Drift tempa — czy model zwalnia w trakcie runa")

    st.markdown(
        "**Co to robi:** sortuje rekordy po `ts` (kolejność wykonania), dzieli na N chunków "
        "i mierzy czy latencja rośnie monotonicznie w trakcie runa.\n\n"
        "**Drift** liczony jako nachylenie regresji liniowej median(latency) po wszystkich chunkach, "
        "znormalizowane do %. **R²** mówi czy trend jest realny (R²≥0.7) czy losowy szum (R²<0.3). "
        "Memory leak daje **mocny monotoniczny trend** — sam wynik first vs last to za mało, "
        f"bo chunki <{MIN_CHUNK_SIZE_FOR_VERDICT} rekordów mają szum ±10-15%."
    )

    runs = filters["runs"]
    sources = {
        "step1": filters["step1"],
        "step2": filters["step2"],
        "onestep": filters.get("onestep") if filters.get("onestep") is not None else pd.DataFrame(),
    }

    available_runs = [r for r in runs if any(
        not src.empty and "ts" in src.columns and not src[src["run"] == r].empty
        for src in sources.values()
    )]
    if not available_runs:
        st.info("Brak danych z polem `ts` w wybranych runach.")
        return

    run = st.selectbox("Run", available_runs, index=0)

    with st.expander("ℹ️ Co to są te suwaki?", expanded=False):
        st.markdown(
            "**Rolling window (records)** — szerokość okna do wygładzania wykresu latency.\n\n"
            "Dla każdego rekordu liczona jest mediana z N sąsiadujących rekordów (np. window=100 → "
            "mediana z 100 ostatnich). Mała wartość (20) = wykres skacze na pojedynczych outlierach, "
            "widać szum. Duża (500) = wykres gładki, widać tylko długoterminowe trendy. "
            "**Default 100 to dobry kompromis.** Zmniejsz jeśli chcesz zobaczyć krótkie spike'i (np. "
            "co 200 rekordów coś chrupie); zwiększ jeśli wykres jest zbyt skoczny.\n\n"
            "**Liczba chunków** — na ile równych części podzielić cały run do tabeli i metryki drift.\n\n"
            "Drift = porównanie pierwszego chunka z ostatnim. Przy 10 chunkach na 5000 rekordach: "
            "każdy chunk = 500 URLi, porównujesz pierwsze 500 z ostatnimi 500. Mniej chunków (5) = "
            "większe próbki, stabilniejsze mediany, ale mniejsza rozdzielczość czasowa. Więcej (20) = "
            "widzisz szczegółowiej kiedy nastąpiło zwolnienie, ale chunki po 250 rekordów mogą być "
            "szumne. **Dla 5000 URLi default 10 (chunk=500) jest OK.** Dla 1M ustaw 20 (chunk=50k)."
        )

    c_w, c_n = st.columns(2)
    window = c_w.slider(
        "Rolling window (records)",
        min_value=20, max_value=500, value=100, step=20,
        help="Szerokość okna wygładzającego dla niebieskiej linii na wykresie. Większe = gładziej.",
    )
    n_chunks = c_n.slider(
        "Liczba chunków",
        min_value=5, max_value=20, value=10,
        help="Na ile części dzielimy run. Drift % = porównanie pierwszego chunka z ostatnim.",
    )

    for label, key in SOURCES:
        df = sources.get(key, pd.DataFrame())
        d = _prepare(df, run)
        if d.empty:
            continue

        st.header(label)
        st.caption(f"N (ok=True) = {len(d)} · pierwszy ts = {d['ts_dt'].iloc[0]} · ostatni ts = {d['ts_dt'].iloc[-1]}")

        table = _chunk_table(d, chunks=n_chunks)
        drift, badge, hint = _drift_metric(table)

        # Concurrency check (drugi sygnał memory leak — gdyby był, vLLM zwalniałby slots wolniej)
        n_split = min(50, len(d) // 4)
        conc_first = _avg_concurrency(d.iloc[:n_split]) if n_split > 0 else 0
        conc_last = _avg_concurrency(d.iloc[-n_split:]) if n_split > 0 else 0
        conc_delta_pct = (conc_last - conc_first) / conc_first * 100 if conc_first > 0 else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Median lat (cały run)", f"{d['latency_s'].median():.2f} s")
        c2.metric("Median lat (first chunk)", f"{table['median_lat_s'].iloc[0]:.2f} s" if not table.empty else "—")
        c3.metric("Median lat (last chunk)", f"{table['median_lat_s'].iloc[-1]:.2f} s" if not table.empty else "—")
        c4.metric("Drift (trend)", f"{drift:+.1f}%", help=hint)
        c5.metric(
            "Concurrency (first/last)",
            f"{conc_first:.1f} → {conc_last:.1f}",
            delta=f"{conc_delta_pct:+.1f}%",
            delta_color="inverse",
            help="Avg liczba równoległych requestów. Memory leak by dawał spadek (vLLM zwalnia slots wolniej). "
                 "Stabilne = vLLM zdrowy.",
        )

        st.markdown(f"**Status: {badge}** — {hint}")

        st.plotly_chart(_rolling_chart(d, window), use_container_width=True)

        st.subheader("Chunki")
        st.dataframe(table, use_container_width=True, hide_index=True)
