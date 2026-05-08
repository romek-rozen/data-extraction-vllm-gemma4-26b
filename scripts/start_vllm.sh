#!/usr/bin/env bash
# Start vLLM serwera z Gemma 4 26B A4B NVFP4 na DGX Spark.
#
# Bazuje na oficjalnym przewodniku NVIDIA dla DGX Spark + Gemma 4:
#   https://catalog.ngc.nvidia.com/orgs/nvidia/containers/vllm
#   docker pull vllm/vllm-openai:gemma4-cu130
#   docker run -it --gpus all -p 8000:8000 vllm/vllm-openai:gemma4-cu130 ${HF_MODEL_HANDLE}
#
# Różnice względem domyślnej komendy:
# - lokalna ścieżka modelu zamiast HF handle (model już pobrany, brak need HF_TOKEN w kontenerze)
# - port hosta 8001 (na Sparku 8000 zajęty przez open-terminal)
# - mount patcha gemma4_patched.py (sm_121 fix)
# - flagi prod z INSTRUCTIONS_FROM_CLAUDE.md (Phase 0):
#     --moe-backend marlin   (FP4 fallback dla sm_121)
#     --max-model-len 32768  (32k — nadmiarowy bufor dla długich artykułów + Step 3 SPO triplets w przyszłości)
#     --enable-prefix-caching
#     --kv-cache-dtype fp8   (2× batch względem BF16)
#     --gpu-memory-utilization 0.85
#     --default-chat-template-kwargs '{"enable_thinking": false}'  (Gemma 4 thinking OFF; per-request override przez chat_template_kwargs w body)
#
# UWAGA: NIE używamy --reasoning-parser gemma4 — łączenie tej flagi z enable_thinking=false
# wyłącza xgrammar (structured output) cicho. Patrz vLLM issue #39130.
# Nasze guided_json wymaga xgrammar, więc reasoning-parser zostaje wyłączony.
#
# Uruchomienie:  bash scripts/start_vllm.sh
# Logi:          docker logs -f vllm-gemma4
# Health:        curl http://localhost:8001/v1/models

set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/models/gemma4-26b-nvfp4-bg}"
HOST_PORT="${HOST_PORT:-8001}"
CONTAINER_NAME="${CONTAINER_NAME:-vllm-gemma4}"
IMAGE="${IMAGE:-vllm/vllm-openai:gemma4-cu130}"

# Patch sm_121 — leży w katalogu modelu bg-digitalservices
PATCH_PATH="$MODEL_DIR/gemma4_patched.py"
if [[ -f "$PATCH_PATH" ]]; then
  PATCH_MOUNT=(-v "$PATCH_PATH:/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/gemma4.py")
  echo "Patch sm_121 znaleziony: $PATCH_PATH"
else
  PATCH_MOUNT=()
  echo "WARN: brak patcha sm_121 w $PATCH_PATH — startuję bez patcha"
fi

# Zatrzymaj poprzedni kontener jeśli istnieje (przed sprawdzeniem portu — żeby restart działał)
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Sprawdź czy port jest wolny (jeśli zajęty przez COŚ INNEGO niż nasz kontener)
if ss -ltn "sport = :$HOST_PORT" 2>/dev/null | grep -q LISTEN; then
  echo "ERROR: port $HOST_PORT jest zajęty przez inny proces. Ustaw HOST_PORT=<inny> i ponów."
  exit 1
fi

docker run -d --gpus all --ipc=host \
  --name "$CONTAINER_NAME" \
  -v "$MODEL_DIR":/model \
  "${PATCH_MOUNT[@]}" \
  -p "$HOST_PORT":8000 \
  "$IMAGE" \
  --model /model \
  --quantization modelopt \
  --kv-cache-dtype fp8 \
  --max-model-len 32768 \
  --max-num-seqs 8 \
  --gpu-memory-utilization 0.75 \
  --moe-backend marlin \
  --enable-prefix-caching \
  --default-chat-template-kwargs '{"enable_thinking": false}'

cat <<EOF

Kontener $CONTAINER_NAME wystartowany.
Port:    $HOST_PORT  (mapowane na 8000 w kontenerze)
Model:   $MODEL_DIR
Image:   $IMAGE

Czekaj na "Application startup complete" w logach:
  docker logs -f $CONTAINER_NAME

Healthcheck (po starcie, ~1-3 min):
  curl http://localhost:$HOST_PORT/v1/models

Smoke test:
  bash scripts/smoke_test.sh

Stop:
  docker rm -f $CONTAINER_NAME
EOF
