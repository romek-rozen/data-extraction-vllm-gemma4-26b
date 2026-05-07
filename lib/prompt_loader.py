"""Ładowanie promptów + schematów + budowanie user messages."""

import json
from functools import lru_cache
from pathlib import Path

from lib.config import PROMPTS_DIR


@lru_cache(maxsize=8)
def load_system_prompt(name: str) -> str:
    """Wczytaj system prompt z prompts/<name>.md (cache'owane)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def load_schema(name: str) -> dict:
    """Wczytaj JSON Schema z prompts/<name>.json."""
    return json.loads((PROMPTS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def build_step1_user(article_text: str) -> str:
    return f"""Analyze the article below and extract structured data:

<article>
{article_text}
</article>"""


def build_step2_user(
    article_text: str,
    detected_language: str,
    category: str,
    entities: list[dict],
) -> str:
    """Step 2 user prompt — context z pipe note (Step 1 output)."""
    entities_summary = ", ".join(e["name"] for e in entities[:10])
    return f"""Generate SEO meta data in language: {detected_language}

Category: {category}
Key entities: {entities_summary}

<article>
{article_text}
</article>"""
