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

# Limit tokenów artykułu — twardy budżet pod max-model-len 24576:
# 24576 - system prompt (max 2929 Step 1) - user template (37) - output (400) - bufor (1210) ≈ 20000.
# Z Phase 1: max artykuł = 5979 tokenów. 20000 to ~3,3× headroom dla outlierów.
MAX_ARTICLE_TOKENS = 20000

# vLLM server (OpenAI-compat)
VLLM_BASE_URL = "http://localhost:8000/v1"
VLLM_MODEL = "/model"  # ścieżka mountowana w kontenerze, override przez env

# Sampling — Google defaults dla Gemma 4 (INSTRUCTIONS_FROM_CLAUDE.md sekcja "Sampling parameters")
SAMPLING_DEFAULT = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 64,
    "repetition_penalty": 1.0,  # NIE 1.2 — łamie powtarzające się klucze JSON
}

MAX_TOKENS_STEP1 = 400
MAX_TOKENS_STEP2 = 300

# Twardy sufit output tokenów (safety) — dla edge case'ów gdzie model próbowałby
# wygenerować dużo tekstu (np. junkey, niespójny artykuł). Per faktyczny request
# używamy MAX_TOKENS_STEP{1,2}; NUM_PREDICT to ceiling dla nietypowych ścieżek
# (np. one-step baseline, eksperymenty bez schematu).
NUM_PREDICT = 4096
