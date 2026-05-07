"""Konfiguracja eksperymentu two-step vLLM."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

WEBSITES_DIR = PROJECT_ROOT / "websites"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
RESULT_DIR = PROJECT_ROOT / "result"

# Truncate ekstrahowanego markdownu (znaki). 80k ≈ ~20k tokenów; bufor pod max-model-len 24576
# (zostawiamy ~4k na system prompt + output).
TEXT_TRUNCATE_LIMIT = 80000

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
