"""Phase 1.5: pomiar tokenów promptów (system + user templates).

System prompt to największy stały koszt per request — ale jest cache'owany
(prefix caching), więc liczy się raz na batch, nie per artykuł.
User template to dodatek per request.

Łączny budżet input = system_tokens + user_template_tokens + article_tokens.

Użycie:
    python3 scripts/measure_prompt_tokens.py
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import PROMPTS_DIR  # noqa: E402

VLLM_URL = "http://localhost:8001"
MODEL = "/model"


def count_tokens(text: str) -> int:
    r = requests.post(
        f"{VLLM_URL}/tokenize",
        json={"model": MODEL, "prompt": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["count"]


# User templates z INSTRUCTIONS_FROM_CLAUDE.md (sekcje "User prompt")
USER_TEMPLATE_STEP1 = """Analyze the article below and extract structured data:

<article>
{article_text}
</article>"""

USER_TEMPLATE_STEP2 = """Generate SEO meta data in language: {detected_language}

Category: {category}
Key entities: {entities_summary}

<article>
{article_text}
</article>"""


def main():
    step1_sys = (PROMPTS_DIR / "step1_system.md").read_text()
    step2_sys = (PROMPTS_DIR / "step2_system.md").read_text()

    # tokeny szablonów BEZ podstawienia (z placeholderami) — to jest stały overhead
    step1_template_empty = USER_TEMPLATE_STEP1.format(article_text="")
    step2_template_empty = USER_TEMPLATE_STEP2.format(
        detected_language="pl",
        category="Health, Medicine",
        entities_summary="witamina D, witamina D3, suplementacja",
        article_text="",
    )

    pieces = {
        "step1_system": step1_sys,
        "step2_system": step2_sys,
        "step1_user_template (pusty article)": step1_template_empty,
        "step2_user_template (pusty article + przykładowy context)": step2_template_empty,
    }

    print(f"{'piece':<55} {'chars':>8} {'tokens':>8}")
    print("-" * 75)
    counts = {}
    for name, text in pieces.items():
        n = count_tokens(text)
        counts[name] = n
        print(f"{name:<55} {len(text):>8} {n:>8}")

    print()
    article_median_tokens = 1247
    article_p95_tokens = 4735
    article_max_tokens = 5979

    step1_total_median = counts["step1_system"] + counts["step1_user_template (pusty article)"] + article_median_tokens
    step1_total_p95 = counts["step1_system"] + counts["step1_user_template (pusty article)"] + article_p95_tokens
    step1_total_max = counts["step1_system"] + counts["step1_user_template (pusty article)"] + article_max_tokens

    step2_total_median = counts["step2_system"] + counts["step2_user_template (pusty article + przykładowy context)"] + article_median_tokens
    step2_total_p95 = counts["step2_system"] + counts["step2_user_template (pusty article + przykładowy context)"] + article_p95_tokens
    step2_total_max = counts["step2_system"] + counts["step2_user_template (pusty article + przykładowy context)"] + article_max_tokens

    print(f"=== Łączny budżet input (system + user + article) ===")
    print(f"{'':35} {'median':>8} {'p95':>8} {'max':>8}")
    print(f"{'Step 1 input tokens':<35} {step1_total_median:>8} {step1_total_p95:>8} {step1_total_max:>8}")
    print(f"{'Step 2 input tokens':<35} {step2_total_median:>8} {step2_total_p95:>8} {step2_total_max:>8}")
    print()
    print(f"max_model_len = 24576")
    print(f"Step 1: bufor po max+output 400 = {24576 - step1_total_max - 400} tokenów")
    print(f"Step 2: bufor po max+output 300 = {24576 - step2_total_max - 300} tokenów")
    print()
    print("UWAGA: system prompt jest CACHE'OWANY (prefix caching) — liczy się raz na warmup,")
    print("a nie 21M razy. Ale BUDŻET MAX_MODEL_LEN to twardy limit per request.")


if __name__ == "__main__":
    main()
