"""Three-step pipeline v2 (D7c iteracja 2):

- Stage 1 (junk classifier): truncated 1000-char input, binary `1`/`0` output
  (vLLM `guided_choice`, NO JSON), prompt z few-shot examples.
- Stage 2 (meta): full text → {language, category, title, meta_description, h1, article_summary}.
- Stage 3 (entities): full text → {entities: [{name, type}]}.

Stage 2 i 3 lecą RÓWNOLEGLE dla non-junk URL.

Re-używa `lib.vllm_client.VLLMClient` dla meta + entities (klasyczny chat_json).
Dla classifier'a robi raw POST — vLLM `guided_choice` (xgrammar) wymusza jeden
z dwóch tokenów `0` / `1`. To omija nadgarstek tokenów JSON.

Nie modyfikuje istniejących plików. Schemy + prompty: wszystkie z sufiksem `_v2`.
"""

import logging
import time
from datetime import datetime
from typing import Any

import requests

from lib.pipeline import dedup_entities, enrich_entity
from lib.vllm_client import VLLMClient

logger = logging.getLogger(__name__)

CLASSIFY_INPUT_MAX_CHARS = 1000


def _truncate_for_classify(text: str) -> str:
    return (text or "")[:CLASSIFY_INPUT_MAX_CHARS]


def call_junk_classifier_binary(
    base_url: str,
    model: str,
    system_prompt: str,
    article_text: str,
    timeout: float = 60.0,
    max_retries_network: int = 2,
) -> dict[str, Any]:
    """Raw POST do vLLM z guided_choice ['0','1'] — wymusza jeden token wyboru.

    Retry-only-on-network: timeout/ConnectionError/HTTPError 5xx. Output to 1 tok,
    quality retry nie ma sensu. Output: {ok, is_junk, raw, latency_s, usage, error, attempts}.
    """
    snippet = _truncate_for_classify(article_text)
    user = f"INPUT:\n```\n{snippet}\n```\nOUTPUT:"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ],
        "max_tokens": 4,
        "temperature": 0.1,
        "top_p": 0.95,
        "top_k": 1,
        "guided_choice": ["0", "1"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last_err = None
    for attempt in range(max_retries_network + 1):
        t0 = time.perf_counter()
        try:
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=timeout)
            latency = time.perf_counter() - t0
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            is_junk = content.startswith("1")
            return {
                "ok": True,
                "is_junk": is_junk,
                "raw": content,
                "latency_s": latency,
                "usage": data.get("usage", {}),
                "error": None,
                "finish_reason": data["choices"][0].get("finish_reason"),
                "attempts": attempt + 1,
            }
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"network_attempt_{attempt}: {e}"
            logger.warning(f"classifier network err, retry {attempt+1}/{max_retries_network}: {e}")
            time.sleep(1.0 * (attempt + 1))
        except requests.HTTPError as e:
            status = getattr(r, "status_code", 0)
            if status >= 500 and attempt < max_retries_network:
                last_err = f"http_{status}_attempt_{attempt}: {e}"
                time.sleep(1.0 * (attempt + 1))
                continue
            return {
                "ok": False, "is_junk": False, "raw": None,
                "latency_s": time.perf_counter() - t0, "usage": {},
                "error": f"classifier_http_{status}: {e}",
                "finish_reason": None, "attempts": attempt + 1,
            }
    return {
        "ok": False, "is_junk": False, "raw": None,
        "latency_s": 0.0, "usage": {},
        "error": f"classifier_network_exhausted: {last_err}",
        "finish_reason": None, "attempts": max_retries_network + 1,
    }


def process_classify_v2(
    base_url: str,
    model: str,
    system_prompt: str,
    article: dict,
) -> dict:
    res = call_junk_classifier_binary(base_url, model, system_prompt, article["text"])
    return {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "path": article["path"],
        "text_tokens": article["text_tokens"],
        "ok": res["ok"],
        "error": res["error"],
        "is_junk": res["is_junk"],
        "raw": res["raw"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def process_meta_v2(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    user = f"""Generate SEO meta and classify the article.

<article>
{article['text']}
</article>"""
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="meta_v2",
        max_tokens=max_tokens,
        **sampling,
    )
    rec = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if res["ok"] and res["parsed"]:
        for k in ("language", "category", "title", "meta_description", "h1", "article_summary"):
            rec[k] = res["parsed"].get(k, "")
    return rec


def process_entities_v2(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    user = f"""Extract entities from the article below.

<article>
{article['text']}
</article>"""
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="entities_v2",
        max_tokens=max_tokens,
        **sampling,
    )
    rec = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if res["ok"] and res["parsed"]:
        raw = res["parsed"].get("entities", [])
        deduped = dedup_entities(raw)
        rec["entities"] = [enrich_entity(e) for e in deduped]
        rec["entities_raw_count"] = len(raw)
    else:
        rec["entities"] = []
        rec["entities_raw_count"] = 0
    return rec


def make_junk_stub_final_v2(article: dict, classify_record: dict) -> dict:
    return {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": True,
        "category": "junkey",
        "language": "",
        "title": "",
        "meta_description": "",
        "h1": "",
        "article_summary": "",
        "entities": [],
        "ok": True,
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "skipped_reason": "junk_classified",
    }


def join_final_v2(
    article: dict,
    classify_record: dict,
    meta_record: dict | None,
    entities_record: dict | None,
) -> dict:
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": False,
        "ok": bool(meta_record and meta_record.get("ok")) and bool(entities_record and entities_record.get("ok")),
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if meta_record and meta_record.get("ok"):
        for k in ("language", "category", "title", "meta_description", "h1", "article_summary"):
            out[k] = meta_record.get(k, "")
    else:
        for k in ("language", "category", "title", "meta_description", "h1", "article_summary"):
            out[k] = ""
        out["error"] = "meta_failed"
    if entities_record and entities_record.get("ok"):
        out["entities"] = entities_record.get("entities", [])
    else:
        out["entities"] = []
        out["error"] = "entities_failed" if not out.get("error") else f"{out['error']}+entities_failed"
    return out
