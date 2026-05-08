"""SPO pipeline v3 — rich JSON output (replaces v2 pipe format).

Format introduced 2026-05-08 to eliminate parse errors that plagued the v2 pipe approach
(extra-pipe qualifiers, missing-pipe predicate-object glue). xgrammar's `response_format:
json_schema` guarantees structural validity, so the parser becomes trivial.

Functions:
- `process_entities_spo_v3(client, system, schema, article, ...)` — single-call cram for
  the **spo_v1** pipeline. Returns entities + rich triples in one LLM round-trip.
- `process_spo_pipe_v3(client, system, schema, article, entities, ...)` — split-call for
  the **spo_v2** pipeline. Receives entities from the upstream `entities_only` step and
  produces only the rich SPO block (primary_topic + central_entities + triples).
- `join_final_v3(article, classify, ent_or_combined, spo_or_none, meta, sponsored)` —
  glue together for the final.jsonl record. Preserves rich triple fields verbatim.

Re-exports `make_junk_stub_final_spo` from v1 (junk stubs unchanged: they just need empty
entities/triples placeholders, so the v1 stub still works).

Reuse:
- `lib.pipeline.enrich_entity` for deterministic category/strength tagging on entities.
- `lib.spo_pipeline_v1._cap_central` for capping `is_central` to 5.
- `lib.pipeline_threestep_v2.process_classify_v2` for the junk classifier (loaded by the
  orchestrator scripts, not here).

Why a fresh module rather than mutating v1/v2:
- Both old modules are still callable for backward compat / A/B reference.
- The schema shape is fundamentally different (rich triples vs simple s/p/o or pipe), so
  function signatures and parsers have to diverge anyway.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lib.pipeline import dedup_entities, enrich_entity
from lib.spo_pipeline_v1 import _cap_central, _META_FIELDS, _merge_meta_into, _merge_sponsored_into
from lib.spo_pipeline_v1 import make_junk_stub_final_spo  # noqa: F401  re-export

# Re-export the shared classifier so orchestrators can `from lib.spo_pipeline_v3 import
# process_classify_v2` without pulling in v1/v2 explicitly.
from lib.pipeline_threestep_v2 import process_classify_v2  # noqa: F401
from lib.spo_pipeline_v2 import process_entities_only_v2  # noqa: F401

from lib.vllm_client import VLLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_entities_block(entities: list[dict]) -> str:
    """Render the entity list for inclusion in the spo_pipe user prompt.

    Format: `* name [type, central]` for centrals, `* name [type]` otherwise; centrals
    listed first. Mirrors the convention used by the model in the v3 entities response, so
    the spo_pipe step (in v2 pipeline) sees the same shape.
    """
    central, peripheral = [], []
    for e in entities:
        name = e.get("name")
        if not name:
            continue
        t = e.get("type", "Other")
        if e.get("is_central"):
            central.append(f"* {name} [{t}, central]")
        else:
            peripheral.append(f"* {name} [{t}]")
    lines = central + peripheral
    return "\n".join(lines) if lines else "(none)"


def _normalize_rich_triple(t: dict) -> dict:
    """Trim/lower-case canonical fields, preserve everything else verbatim.

    - `relation_type` is forced lowercase + stripped (the schema permits any string, but
      we want a normalized aggregation key).
    - `subject`, `object` are stripped.
    - All other fields are passed through.
    Returns a new dict; does not mutate input.
    """
    out = dict(t)
    if isinstance(out.get("subject"), str):
        out["subject"] = out["subject"].strip()
    if isinstance(out.get("object"), str):
        out["object"] = out["object"].strip()
    if isinstance(out.get("relation_type"), str):
        out["relation_type"] = out["relation_type"].strip().lower()
    if isinstance(out.get("predicate_phrase"), str):
        out["predicate_phrase"] = out["predicate_phrase"].strip()
    if isinstance(out.get("evidence_span"), str):
        # Keep evidence_span as-is (whitespace inside is meaningful) but strip outer ws.
        out["evidence_span"] = out["evidence_span"].strip()
    return out


def _dedup_rich_triples(triples: list[dict]) -> list[dict]:
    """Dedup by (subject_lower, relation_type, object_lower).

    Keeps first occurrence (which has whatever evidence_span/confidence the model emitted
    first — we don't try to merge).
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for t in triples:
        s = (t.get("subject") or "").strip().lower()
        r = (t.get("relation_type") or "").strip().lower()
        o = (t.get("object") or "").strip().lower()
        key = (s, r, o)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _validate_triples_against_entities(triples: list[dict], entity_names: set[str]) -> dict:
    """Count triples whose entity-mode subject/object don't appear in `entity_names`.

    Informational metric (does not drop the triple). Useful for monitoring how well the
    model respects the "subject MUST come from the entity list" rule on rich JSON.
    Literals are counted as matched-by-design (their s/o is a value, not an entity).
    """
    s_unm = 0
    o_unm = 0
    for t in triples:
        s = t.get("subject", "")
        if s and s not in entity_names:
            s_unm += 1
        if t.get("object_kind") == "entity":
            o = t.get("object", "")
            if o and o not in entity_names:
                o_unm += 1
    return {"triples_s_unmatched": s_unm, "triples_o_unmatched": o_unm}


# ---------------------------------------------------------------------------
# Stage: entities + rich SPO in one call (used by spo_v1 pipeline)
# ---------------------------------------------------------------------------


def process_entities_spo_v3(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int = 4500,
    sampling: dict[str, Any] | None = None,
) -> dict:
    """Single LLM call: extract entities + rich SPO triples from an article.

    Args:
        client: VLLMClient already pointed at the running vLLM server.
        system: system prompt (typically `prompts/spo_entities_v3_system.md`).
        schema: JSON schema dict (typically `prompts/spo_schema_v3.json`).
        article: dict with `text`, `domain`, `path`, `url_hash`, `id`, `url`.
        max_tokens: output budget. Rich triples are verbose — default 4500 (60 ent ×
            ~25 tok + 40 triples × ~80 tok + envelope ≈ 4400).
        sampling: temperature/top_p/top_k overrides; defaults to None → client defaults.

    Returns:
        rec: dict with the standard reporter fields plus the rich entities/triples
        payload:
            {url_hash, id, ts, ok, error, latency_s, usage, finish_reason, attempts,
             primary_topic, entities: [{name,type,category,strength,is_central}],
             central_entities: [{entity_name, centrality}],
             triples: [{subject,subject_type,relation_type,predicate_phrase,object,
                        object_type,object_kind,evidence_span,confidence}],
             entities_raw_count, triples_raw_count, n_central,
             triples_s_unmatched, triples_o_unmatched, parse_errors}
    """
    domain = (article.get("domain") or "").lower().strip()
    path = article.get("path", "")

    user = f"""Extract entities and Subject-Predicate-Object triples from the article below.

PUBLISHER DOMAIN: {domain}
PATH: {path}

<article>
{article['text']}
</article>

Output the JSON object only — no commentary, no markdown fence."""

    sampling = dict(sampling or {})
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="spo_v3_cram",
        max_tokens=max_tokens,
        **sampling,
    )

    rec_base = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "primary_topic": "",
        "entities": [],
        "central_entities": [],
        "triples": [],
        "entities_raw_count": 0,
        "triples_raw_count": 0,
        "n_central": 0,
        "triples_s_unmatched": 0,
        "triples_o_unmatched": 0,
        "parse_errors": 0,  # always 0 in v3 (xgrammar guarantees structure); kept for compat
    }

    rec = {
        **rec_base,
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
    }

    if not res["ok"] or not res.get("parsed"):
        return rec

    parsed = res["parsed"]

    # --- entities: enrich with category/strength, dedup, cap centrals ---
    raw_entities = parsed.get("entities", []) or []
    enriched: list[dict] = []
    for e in raw_entities:
        if not e.get("name"):
            continue
        base = enrich_entity(e)
        base["is_central"] = bool(e.get("is_central", False))
        enriched.append(base)
    enriched = dedup_entities(enriched)
    enriched = _cap_central(enriched, cap=5)
    entity_names = {e["name"] for e in enriched if e.get("name")}

    # --- triples: normalize, dedup, validate against entity list ---
    raw_triples = parsed.get("triples", []) or []
    triples = [_normalize_rich_triple(t) for t in raw_triples]
    triples = _dedup_rich_triples(triples)
    match_stats = _validate_triples_against_entities(triples, entity_names)

    rec.update({
        "primary_topic": (parsed.get("primary_topic") or "").strip(),
        "entities": enriched,
        "central_entities": parsed.get("central_entities", []) or [],
        "triples": triples,
        "entities_raw_count": len(raw_entities),
        "triples_raw_count": len(raw_triples),
        "n_central": sum(1 for e in enriched if e.get("is_central")),
        **match_stats,
    })
    return rec


# ---------------------------------------------------------------------------
# Stage: SPO-only call after upstream entities_only (used by spo_v2 pipeline)
# ---------------------------------------------------------------------------


def process_spo_pipe_v3(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    entities: list[dict],
    max_tokens: int = 3500,
    sampling: dict[str, Any] | None = None,
) -> dict:
    """Single LLM call producing rich SPO JSON, given entities from the upstream step.

    Args:
        client: VLLMClient.
        system: system prompt (typically `prompts/spo_pipe_v3_system.md`).
        schema: JSON schema dict (typically `prompts/spo_pipe_v3_schema.json`).
        article: dict with `text`, `domain`, `path`, `url_hash`, `id`, `url`.
        entities: list of enriched entities from `process_entities_only_v2` (each item
            has `name`, `type`, optionally `is_central`). The user prompt embeds them as
            `* name [type, central]` so the model can cite them as canonical subjects.
        max_tokens: output budget. Rich triples only (no entity block here) — default
            3500 (40 triples × ~80 tok + envelope ≈ 3300).
        sampling: temperature/top_p/top_k overrides.

    Returns:
        rec: dict with the standard reporter fields plus the rich SPO payload:
            {url_hash, id, ts, ok, error, latency_s, usage, finish_reason, attempts,
             primary_topic, central_entities, triples, triples_raw_count,
             triples_s_unmatched, triples_o_unmatched, parse_errors}
    """
    domain = (article.get("domain") or "").lower().strip()
    path = article.get("path", "")
    entity_names = {e.get("name", "") for e in entities if e.get("name")}
    entity_block = _format_entities_block(entities)

    user = f"""Extract Subject-Predicate-Object triples from the article below.

ENTITIES (canonical names — use these EXACT strings as subjects):
{entity_block}

PUBLISHER DOMAIN: {domain}
PATH: {path}

<article>
{article['text']}
</article>

Output the JSON object only — no commentary, no markdown fence."""

    sampling = dict(sampling or {})
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="spo_v3_split",
        max_tokens=max_tokens,
        **sampling,
    )

    rec_base = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "primary_topic": "",
        "central_entities": [],
        "triples": [],
        "triples_raw_count": 0,
        "triples_s_unmatched": 0,
        "triples_o_unmatched": 0,
        "parse_errors": 0,
    }

    rec = {
        **rec_base,
        "ok": res["ok"],
        "error": res["error"],
        "latency_s": round(res["latency_s"], 3),
        "usage": res["usage"],
        "finish_reason": res.get("finish_reason"),
        "attempts": res.get("attempts", 1),
    }

    if not res["ok"] or not res.get("parsed"):
        return rec

    parsed = res["parsed"]
    raw_triples = parsed.get("triples", []) or []
    triples = [_normalize_rich_triple(t) for t in raw_triples]
    triples = _dedup_rich_triples(triples)
    match_stats = _validate_triples_against_entities(triples, entity_names)

    rec.update({
        "primary_topic": (parsed.get("primary_topic") or "").strip(),
        "central_entities": parsed.get("central_entities", []) or [],
        "triples": triples,
        "triples_raw_count": len(raw_triples),
        **match_stats,
    })
    return rec


# ---------------------------------------------------------------------------
# Final-record join (v3 — rich fields preserved)
# ---------------------------------------------------------------------------


def join_final_v3(
    article: dict,
    classify_record: dict,
    entities_record: dict | None,
    spo_record: dict | None,
    meta_record: dict | None = None,
    sponsored_record: dict | None = None,
) -> dict:
    """Join all stage outputs into a single final.jsonl record.

    Works for both pipelines:
    - spo_v1 (cram): pass the cram record as `entities_record` AND `spo_record`
      (they carry overlapping fields; this function picks fields by name so passing the
      same dict to both args is safe). For clarity the orchestrator may pass
      `spo_record=None` and rely on `entities_record` to carry triples — also handled.
    - spo_v2 (split): `entities_record` from `process_entities_only_v2` (entity list +
      n_central) and `spo_record` from `process_spo_pipe_v3` (primary_topic +
      central_entities + triples).
    """
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": False,
        "ok": True,  # tightened below per-stage
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }

    # --- entities block ---
    if entities_record and entities_record.get("ok"):
        out["entities"] = entities_record.get("entities", [])
        out["n_central"] = entities_record.get("n_central", 0)
        out["entities_raw_count"] = entities_record.get("entities_raw_count", 0)
    else:
        out["entities"] = []
        out["n_central"] = 0
        out["entities_raw_count"] = 0
        out["error"] = "entities_failed"
        out["ok"] = False

    # --- SPO block ---
    # In cram mode, entities_record holds the SPO too.
    # In split mode, spo_record holds it.
    src = spo_record if (spo_record and spo_record.get("ok")) else entities_record
    if src and src.get("ok"):
        out["primary_topic"] = src.get("primary_topic", "")
        out["central_entities"] = src.get("central_entities", []) or []
        out["triples"] = src.get("triples", [])
        out["triples_raw_count"] = src.get("triples_raw_count", 0)
        out["triples_s_unmatched"] = src.get("triples_s_unmatched", 0)
        out["triples_o_unmatched"] = src.get("triples_o_unmatched", 0)
        out["parse_errors"] = src.get("parse_errors", 0)
    else:
        out["primary_topic"] = ""
        out["central_entities"] = []
        out["triples"] = []
        out["triples_raw_count"] = 0
        out["triples_s_unmatched"] = 0
        out["triples_o_unmatched"] = 0
        out["parse_errors"] = 0
        if spo_record is not None or entities_record is None:
            # In split mode an explicit spo_record fail is its own error; in cram mode
            # the entities_failed error already covers it.
            out["error"] = "spo_failed" if not out.get("error") else f"{out['error']}+spo_failed"
            out["ok"] = False

    # --- meta + sponsored (shared from v1 helpers) ---
    _merge_meta_into(out, meta_record)
    _merge_sponsored_into(out, sponsored_record)
    if meta_record is not None:
        out["ok"] = out["ok"] and bool(meta_record.get("ok"))
    if sponsored_record is not None:
        out["ok"] = out["ok"] and bool(sponsored_record.get("ok"))
    return out
