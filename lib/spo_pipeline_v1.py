"""SPO pipeline v1 — bootstrap discovery: classify (junk) + entities_spo (entities z is_central + free-form SPO triples).

Two-step pipeline (NIE four-step). Pomijamy meta i sponsored — interesuje nas
tylko junk filter + ekstrakcja encji kanonicznych z trójkami SPO.

Reuse:
- `process_classify_v2` z `lib.pipeline_threestep_v2` (junk classifier binary).
- `enrich_entity` + `dedup_entities` z `lib.pipeline`.

Decyzje (D8 w DECISIONS.md):
- name = forma kanoniczna (instrukcja w prompcie, brak osobnego pola canonical_name)
- is_central: boolean, cap top-5 per artykuł
- triples: free-form predicates (bootstrap discovery — analizujemy rozkład po runie,
  closed vocab v2 dopiero z danych)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lib.pipeline import dedup_entities, enrich_entity
from lib.pipeline_threestep_v2 import (  # noqa: F401  re-eksport dla orchestratora
    make_junk_stub_final_v2,
    process_classify_v2,
)
from lib.vllm_client import VLLMClient

logger = logging.getLogger(__name__)


def _normalize_predicate(p: str) -> str:
    """Lowercase + strip + collapse whitespace."""
    if not p:
        return ""
    return " ".join(p.lower().split())


def _cap_central(entities: list[dict], cap: int = 5) -> list[dict]:
    """Jeśli model zwrócił więcej is_central=True niż cap, zostaw cap pierwszych."""
    seen_central = 0
    out = []
    for e in entities:
        if e.get("is_central"):
            if seen_central >= cap:
                e = {**e, "is_central": False}
            else:
                seen_central += 1
        out.append(e)
    return out


def _validate_triples(triples: list[dict], entity_names: set[str]) -> tuple[list[dict], dict]:
    """Walidacja triples vs entities.

    - normalize predicate (lowercase, strip)
    - flag jeśli `s` nie matchuje żadnej entity.name (warning, ale zachowujemy)
    - `o` może być entity.name lub literal value (bez warning)
    - dedup po (s, p, o)

    Zwraca: (cleaned_triples, stats_dict).
    """
    seen = set()
    cleaned = []
    s_unmatched = 0
    o_unmatched = 0
    for t in triples:
        s = (t.get("s") or "").strip()
        o = (t.get("o") or "").strip()
        p = _normalize_predicate(t.get("p") or "")
        if not s or not o or not p:
            continue
        key = (s.lower(), p, o.lower())
        if key in seen:
            continue
        seen.add(key)
        if s not in entity_names:
            s_unmatched += 1
        if o not in entity_names:
            o_unmatched += 1
        cleaned.append({"s": s, "p": p, "o": o})
    return cleaned, {
        "triples_total": len(cleaned),
        "triples_s_unmatched": s_unmatched,
        "triples_o_unmatched": o_unmatched,
    }


def process_entities_spo(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    """LLM call dla entities + SPO. Reużywa wzorca process_entities_v2.

    Output rec:
        url_hash, id, ok, error, latency_s, usage, finish_reason, attempts, ts,
        entities: [{name, type, category, strength, is_central}],
        triples: [{s, p, o}],
        entities_raw_count: int,
        triples_raw_count: int,
        triples_s_unmatched: int,
        triples_o_unmatched: int.
    """
    domain = (article.get("domain") or "").lower().strip()
    path = article.get("path", "")
    user = f"""Extract canonical entities (with is_central flag) and SPO triples from the article below.

PUBLISHER DOMAIN: {domain}
PATH: {path}

<article>
{article['text']}
</article>"""
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="spo_v1",
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
        raw_entities = res["parsed"].get("entities", [])
        raw_triples = res["parsed"].get("triples", [])
        # Dedup encji + enrich (category, strength) + zachowaj is_central
        deduped = dedup_entities(raw_entities)
        enriched = []
        for e in deduped:
            base = enrich_entity(e)
            base["is_central"] = bool(e.get("is_central", False))
            enriched.append(base)
        enriched = _cap_central(enriched, cap=5)
        # Validate triples vs entities
        entity_names = {e["name"] for e in enriched if e.get("name")}
        cleaned_triples, stats = _validate_triples(raw_triples, entity_names)
        rec["entities"] = enriched
        rec["entities_raw_count"] = len(raw_entities)
        rec["triples"] = cleaned_triples
        rec["triples_raw_count"] = len(raw_triples)
        rec["triples_s_unmatched"] = stats["triples_s_unmatched"]
        rec["triples_o_unmatched"] = stats["triples_o_unmatched"]
        rec["n_central"] = sum(1 for e in enriched if e.get("is_central"))
    else:
        rec["entities"] = []
        rec["entities_raw_count"] = 0
        rec["triples"] = []
        rec["triples_raw_count"] = 0
        rec["triples_s_unmatched"] = 0
        rec["triples_o_unmatched"] = 0
        rec["n_central"] = 0
    return rec


def make_junk_stub_final_spo(article: dict, classify_record: dict) -> dict:
    """Junk stub dla SPO pipeline — pusty entities + triples."""
    return {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": True,
        "ok": True,
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "entities": [],
        "triples": [],
        "n_central": 0,
        "skipped_reason": "junk_classified",
    }


def join_final_spo(
    article: dict,
    classify_record: dict,
    entities_spo_record: dict | None,
) -> dict:
    """Złóż final.jsonl rekord z 2 etapów: classify + entities_spo."""
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": False,
        "ok": bool(entities_spo_record and entities_spo_record.get("ok")),
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if entities_spo_record and entities_spo_record.get("ok"):
        out["entities"] = entities_spo_record.get("entities", [])
        out["triples"] = entities_spo_record.get("triples", [])
        out["n_central"] = entities_spo_record.get("n_central", 0)
        out["entities_raw_count"] = entities_spo_record.get("entities_raw_count", 0)
        out["triples_raw_count"] = entities_spo_record.get("triples_raw_count", 0)
        out["triples_s_unmatched"] = entities_spo_record.get("triples_s_unmatched", 0)
        out["triples_o_unmatched"] = entities_spo_record.get("triples_o_unmatched", 0)
    else:
        out["entities"] = []
        out["triples"] = []
        out["n_central"] = 0
        out["entities_raw_count"] = 0
        out["triples_raw_count"] = 0
        out["triples_s_unmatched"] = 0
        out["triples_o_unmatched"] = 0
        out["error"] = "entities_spo_failed"
    return out
