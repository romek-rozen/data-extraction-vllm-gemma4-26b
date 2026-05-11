#!/usr/bin/env bash
# Smoke test serwera Qwen3-Embedding (vllm-qwen3-embed na :8002).
# Sprawdza: /v1/models, single embed, batch embed, sanity cosine similarity.

set -euo pipefail

HOST_PORT="${HOST_PORT:-8002}"
MODEL="${MODEL:-Qwen/Qwen3-Embedding-4B}"
BASE_URL="http://localhost:$HOST_PORT/v1"

echo "=== /v1/models ==="
curl -sS "$BASE_URL/models" | python3 -m json.tool || {
  echo "ERROR: serwer nie odpowiada na $BASE_URL/models"
  exit 1
}

echo
echo "=== single embed (hello world) ==="
curl -sS "$BASE_URL/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"input\":[\"hello world\"]}" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
e = d['data'][0]['embedding']
print(f'  dim       = {len(e)}')
print(f'  first 5   = {[round(x, 4) for x in e[:5]]}')
print(f'  usage     = {d.get(\"usage\")}')
assert len(e) == 2560, f'expected dim 2560, got {len(e)}'
print('  OK')
"

echo
echo "=== batch embed (3 inputs) ==="
curl -sS "$BASE_URL/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"input\":[\"witamina D wspiera odporność\",\"vitamin D supports immunity\",\"kupiłem nowy samochód\"]}" \
  | python3 -c "
import json, sys, math
d = json.load(sys.stdin)
embs = [item['embedding'] for item in d['data']]
print(f'  batch size = {len(embs)}')
print(f'  dim        = {len(embs[0])}')
print(f'  usage      = {d.get(\"usage\")}')

def cos(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb)

sim_pl_en = cos(embs[0], embs[1])  # PL/EN to samo znaczenie
sim_pl_pl = cos(embs[0], embs[2])  # PL vs PL inny temat
print(f'  cos(witamina PL,  vitamin EN)   = {sim_pl_en:.4f}')
print(f'  cos(witamina PL,  samochód PL)  = {sim_pl_pl:.4f}')
assert sim_pl_en > sim_pl_pl, 'cross-lingual semantic similarity should beat unrelated PL pair'
print('  OK — cross-lingual semantic similarity działa')
"

echo
echo "[DONE] embedding server smoke OK"
