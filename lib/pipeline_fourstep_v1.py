"""Four-step pipeline v1: classify → (meta || entities || sponsored).

Architektura: classify pierwszy (binary junk, truncated input). Po classify dla
non-junk URL równolegle lecą TRZY niezależne LLM calls (meta, entities, sponsored).

Decyzja architektoniczna: sponsored detection to OSOBNY etap, nie część meta — bo
łączenie SEO-generation z sponsored-classification w jednym promptcie rozmydla
model (testowane wcześniej w v6 promptach: dodawanie kolejnych zadań pogarsza
jakość każdego z nich). Każdy etap ma jedno zadanie, jeden tryb cognitive.

Reuse: lib.pipeline_threestep_v2.{process_classify_v2, process_meta_v2,
process_entities_v2, make_junk_stub_final_v2}. Nie modyfikujemy istniejących
plików — tylko dorzucamy `process_sponsored_v1` + `join_final_v4`.
"""

from datetime import datetime
from typing import Any

from lib.pipeline_threestep_v2 import (  # noqa: F401  re-eksport dla orchestratora
    make_junk_stub_final_v2,
    process_classify_v2,
    process_entities_v2,
    process_meta_v2,
)
from lib.vllm_client import VLLMClient


def process_sponsored_v1(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    """Wywołaj LLM klasyfikator sponsored. Output: {sponsored, subtype, justification}.

    Przekazuje `PUBLISHER DOMAIN` do user-prompta — bez tego model nie odróżnia
    internal-links (właściciel promuje swój sklep) od external paid placement.
    """
    domain = (article.get("domain") or "").lower().strip()
    user = f"""Classify the article below.

PUBLISHER DOMAIN: {domain}
Links to {domain} (and its subdomains) are INTERNAL — publisher's own pages.
INTERNAL links/mentions = NOT third-party sponsored (publisher's own commercial content).
Only links/mentions of OTHER domains count as third-party sponsored signals.

<article>
{article['text']}
</article>"""
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="sponsored_v1",
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
        rec["sponsored"] = bool(res["parsed"].get("sponsored", False))
        rec["sponsored_subtype"] = res["parsed"].get("sponsored_subtype")
        rec["sponsored_justification"] = res["parsed"].get("sponsored_justification", "")
    else:
        rec["sponsored"] = False
        rec["sponsored_subtype"] = None
        rec["sponsored_justification"] = ""
    return rec


def make_junk_stub_final_v4(article: dict, classify_record: dict) -> dict:
    """Junk stub dla v4 — dodaj puste pola sponsored (junk = nigdy sponsored)."""
    stub = make_junk_stub_final_v2(article, classify_record)
    stub.update({
        "sponsored": False,
        "sponsored_subtype": None,
        "sponsored_justification": "junk_classified",
    })
    return stub


def join_final_v4(
    article: dict,
    classify_record: dict,
    meta_record: dict | None,
    entities_record: dict | None,
    sponsored_record: dict | None,
) -> dict:
    """Złóż final.jsonl rekord z 4 etapów."""
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": False,
        "ok": (
            bool(meta_record and meta_record.get("ok"))
            and bool(entities_record and entities_record.get("ok"))
            and bool(sponsored_record and sponsored_record.get("ok"))
        ),
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    # meta
    if meta_record and meta_record.get("ok"):
        for k in ("language", "category", "title", "meta_description", "h1", "article_summary"):
            out[k] = meta_record.get(k, "")
    else:
        for k in ("language", "category", "title", "meta_description", "h1", "article_summary"):
            out[k] = ""
        out["error"] = "meta_failed"
    # entities
    if entities_record and entities_record.get("ok"):
        out["entities"] = entities_record.get("entities", [])
    else:
        out["entities"] = []
        out["error"] = "entities_failed" if not out.get("error") else f"{out['error']}+entities_failed"
    # sponsored
    if sponsored_record and sponsored_record.get("ok"):
        out["sponsored"] = bool(sponsored_record.get("sponsored", False))
        out["sponsored_subtype"] = sponsored_record.get("sponsored_subtype")
        out["sponsored_justification"] = sponsored_record.get("sponsored_justification", "")
    else:
        out["sponsored"] = False
        out["sponsored_subtype"] = None
        out["sponsored_justification"] = ""
        out["error"] = "sponsored_failed" if not out.get("error") else f"{out['error']}+sponsored_failed"
    return out
