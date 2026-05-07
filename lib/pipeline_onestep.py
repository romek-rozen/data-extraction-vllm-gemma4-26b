"""One-step pipeline: ekstrakcja encji + język + kategoria + SEO meta w jednym zapytaniu.

Zaprojektowane jako test porównawczy do two-step (lib/pipeline.py). Reużywa
istniejących modułów: VLLMClient, prompt_loader, schemat enum mappings z
lib/pipeline.py (TYPE_TO_CATEGORY, dedup_entities, enrich_entity).

NIE modyfikuje istniejącego two-step — tylko dodaje równoległą ścieżkę.
"""

from typing import Any

from lib.pipeline import dedup_entities, enrich_entity
from lib.vllm_client import VLLMClient


def build_onestep_user(article_text: str) -> str:
    return f"""Analyze the article below and return ONE JSON object with:
extracted entities + detected language + category + SEO meta (title, meta_description, h1, article_summary).
SEO meta must be in the same language as the article.

<article>
{article_text}
</article>"""


def process_onestep(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    """Jeden call vLLM → cały output. Zwraca rekord kompatybilny strukturalnie z final.jsonl."""
    user = build_onestep_user(article["text"])
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="onestep",
        max_tokens=max_tokens,
        **sampling,
    )
    record = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "path": article["path"],
        "text_tokens": article["text_tokens"],
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
    }
    if res["ok"] and res["parsed"]:
        parsed = res["parsed"]
        raw_entities = parsed.get("entities", [])
        deduped = dedup_entities(raw_entities)
        enriched = [enrich_entity(e) for e in deduped]
        record.update({
            "language": parsed.get("language"),
            "category": parsed.get("category"),
            "entities": enriched,
            "entities_raw_count": len(raw_entities),
            "title": parsed.get("title"),
            "meta_description": parsed.get("meta_description"),
            "h1": parsed.get("h1"),
            "article_summary": parsed.get("article_summary"),
        })
    return record
