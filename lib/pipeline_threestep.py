"""Three-step pipeline (D7c): classify → (meta || entities) z early-exit na junku.

Re-używa `lib.pipeline.process_step1` dla encji oraz `process_step2` dla SEO meta —
nic nie modyfikuje, tylko dokłada `process_classify` i lekki `build_classify_user`.

Decyzja architektoniczna (D7c): osobny lekki klasyfikator pozwala na junk-skip
*przed* drogim Step 1, bez utraty jakości na non-junku (entities/meta lecą tym
samym promptem v6 co two-step default).
"""

from datetime import datetime
from typing import Any

from lib.pipeline import process_step1, process_step2  # noqa: F401  (re-export dla orchestrator)
from lib.vllm_client import VLLMClient


def build_classify_user(article_text: str) -> str:
    return f"""Classify the article below — pick ONE category and detect language.

<article>
{article_text}
</article>"""


def process_classify(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    """Krótki classifier: zwraca {language, category}. Output ~20 tok.

    Rekord finalny ma kompatybilną szatę z process_step1 (te same pola idempotencji)
    plus `is_junk: bool` wyliczone z `category == "junkey"`.
    """
    user = build_classify_user(article["text"])
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="classify",
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
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if res["ok"] and res["parsed"]:
        cat = res["parsed"].get("category")
        record.update({
            "category": cat,
            "language": res["parsed"].get("language"),
            "is_junk": cat == "junkey",
        })
    return record


def make_junk_stub_final(article: dict, classify_record: dict) -> dict:
    """Krótka ścieżka dla junku — zapis do final.jsonl bez encji i meta."""
    return {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "category": classify_record.get("category"),
        "language": classify_record.get("language"),
        "is_junk": True,
        "entities": [],
        "title": "",
        "meta_description": "",
        "h1": "",
        "article_summary": "",
        "ok": True,
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "skipped_reason": "junk_classified",
    }


def join_final(
    article: dict,
    classify_record: dict,
    entities_record: dict | None,
    meta_record: dict | None,
) -> dict:
    """Złóż final.jsonl rekord z 3 etapów. classify-only (junk) idzie make_junk_stub_final."""
    entities = (entities_record or {}).get("entities", []) if entities_record and entities_record.get("ok") else []
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "category": classify_record.get("category"),
        "language": classify_record.get("language"),
        "is_junk": False,
        "entities": entities,
        "ok": bool(entities_record and entities_record.get("ok")) and bool(meta_record and meta_record.get("ok")),
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if meta_record and meta_record.get("ok"):
        for k in ("title", "meta_description", "h1", "article_summary"):
            out[k] = meta_record.get(k, "")
    else:
        out["title"] = ""
        out["meta_description"] = ""
        out["h1"] = ""
        out["article_summary"] = ""
        if not (meta_record and meta_record.get("ok")):
            out["error"] = "meta_failed"
        if not (entities_record and entities_record.get("ok")):
            out["error"] = "entities_failed" if not out.get("error") else f"{out['error']}+entities_failed"
    return out
