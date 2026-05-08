#!/bin/bash
# Sequential SPO v1 → v2 full benchmark on hot cache.
#
# Why sequential rather than parallel (run_spo_v1_v2_test.py):
#   - Clean per-pipeline wall_s measurement (no shared-GPU noise) for
#     ETA extrapolation onto RTX 6000 Pro.
#   - Maximum prefix-cache hit rate per pipeline (single set of system
#     prompts in flight at a time = vLLM cache untouched by the other pipeline).
#   - First pipeline (v1) results visible at ~t+28h instead of ~t+57h.
#   - Failure isolation — a crash in v1 doesn't poison v2.
#
# Assumes:
#   - websites_cache/ already populated (build via stream_articles_async ProcessPool 64w
#     for ~93s on Spark; see PLANS/production_deployment.md).
#   - vLLM Gemma 4 26B running on localhost:8001.
#
# Usage:
#   bash scripts/run_spo_v1_v2_sequential.sh                 # full sample (--limit 0)
#   LIMIT=1000 bash scripts/run_spo_v1_v2_sequential.sh      # smaller sample
#   CONC=8 bash scripts/run_spo_v1_v2_sequential.sh          # default; or CONC=6 etc.
#   TAG=v3_seq bash scripts/run_spo_v1_v2_sequential.sh      # custom tag suffix

set -u

REPO=/home/spark001/Spark-testy/mateusz-g-two-step-vllm
cd "$REPO" || exit 99

LIMIT=${LIMIT:-0}
CONC=${CONC:-8}
TAG=${TAG:-v3_seq}
SEED=${SEED:-42}

# Master log + dir for cross-run artifacts.
TS=$(date '+%Y-%m-%d_%H-%M-%S')
MASTER_DIR="$REPO/final_results/${TS}__spo_v1_v2_seq_${TAG}"
mkdir -p "$MASTER_DIR"
LOG="$MASTER_DIR/run_log.txt"

echo "$(date '+%Y-%m-%d %H:%M:%S') === SPO v1 → v2 SEQUENTIAL master start ===" | tee -a "$LOG"
echo "  master_dir = $MASTER_DIR" | tee -a "$LOG"
echo "  LIMIT=$LIMIT  CONC=$CONC  TAG=$TAG  SEED=$SEED" | tee -a "$LOG"
echo "  cache files = $(ls $REPO/websites_cache 2>/dev/null | wc -l)" | tee -a "$LOG"

# ----- Stage 1 — run_spo_v1.py (cram, single-call entities + rich SPO) -----
echo "$(date '+%H:%M:%S') stage1: launching run_spo_v1.py --concurrency $CONC --limit $LIMIT --random --seed $SEED --tag $TAG" | tee -a "$LOG"
T1_START=$(date +%s)
python3 -u "$REPO/scripts/run_spo_v1.py" \
    --limit "$LIMIT" --concurrency "$CONC" \
    --random --seed "$SEED" --tag "$TAG" \
    --no-summary 2>&1 | tee -a "$MASTER_DIR/v1_subproc.log"
T1_END=$(date +%s)
T1_ELAPSED=$((T1_END - T1_START))
echo "$(date '+%H:%M:%S') stage1: v1 done in ${T1_ELAPSED}s ($(echo "scale=2; $T1_ELAPSED/3600" | bc)h)" | tee -a "$LOG"

# Locate v1 output dir — newest matching tag created since this script started.
V1_DIR=$(ls -dt "$REPO"/final_results/*__spo_v1_${TAG} 2>/dev/null | head -1)
echo "$(date '+%H:%M:%S') stage1: V1_DIR=$V1_DIR" | tee -a "$LOG"
echo "$V1_DIR" > "$MASTER_DIR/v1_dir.txt"

# ----- Stage 2 — run_spo_v2.py (split: entities_only + spo_pipe + meta + sponsored) -----
echo "$(date '+%H:%M:%S') stage2: launching run_spo_v2.py --concurrency $CONC --limit $LIMIT --random --seed $SEED --tag $TAG" | tee -a "$LOG"
T2_START=$(date +%s)
python3 -u "$REPO/scripts/run_spo_v2.py" \
    --limit "$LIMIT" --concurrency "$CONC" \
    --random --seed "$SEED" --tag "$TAG" \
    --no-summary 2>&1 | tee -a "$MASTER_DIR/v2_subproc.log"
T2_END=$(date +%s)
T2_ELAPSED=$((T2_END - T2_START))
echo "$(date '+%H:%M:%S') stage2: v2 done in ${T2_ELAPSED}s ($(echo "scale=2; $T2_ELAPSED/3600" | bc)h)" | tee -a "$LOG"

V2_DIR=$(ls -dt "$REPO"/final_results/*__spo_v2_${TAG} 2>/dev/null | head -1)
echo "$(date '+%H:%M:%S') stage2: V2_DIR=$V2_DIR" | tee -a "$LOG"
echo "$V2_DIR" > "$MASTER_DIR/v2_dir.txt"

# ----- Stage 3 — comparison report -----
echo "$(date '+%H:%M:%S') stage3: generating v1 vs v2 comparison report" | tee -a "$LOG"
python3 "$REPO/scripts/spo_compare_benches.py" \
    --tag "$TAG" \
    --output "$MASTER_DIR/comparison_report.md" \
    >> "$LOG" 2>&1 || echo "$(date '+%H:%M:%S') compare report failed (non-fatal)" >> "$LOG"

TOTAL=$((T2_END - T1_START))
echo "$(date '+%Y-%m-%d %H:%M:%S') === MASTER DONE total=${TOTAL}s ($(echo "scale=2; $TOTAL/3600" | bc)h) ===" | tee -a "$LOG"
echo "  v1 wall: ${T1_ELAPSED}s" | tee -a "$LOG"
echo "  v2 wall: ${T2_ELAPSED}s" | tee -a "$LOG"
echo "  master_dir: $MASTER_DIR" | tee -a "$LOG"
