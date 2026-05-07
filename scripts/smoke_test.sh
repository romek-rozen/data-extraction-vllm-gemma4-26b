#!/usr/bin/env bash
# Smoke test vLLM po starcie kontenera.
# Wywołuje /v1/models i prosty /v1/chat/completions z testem matematycznym.

set -euo pipefail

HOST_PORT="${HOST_PORT:-8001}"
BASE_URL="http://localhost:$HOST_PORT/v1"

echo "=== /v1/models ==="
curl -sS "$BASE_URL/models" | python3 -m json.tool || {
  echo "ERROR: serwer nie odpowiada na $BASE_URL/models"
  exit 1
}

echo
echo "=== /v1/chat/completions (12*17 = ?) ==="
curl -sS "$BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "/model",
    "messages": [{"role": "user", "content": "12*17 = ? Answer with just the number."}],
    "max_tokens": 20,
    "temperature": 1.0,
    "chat_template_kwargs": {"enable_thinking": false}
  }' | python3 -m json.tool

echo
echo "=== Test JSON extraction (Step 1 sanity) ==="
curl -sS "$BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "/model",
    "messages": [
      {"role": "system", "content": "Return ONLY a JSON object with field language (ISO 639-1)."},
      {"role": "user", "content": "Witamina D wspiera odporność."}
    ],
    "max_tokens": 50,
    "temperature": 1.0,
    "response_format": {"type": "json_object"},
    "chat_template_kwargs": {"enable_thinking": false}
  }' | python3 -m json.tool
