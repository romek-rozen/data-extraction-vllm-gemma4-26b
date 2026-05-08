"""SPO pipeline v2 — three-step (classify + entities_only + spo_pipe) baseline porównawczy do v1.

Vs v1 (single-call entities+triples JSON):
- entities_only: tylko encje + is_central (mniejszy schema, mniejszy budget output)
- spo_pipe: pipe-separated `subject|predicate|object` per linia (no JSON) — ~60% mniej tokenów output
- każdy step ma osobny budget tokenów + osobny prompt focused

Reuse:
- `process_classify_v2`, `make_junk_stub_final_v2` z lib.pipeline_threestep_v2 (junk classifier).
- `enrich_entity`, `dedup_entities` z lib.pipeline (canonicalize + Azure category mapping).
- `_cap_central` z lib.spo_pipeline_v1.

Decyzje:
- spo_pipe: vLLM bez `response_format` (raw text), `temperature=1.0, top_p=0.95, top_k=64` (Google defaults),
  `max_tokens=2000` (40 linii × ~30 tok + bufor).
- Predicate normalization: lowercase + strip + collapse whitespace.
- Sanity: linie z innym niż 3 segmentami → parse_errors++, skip.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from lib.pipeline import dedup_entities, enrich_entity
from lib.spo_pipeline_v1 import _cap_central, _normalize_predicate
from lib.vllm_client import VLLMClient

logger = logging.getLogger(__name__)

# Re-export for orchestrator convenience
from lib.pipeline_threestep_v2 import process_classify_v2  # noqa: E402,F401
from lib.spo_pipeline_v1 import make_junk_stub_final_spo  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Stage 2: entities_only (JSON, no triples)
# ---------------------------------------------------------------------------


def process_entities_only_v2(
    client: VLLMClient,
    system: str,
    schema: dict,
    article: dict,
    max_tokens: int,
    sampling: dict[str, Any],
) -> dict:
    """LLM call: tylko entities + is_central (bez triples). JSON output via xgrammar.

    Output rec:
        url_hash, id, ok, error, latency_s, usage, finish_reason, attempts, ts,
        entities: [{name, type, category, strength, is_central}],
        entities_raw_count: int,
        n_central: int.
    """
    domain = (article.get("domain") or "").lower().strip()
    path = article.get("path", "")
    user = f"""Extract canonical entities (with is_central flag) from the article below.

PUBLISHER DOMAIN: {domain}
PATH: {path}

<article>
{article['text']}
</article>"""
    res = client.chat_json(
        system_prompt=system,
        user_prompt=user,
        json_schema=schema,
        schema_name="entities_only_v2",
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
        deduped = dedup_entities(raw_entities)
        enriched = []
        for e in deduped:
            base = enrich_entity(e)
            base["is_central"] = bool(e.get("is_central", False))
            enriched.append(base)
        enriched = _cap_central(enriched, cap=5)
        rec["entities"] = enriched
        rec["entities_raw_count"] = len(raw_entities)
        rec["n_central"] = sum(1 for e in enriched if e.get("is_central"))
    else:
        rec["entities"] = []
        rec["entities_raw_count"] = 0
        rec["n_central"] = 0
    return rec


# ---------------------------------------------------------------------------
# Stage 3: spo_pipe (raw text, pipe-separated)
# ---------------------------------------------------------------------------


def _parse_pipe_output(raw: str) -> tuple[list[dict], dict]:
    """Parse `s|p|o` per line.

    - Skip blank lines, fence/comment lines (`#`, ```), numbered prefixes (`1. `, `- `).
    - Lines with != 3 pipe-separated parts → parse_errors++, skip.
    - Predicate normalized (lowercase + strip + collapse ws).
    - Dedup by (s.lower(), p, o.lower()).

    Returns: (triples, stats) where stats = {parse_errors, n_lines_total, sample_bad_lines}.
    """
    triples: list[dict] = []
    seen: set[tuple] = set()
    parse_errors = 0
    n_lines_total = 0
    sample_bad: list[str] = []

    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip code fences, headers, comments
        if line.startswith("```") or line.startswith("#"):
            continue
        # Strip simple list/numbering prefixes the model might add
        for pfx in ("- ", "* "):
            if line.startswith(pfx):
                line = line[len(pfx):].strip()
                break
        # `1. foo|bar|baz` → strip leading `N. `
        if len(line) > 2 and line[0].isdigit():
            i = 0
            while i < len(line) and line[i].isdigit():
                i += 1
            if i < len(line) - 1 and line[i] == "." and line[i + 1] == " ":
                line = line[i + 2:].strip()

        n_lines_total += 1
        parts = line.split("|")
        if len(parts) != 3:
            parse_errors += 1
            if len(sample_bad) < 5:
                sample_bad.append(line[:200])
            continue
        s = parts[0].strip()
        p = _normalize_predicate(parts[1])
        o = parts[2].strip()
        if not s or not p or not o:
            parse_errors += 1
            if len(sample_bad) < 5:
                sample_bad.append(line[:200])
            continue
        key = (s.lower(), p, o.lower())
        if key in seen:
            continue
        seen.add(key)
        triples.append({"s": s, "p": p, "o": o})

    return triples, {
        "parse_errors": parse_errors,
        "n_lines_total": n_lines_total,
        "sample_bad_lines": sample_bad,
    }


def _validate_triples_against_entities(triples: list[dict], entity_names: set[str]) -> dict:
    """Count s/o that don't match any canonical entity name (informational, non-blocking)."""
    s_unm = 0
    o_unm = 0
    for t in triples:
        if t["s"] not in entity_names:
            s_unm += 1
        if t["o"] not in entity_names:
            o_unm += 1
    return {"triples_s_unmatched": s_unm, "triples_o_unmatched": o_unm}


def process_spo_pipe_v2(
    base_url: str,
    model: str,
    system: str,
    article: dict,
    entities: list[dict],
    max_tokens: int = 2000,
    timeout: float = 300.0,
    max_retries_network: int = 2,
    temperature: float = 1.0,
    top_p: float = 0.95,
    top_k: int = 64,
) -> dict:
    """Raw POST: non-JSON output, pipe-separated triples.

    Args:
        entities: lista enriched entities z entities_only step (z polem `name`).

    Returns:
        rec: {ok, error, latency_s, usage, finish_reason, attempts, ts,
              triples: [{s,p,o}], triples_raw_count, parse_errors,
              triples_s_unmatched, triples_o_unmatched, n_lines_total}.
    """
    domain = (article.get("domain") or "").lower().strip()
    path = article.get("path", "")

    # Format: `* name [type, central]` — central first (article's main subjects), reszta after.
    # Sygnały dla modelu: `central` → preferuj jako `s` w triples; `type` → dobierz predykat
    # (Person/Org → subject, Number/Temperature/Currency → object).
    entity_names = [e.get("name", "") for e in entities if e.get("name")]
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
    entity_lines = central + peripheral
    entity_list_str = "\n".join(entity_lines) if entity_lines else "(none)"

    user = f"""Extract Subject-Predicate-Object triples from the article below.

ENTITIES (canonical names — use these EXACT strings as subjects):
{entity_list_str}

PUBLISHER DOMAIN: {domain}
PATH: {path}

<article>
{article['text']}
</article>

Output ONLY pipe-separated triples (one per line), max 40 lines. No JSON, no commentary."""

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "repetition_penalty": 1.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    last_err = None
    rec_base = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "triples": [],
        "triples_raw_count": 0,
        "parse_errors": 0,
        "triples_s_unmatched": 0,
        "triples_o_unmatched": 0,
        "n_lines_total": 0,
        "sample_bad_lines": [],
    }

    for attempt in range(max_retries_network + 1):
        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=timeout
            )
            latency = time.perf_counter() - t0
            r.raise_for_status()
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
            finish_reason = choice.get("finish_reason")

            triples, parse_stats = _parse_pipe_output(content)
            entity_name_set = {n for n in entity_names}
            match_stats = _validate_triples_against_entities(triples, entity_name_set)

            rec = {
                **rec_base,
                "ok": True,
                "error": None,
                "raw": content,
                "latency_s": round(latency, 3),
                "usage": data.get("usage", {}),
                "finish_reason": finish_reason,
                "attempts": attempt + 1,
                "triples": triples,
                "triples_raw_count": parse_stats["n_lines_total"],
                "parse_errors": parse_stats["parse_errors"],
                "n_lines_total": parse_stats["n_lines_total"],
                "sample_bad_lines": parse_stats["sample_bad_lines"],
                **match_stats,
            }
            return rec
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"network_attempt_{attempt}: {e}"
            logger.warning(f"spo_pipe network err, retry {attempt+1}/{max_retries_network}: {e}")
            time.sleep(1.0 * (attempt + 1))
        except requests.HTTPError as e:
            status = getattr(r, "status_code", 0)
            if status >= 500 and attempt < max_retries_network:
                last_err = f"http_{status}_attempt_{attempt}: {e}"
                time.sleep(1.0 * (attempt + 1))
                continue
            return {
                **rec_base,
                "ok": False,
                "error": f"spo_pipe_http_{status}: {e}",
                "raw": None,
                "latency_s": round(time.perf_counter() - t0, 3),
                "usage": {},
                "finish_reason": None,
                "attempts": attempt + 1,
            }

    return {
        **rec_base,
        "ok": False,
        "error": f"spo_pipe_network_exhausted: {last_err}",
        "raw": None,
        "latency_s": 0.0,
        "usage": {},
        "finish_reason": None,
        "attempts": max_retries_network + 1,
    }


# ---------------------------------------------------------------------------
# Final join
# ---------------------------------------------------------------------------


def join_final_spo_v2(
    article: dict,
    classify_record: dict,
    entities_record: dict | None,
    spo_record: dict | None,
    meta_record: dict | None = None,
    sponsored_record: dict | None = None,
) -> dict:
    """Złóż final.jsonl rekord z classify + entities_only + spo_pipe (+ opcjonalnie meta + sponsored)."""
    from lib.spo_pipeline_v1 import _merge_meta_into, _merge_sponsored_into
    out = {
        "url_hash": article["url_hash"],
        "id": article["id"],
        "url": article["url"],
        "domain": article["domain"],
        "is_junk": False,
        "ok": bool(entities_record and entities_record.get("ok"))
              and bool(spo_record and spo_record.get("ok")),
        "error": None,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if entities_record and entities_record.get("ok"):
        out["entities"] = entities_record.get("entities", [])
        out["n_central"] = entities_record.get("n_central", 0)
        out["entities_raw_count"] = entities_record.get("entities_raw_count", 0)
    else:
        out["entities"] = []
        out["n_central"] = 0
        out["entities_raw_count"] = 0
        out["error"] = "entities_only_failed"

    if spo_record and spo_record.get("ok"):
        out["triples"] = spo_record.get("triples", [])
        out["triples_raw_count"] = spo_record.get("triples_raw_count", 0)
        out["parse_errors"] = spo_record.get("parse_errors", 0)
        out["triples_s_unmatched"] = spo_record.get("triples_s_unmatched", 0)
        out["triples_o_unmatched"] = spo_record.get("triples_o_unmatched", 0)
        out["n_lines_total"] = spo_record.get("n_lines_total", 0)
    else:
        out["triples"] = []
        out["triples_raw_count"] = 0
        out["parse_errors"] = 0
        out["triples_s_unmatched"] = 0
        out["triples_o_unmatched"] = 0
        out["n_lines_total"] = 0
        out["error"] = "spo_pipe_failed" if not out.get("error") else f"{out['error']}+spo_pipe_failed"
    _merge_meta_into(out, meta_record)
    _merge_sponsored_into(out, sponsored_record)
    if meta_record is not None:
        out["ok"] = out["ok"] and bool(meta_record.get("ok"))
    if sponsored_record is not None:
        out["ok"] = out["ok"] and bool(sponsored_record.get("ok"))
    return out
