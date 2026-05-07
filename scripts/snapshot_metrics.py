"""Snapshot vLLM Prometheus /metrics dla diagnostyki prefix cache.

Workaround dla `prompt_tokens_details: null` w response per-request
(bug w vLLM build `gemma4-cu130`). `/metrics` jest globalny od startu kontenera.

Użycie:
    python3 scripts/snapshot_metrics.py before > result/metrics_before.txt
    # ...run pipeline...
    python3 scripts/snapshot_metrics.py after  > result/metrics_after.txt
    python3 scripts/snapshot_metrics.py diff result/metrics_before.txt result/metrics_after.txt
"""

import re
import sys
from pathlib import Path

import requests

URL = "http://localhost:8001/metrics"

# Metryki które nas interesują (kluczowe pól per_run delta)
KEYS = [
    "vllm:prefix_cache_queries_total",
    "vllm:prefix_cache_hits_total",
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:kv_cache_usage_perc",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:request_success_total",
    "vllm:e2e_request_latency_seconds_sum",
    "vllm:e2e_request_latency_seconds_count",
]


def parse_metrics(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r"^([a-z_:]+)(\{[^}]*\})?\s+([0-9eE.+\-]+)$", line)
        if not m:
            continue
        name, _labels, value = m.groups()
        if name in KEYS:
            out[name] = out.get(name, 0.0) + float(value)
    return out


def fetch() -> dict[str, float]:
    r = requests.get(URL, timeout=5)
    r.raise_for_status()
    return parse_metrics(r.text)


def show(label: str, m: dict[str, float]):
    print(f"=== {label} ===")
    for k in KEYS:
        print(f"  {k:50} = {m.get(k, 0):.0f}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    if cmd == "before" or cmd == "after":
        m = fetch()
        show(cmd, m)
    elif cmd == "diff" and len(sys.argv) == 4:
        before = parse_metrics(Path(sys.argv[2]).read_text())
        after = parse_metrics(Path(sys.argv[3]).read_text())
        print("=== DELTA (after - before) ===")
        for k in KEYS:
            d = after.get(k, 0) - before.get(k, 0)
            print(f"  {k:50} = {d:+.0f}")
        q = after.get("vllm:prefix_cache_queries_total", 0) - before.get("vllm:prefix_cache_queries_total", 0)
        h = after.get("vllm:prefix_cache_hits_total", 0) - before.get("vllm:prefix_cache_hits_total", 0)
        if q > 0:
            print(f"\n→ Prefix cache hit rate (run): {100 * h / q:.1f}%  ({h:.0f} / {q:.0f} tokenów)")
        n_req = after.get("vllm:e2e_request_latency_seconds_count", 0) - before.get("vllm:e2e_request_latency_seconds_count", 0)
        t_sum = after.get("vllm:e2e_request_latency_seconds_sum", 0) - before.get("vllm:e2e_request_latency_seconds_sum", 0)
        if n_req > 0:
            print(f"→ Średnia latencja: {t_sum / n_req:.2f} s/req  ({n_req:.0f} requestów, {t_sum:.0f} s sumarycznie)")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
