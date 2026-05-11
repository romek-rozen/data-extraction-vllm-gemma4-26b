#!/usr/bin/env bash
# Start OBU serwerów vLLM jednocześnie na DGX Spark (GB10, sm_121):
#   - Gemma 4 26B NVFP4   (LLM, generative, /v1/chat/completions na :8001)
#   - Qwen3-Embedding-4B  (embedding, pooling, /v1/embeddings na :8002)
#
# Po co: produkcyjny flow — pipeline ekstrakcji (Gemma) + embedding artykułów
# (Qwen) bez restartów. Spark ma 121 GB unified memory, dwa kontenery koegzystują.
#
# Memory split (gpu-memory-utilization to udział TOTAL pamięci):
#   Gemma  GPU_MEM_LLM=0.60  → ~73 GB (wagi NVFP4 ~13 GB + KV fp8 24k×32seq + activations)
#   Qwen   GPU_MEM_EMB=0.20  → ~24 GB (wagi bf16 ~8 GB + KV bf16 8k + scratch)
#   Suma   0.80              → ~97 GB; zostaje ~25 GB systemowi
#
# UWAGA: start_vllm.sh ma hardkodowane 0.85 — NIE uruchamiamy go tutaj,
# zamiast tego inline'ujemy komendę z naszym GPU_MEM_LLM.
#
# Uruchomienie:
#   bash scripts/start_vllm_llm_plus_embedding.py
#   SIZE=0.6B bash scripts/start_vllm_llm_plus_embedding.py
#   GPU_MEM_LLM=0.55 GPU_MEM_EMB=0.25 bash scripts/...
#
# Logi:    docker logs -f vllm-gemma4
#          docker logs -f vllm-qwen3-embed
# Stop:    docker rm -f vllm-gemma4 vllm-qwen3-embed

set -euo pipefail

# ---- LLM (Gemma 4) ----
LLM_MODEL_DIR="${LLM_MODEL_DIR:-$HOME/models/gemma4-26b-nvfp4-bg}"
LLM_PORT="${LLM_PORT:-8001}"
LLM_NAME="${LLM_NAME:-vllm-gemma4}"
LLM_IMAGE="${LLM_IMAGE:-vllm/vllm-openai:gemma4-cu130}"
GPU_MEM_LLM="${GPU_MEM_LLM:-0.60}"

# ---- Embedding (Qwen3) ----
SIZE="${SIZE:-4B}"
EMB_HF_ID="Qwen/Qwen3-Embedding-${SIZE}"
EMB_MODEL_DIR="${EMB_MODEL_DIR:-$HOME/models/qwen3-embedding-${SIZE,,}}"
EMB_PORT="${EMB_PORT:-8002}"
EMB_NAME="${EMB_NAME:-vllm-qwen3-embed}"
EMB_IMAGE="${EMB_IMAGE:-nvcr.io/nvidia/vllm:26.02-py3}"
GPU_MEM_EMB="${GPU_MEM_EMB:-0.20}"
EMB_MAX_LEN="${EMB_MAX_LEN:-8192}"

# Sanity: oba modele pobrane
for d in "$LLM_MODEL_DIR" "$EMB_MODEL_DIR"; do
  if [[ ! -d "$d" || -z "$(ls -A "$d" 2>/dev/null)" ]]; then
    echo "ERROR: brak modelu w $d"
    exit 1
  fi
done

# Sanity: porty wolne (jeśli zajęte przez nasze kontenery, docker rm rozwiąże)
for p in "$LLM_PORT" "$EMB_PORT"; do
  if ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN; then
    own=$(docker ps --filter "publish=$p" --format '{{.Names}}' 2>/dev/null || true)
    if [[ -z "$own" ]]; then
      echo "ERROR: port $p zajęty przez inny proces (nie nasz docker)."
      exit 1
    fi
  fi
done

# Idempotent restart
docker rm -f "$LLM_NAME" "$EMB_NAME" 2>/dev/null || true

# ---- Start Gemma 4 ----
LLM_PATCH="$LLM_MODEL_DIR/gemma4_patched.py"
if [[ -f "$LLM_PATCH" ]]; then
  PATCH_MOUNT=(-v "$LLM_PATCH:/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/gemma4.py")
  echo "[gemma] patch sm_121: $LLM_PATCH"
else
  PATCH_MOUNT=()
  echo "[gemma] WARN: brak patcha sm_121 ($LLM_PATCH) — startuję bez"
fi

echo "[gemma] start na :$LLM_PORT  GPU_MEM=$GPU_MEM_LLM"
docker run -d --gpus all --ipc=host \
  --name "$LLM_NAME" \
  -v "$LLM_MODEL_DIR":/model \
  "${PATCH_MOUNT[@]}" \
  -p "$LLM_PORT":8000 \
  "$LLM_IMAGE" \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 24576 \
  --max-num-seqs 32 \
  --max-num-batched-tokens 16384 \
  --gpu-memory-utilization "$GPU_MEM_LLM" \
  --moe-backend marlin \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --default-chat-template-kwargs '{"enable_thinking": false}' >/dev/null

# ---- Start Qwen3-Embedding ----
echo "[qwen-embed] start na :$EMB_PORT  GPU_MEM=$GPU_MEM_EMB  size=$SIZE"
# Obraz nvcr.io/nvidia/vllm ma generyczny entrypoint (nvidia_entrypoint.sh),
# więc trzeba jawnie podać `vllm serve` — inaczej exec --model = błąd.
docker run -d --gpus all --ipc=host \
  --name "$EMB_NAME" \
  -v "$EMB_MODEL_DIR":/model \
  -p "$EMB_PORT":8000 \
  "$EMB_IMAGE" \
  vllm serve /model \
  --served-model-name "$EMB_HF_ID" \
  --task embed \
  --dtype bfloat16 \
  --trust-remote-code \
  --gpu-memory-utilization "$GPU_MEM_EMB" \
  --max-model-len "$EMB_MAX_LEN" >/dev/null

echo
echo "[wait] oba kontenery startują, polling /v1/models..."

wait_ready() {
  local name="$1" port="$2" timeout="${3:-600}" t0 now
  t0=$(date +%s)
  while true; do
    # Kontener jeszcze żyje?
    if ! docker ps --filter "name=^${name}$" --format '{{.Names}}' | grep -q "$name"; then
      echo "[FAIL] $name kontener nie działa — ostatnie logi:"
      docker logs --tail 30 "$name" 2>&1 || true
      return 1
    fi
    if curl -sf "http://localhost:$port/v1/models" >/dev/null 2>&1; then
      now=$(date +%s)
      echo "[OK] $name ready po $((now - t0))s na :$port"
      return 0
    fi
    now=$(date +%s)
    if (( now - t0 > timeout )); then
      echo "[TIMEOUT] $name nie wstał w ${timeout}s — ostatnie logi:"
      docker logs --tail 30 "$name" 2>&1 || true
      return 1
    fi
    sleep 3
  done
}

wait_ready "$LLM_NAME" "$LLM_PORT" 600 || exit 2
wait_ready "$EMB_NAME" "$EMB_PORT" 300 || exit 2

cat <<EOF

[DONE] oba serwery online:
  $LLM_NAME       :$LLM_PORT  ($LLM_MODEL_DIR, NVFP4, GPU_MEM=$GPU_MEM_LLM)
  $EMB_NAME  :$EMB_PORT  ($EMB_HF_ID, bf16, GPU_MEM=$GPU_MEM_EMB)

Smoke:
  bash scripts/smoke_test.sh       # Gemma chat + JSON mode
  curl http://localhost:$EMB_PORT/v1/embeddings \\
    -H 'Content-Type: application/json' \\
    -d '{"model":"$EMB_HF_ID","input":["hello world"]}' | head -c 300

Stop:
  docker rm -f $LLM_NAME $EMB_NAME
EOF
