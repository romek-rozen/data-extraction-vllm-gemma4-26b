"""Konfiguracja eksperymentu two-step vLLM."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

WEBSITES_DIR = PROJECT_ROOT / "websites"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
RESULT_DIR = PROJECT_ROOT / "result"

# Twardy limit znaków (safety net przed tokenizacją). 80k ≈ ~20k tokenów —
# znacznie powyżej max obserwowanego (5979) z Phase 1. Dla outlierów odsiewa
# patologiczne strony bez kosztu tokenizacji.
TEXT_TRUNCATE_LIMIT = 80000

# Limit tokenów artykułu — twardy budżet pod max-model-len 32768:
# 32768 - system_prompt (~2929) - user_template (37) - output (4000) - bufor (~800) ≈ 25000.
# Phase 1 max obserwowany artykuł = 5979 tok → 25000 to ~4× headroom dla outlierów.
MAX_ARTICLE_TOKENS = 25000

# vLLM server (OpenAI-compat)
VLLM_BASE_URL = "http://localhost:8001/v1"
VLLM_MODEL = "/model"  # ścieżka mountowana w kontenerze, override przez env

# Sampling — Phase 3 walidacja (decyzja D12 w DECISIONS.md):
# - Step 1: A (Google default) — schema xgrammar dominuje, temperatura nie wpływa
# - Step 2: B (0.8) — eliminuje rzadkie zapętlenia (1/100 przy temp 1.0)
SAMPLING_STEP1 = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 64,
    "repetition_penalty": 1.0,  # NIE 1.2 — łamie powtarzające się klucze JSON
}
SAMPLING_STEP2 = {
    "temperature": 0.8,
    "top_p": 0.9,
    "top_k": 50,
    "repetition_penalty": 1.0,
}
# Backward compat — wcześniejszy kod używał SAMPLING_DEFAULT
SAMPLING_DEFAULT = SAMPLING_STEP1

MAX_TOKENS_STEP1 = 4000   # v6: bez metadata, ale długie artykuły potrafią mieć 50+ encji × ~10-15 tok = 500-750; bufor 4000 dla outlierów (input 18k + output 4k = 22k < 24576 max-model-len).
MAX_TOKENS_STEP2 = 2000   # title+meta+h1+summary realnie ~250-350 tok (max obserwowane v6: 214). Bufor 2000 daje modelowi powietrze; retry-with-feedback łapie ewentualne patologiczne loopy.

# Twardy sufit output tokenów (safety) — dla edge case'ów gdzie model próbowałby
# wygenerować dużo tekstu (np. junkey, niespójny artykuł). Per faktyczny request
# używamy MAX_TOKENS_STEP{1,2}; NUM_PREDICT to ceiling dla nietypowych ścieżek
# (np. one-step baseline, eksperymenty bez schematu).
NUM_PREDICT = 4096
