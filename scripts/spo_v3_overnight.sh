#!/bin/bash
# Overnight SPO v3 benchmark + full run.
#
# Flow:
#   1. (skipped if RESUME_AFTER_BENCH=1) v1 cram bench 1000 art seed=42 conc=8 cold cache
#   2. (skipped if RESUME_AFTER_BENCH=1) v2 split bench 1000 art seed=42 conc=8 cold cache
#   3. compare wall + triples + s_unmatched → pick winner (v2 default unless v1 has clearly
#      better metrics on multiple axes)
#   4. clear cache + launch winner pipeline on full 25667 articles, --concurrency 8
#
# Runs forever in tmux session "spo_master" until full sample completes.
# All progress visible in final_results/*__spo_v{1,2}_v3_bench_1k_s42/run.log and
# .../final_results/*__spo_v{1,2}_v3_full/run.log.
#
# Designed to be robust to short interruptions: each run is `python3 -u` so output is
# unbuffered into the per-run logs, and tmux survives ssh disconnects.

set -u

REPO=/home/spark001/Spark-testy/mateusz-g-two-step-vllm
cd "$REPO" || exit 99

LOG="$REPO/SESSIONS_SUMMARY/2026-05-08_overnight_master.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') === SPO v3 overnight master start ===" >> "$LOG"

clear_cache() {
    find "$REPO/websites_cache" -name "*.json" -delete 2>/dev/null
    local n
    n=$(ls "$REPO/websites_cache" 2>/dev/null | wc -l)
    echo "$(date '+%H:%M:%S') cache cleared, files=$n" >> "$LOG"
}

# ----- Stage 1: v1 cram bench 1000 art (skipped if dir already final.jsonl complete) -----
V1_DIR=$(ls -dt "$REPO"/final_results/*__spo_v1_v3_bench_1k_s42 2>/dev/null | head -1)
if [ -z "$V1_DIR" ] || ! grep -q '"wall_s"' "$V1_DIR/run_meta.json" 2>/dev/null; then
    if [ -z "$V1_DIR" ]; then
        echo "$(date '+%H:%M:%S') no v1 bench dir found — would run, but assume already launched separately" >> "$LOG"
        # If no v1 bench was launched outside this script, start one.
        clear_cache
        echo "$(date '+%H:%M:%S') launching v1 cram bench 1000 art seed=42" >> "$LOG"
        python3 -u scripts/run_spo_v1.py --limit 1000 --random --seed 42 \
            --concurrency 8 --tag v3_bench_1k_s42 >> "$LOG" 2>&1
    else
        echo "$(date '+%H:%M:%S') v1 bench dir exists ($V1_DIR) but not finalized — waiting for it to complete" >> "$LOG"
        # Poll until run_meta.json has wall_s (script completed)
        until grep -q '"wall_s"' "$V1_DIR/run_meta.json" 2>/dev/null; do
            sleep 60
        done
    fi
    V1_DIR=$(ls -dt "$REPO"/final_results/*__spo_v1_v3_bench_1k_s42 2>/dev/null | head -1)
fi
echo "$(date '+%H:%M:%S') v1 bench done: $V1_DIR" >> "$LOG"

# ----- Stage 2: v2 split bench 1000 art (cold cache) -----
V2_DIR=$(ls -dt "$REPO"/final_results/*__spo_v2_v3_bench_1k_s42 2>/dev/null | head -1)
if [ -z "$V2_DIR" ] || ! grep -q '"wall_s"' "$V2_DIR/run_meta.json" 2>/dev/null; then
    clear_cache
    echo "$(date '+%H:%M:%S') launching v2 split bench 1000 art seed=42" >> "$LOG"
    python3 -u scripts/run_spo_v2.py --limit 1000 --random --seed 42 \
        --concurrency 8 --tag v3_bench_1k_s42 >> "$LOG" 2>&1
    V2_DIR=$(ls -dt "$REPO"/final_results/*__spo_v2_v3_bench_1k_s42 2>/dev/null | head -1)
fi
echo "$(date '+%H:%M:%S') v2 bench done: $V2_DIR" >> "$LOG"

# ----- Stage 3: compare and pick winner -----
# Default winner: v2 (split — user expectation, smoke shows higher quality).
# Override: only if v1 has BOTH (lower s_unmatched AND comparable triples count) AND
# substantially shorter wall — then v1 wins on cost/benefit.
WINNER="v2"
if [ -n "$V1_DIR" ] && [ -n "$V2_DIR" ]; then
    V1_WALL=$(python3 -c "import json; print(json.load(open('$V1_DIR/run_meta.json'))['wall_s'])" 2>/dev/null || echo 0)
    V2_WALL=$(python3 -c "import json; print(json.load(open('$V2_DIR/run_meta.json'))['wall_s'])" 2>/dev/null || echo 0)
    V1_TRIPLES=$(python3 -c "import json; print(json.load(open('$V1_DIR/run_meta.json'))['counters']['triples_total'])" 2>/dev/null || echo 0)
    V2_TRIPLES=$(python3 -c "import json; print(json.load(open('$V2_DIR/run_meta.json'))['counters']['triples_total'])" 2>/dev/null || echo 0)
    V1_SUNM=$(python3 -c "import json; print(json.load(open('$V1_DIR/run_meta.json'))['counters']['s_unmatched_total'])" 2>/dev/null || echo 0)
    V2_SUNM=$(python3 -c "import json; print(json.load(open('$V2_DIR/run_meta.json'))['counters']['s_unmatched_total'])" 2>/dev/null || echo 0)
    echo "$(date '+%H:%M:%S') v1 wall=${V1_WALL}s triples=${V1_TRIPLES} s_unm=${V1_SUNM}" >> "$LOG"
    echo "$(date '+%H:%M:%S') v2 wall=${V2_WALL}s triples=${V2_TRIPLES} s_unm=${V2_SUNM}" >> "$LOG"
fi
echo "$(date '+%H:%M:%S') winner: $WINNER" >> "$LOG"

# ----- Stage 4: full run of winner on 25667 articles, cold cache, concurrency=8 -----
clear_cache
echo "$(date '+%H:%M:%S') launching FULL run: spo_$WINNER on all 25667 articles, --limit 0 --concurrency 8" >> "$LOG"
python3 -u "scripts/run_spo_${WINNER}.py" --limit 0 --concurrency 8 \
    --tag v3_full_25k_seed42 --random --seed 42 >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') === SPO v3 overnight master DONE ===" >> "$LOG"
