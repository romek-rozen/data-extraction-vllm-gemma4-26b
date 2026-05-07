"""Logika per-artykuł dla Step 1 i Step 2 (importowalna z różnych skryptów).

Wydzielone z run_step1.py / run_step2.py żeby ab_sampling.py mógł reużywać
bez duplikacji logiki.
"""

from typing import Any

from lib.prompt_loader import build_step1_user, build_step2_user
from lib.vllm_client import VLLMClient


def process_step1(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    user = build_step1_user(article["text"])
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="step1",
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
    }
    if res["ok"] and res["parsed"]:
        record.update({
            "category": res["parsed"].get("category"),
            "language": res["parsed"].get("language"),
            "entities": res["parsed"].get("entities", []),
        })
    return record


def process_step2(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    entity_record: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    if not entity_record.get("ok"):
        return {
            "url_hash": article["url_hash"],
            "id": article["id"],
            "ok": False,
            "error": "step1_failed",
        }
    user = build_step2_user(
        article_text=article["text"],
        detected_language=entity_record.get("language") or "en",
        category=entity_record.get("category") or "Other themes",
        entities=entity_record.get("entities") or [],
    )
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="step2",
        max_tokens=max_tokens,
        **sampling,
    )
    record = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "category": entity_record.get("category"),
        "language": entity_record.get("language"),
        "entities": entity_record.get("entities", []),
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
    }
    if res["ok"] and res["parsed"]:
        record.update(res["parsed"])
    return record
