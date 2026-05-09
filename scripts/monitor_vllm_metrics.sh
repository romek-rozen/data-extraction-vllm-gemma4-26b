#!/bin/bash
# Monitor vLLM /metrics co INTERVAL sekund, zapisuje do pliku.
# Usage: bash scripts/monitor_vllm_metrics.sh [interval_s] [out_file]
#
# Kluczowe metryki:
#   num_requests_running     - inflight (limit = max-num-seqs)
#   num_requests_waiting     - kolejka klienta (>0 = klient pcha więcej niż serwer trawi)
#   kv_cache_usage_perc      - % KV cache wykorzystane (0..1)
#   num_preemptions_total    - ile razy vLLM wyrzucił request z batcha (KV thrashing!)
#   prefix_cache_queries/hits_total - 73% to dobry hit rate
#   generation_tokens_per_second - aggregate output throughput

INTERVAL=${1:-5}
OUT=${2:-/tmp/vllm_metrics.log}
URL=${URL:-http://localhost:8001/metrics}

echo "interval=${INTERVAL}s  out=${OUT}  url=${URL}"
echo "stop: Ctrl-C lub kill -TERM PID"

while true; do
  ts=$(date '+%H:%M:%S')
  m=$(curl -s "$URL" 2>/dev/null | grep -E "^vllm:(num_requests_running|num_requests_waiting|kv_cache_usage_perc|num_preemptions_total|prefix_cache_(queries|hits)_total|generation_tokens_per_second|prompt_tokens_per_second|gpu_cache_usage_perc)" | grep -v "^#")
  if [ -z "$m" ]; then
    echo "$ts  [no metrics — vLLM down?]" | tee -a "$OUT"
  else
    echo "=== $ts ===" | tee -a "$OUT"
    echo "$m" | tee -a "$OUT"
  fi
  sleep "$INTERVAL"
done
