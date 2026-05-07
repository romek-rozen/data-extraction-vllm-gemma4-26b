"""Logika per-artykuł dla Step 1 i Step 2 (importowalna z różnych skryptów).

Wydzielone z run_step1.py / run_step2.py żeby ab_sampling.py mógł reużywać
bez duplikacji logiki.
"""

from typing import Any

from lib.prompt_loader import build_step1_user, build_step2_user
from lib.vllm_client import VLLMClient


# Mapping type → (category, strength). Czysty Azure NER (51 typów, 11 kategorii).
# Spec: https://learn.microsoft.com/en-us/azure/ai-services/language-service/named-entity-recognition/concepts/named-entity-categories
#
# - category: Azure high-level Category (Person, Organization, Location, Event, Product, Quantity, DateTime, Skill, Information, Address, Email, IpAddress, PhoneNumber, URL, PersonType)
# - strength: strong = encja istnieje samodzielnie z ID w bazie wiedzy (Wikidata, schema.org); weak = kontekstowo-zależna
TYPE_TO_CATEGORY: dict[str, tuple[str, str]] = {
    # Person
    "Person":                        ("Person", "strong"),
    "PersonType":                    ("PersonType", "weak"),
    # Organization
    "Organization":                  ("Organization", "strong"),
    "OrganizationMedical":           ("Organization", "strong"),
    "OrganizationSports":            ("Organization", "strong"),
    "OrganizationStockExchange":     ("Organization", "strong"),
    # Location
    "Location":                      ("Location", "strong"),
    "City":                          ("Location", "strong"),
    "Continent":                     ("Location", "strong"),
    "CountryRegion":                 ("Location", "strong"),
    "State":                         ("Location", "strong"),
    "GPE":                           ("Location", "strong"),
    "Geographical":                  ("Location", "strong"),
    "Airport":                       ("Location", "strong"),
    "Structural":                    ("Location", "strong"),
    "Address":                       ("Address", "weak"),
    # Event
    "Event":                         ("Event", "strong"),
    "CulturalEvent":                 ("Event", "strong"),
    "NaturalEvent":                  ("Event", "strong"),
    "SportsEvent":                   ("Event", "strong"),
    # Product
    "Product":                       ("Product", "strong"),
    "ComputingProduct":              ("Product", "strong"),
    # Quantity
    "Number":                        ("Quantity", "weak"),
    "NumberRange":                   ("Quantity", "weak"),
    "Ordinal":                       ("Quantity", "weak"),
    "Currency":                      ("Quantity", "weak"),
    "Percentage":                    ("Quantity", "weak"),
    "Age":                           ("Quantity", "weak"),
    "Dimension":                     ("Quantity", "weak"),
    "Area":                          ("Quantity", "weak"),
    "Length":                        ("Quantity", "weak"),
    "Height":                        ("Quantity", "weak"),
    "Volume":                        ("Quantity", "weak"),
    "Weight":                        ("Quantity", "weak"),
    "Speed":                         ("Quantity", "weak"),
    "Temperature":                   ("Quantity", "weak"),
    # DateTime
    "Date":                          ("DateTime", "weak"),
    "Time":                          ("DateTime", "weak"),
    "DateTime":                      ("DateTime", "weak"),
    "DateRange":                     ("DateTime", "weak"),
    "TimeRange":                     ("DateTime", "weak"),
    "DateTimeRange":                 ("DateTime", "weak"),
    "Duration":                      ("DateTime", "weak"),
    "SetTemporal":                   ("DateTime", "weak"),
    "Temporal":                      ("DateTime", "weak"),
    # Communication / Identifiers
    "Email":                         ("Email", "weak"),
    "PhoneNumber":                   ("PhoneNumber", "weak"),
    "URL":                           ("URL", "weak"),
    "IpAddress":                     ("IpAddress", "weak"),
    # Skill / Information
    "Skill":                         ("Skill", "weak"),
    "Information":                   ("Information", "weak"),
}


# Dozwolone pola metadata per typ (Azure spec).
# Model bywa, że dorzuca pola z innego sub-schema (np. offset/relativeTo do Number).
# Cleanup gwarantuje że zachowamy tylko semantycznie poprawne pola.
METADATA_FIELDS_BY_TYPE: dict[str, set[str]] = {
    # unit + value
    "Age":          {"unit", "value"},
    "Area":         {"unit", "value"},
    "Length":       {"unit", "value"},
    "Height":       {"unit", "value"},
    "Volume":       {"unit", "value"},
    "Weight":       {"unit", "value"},
    "Speed":        {"unit", "value"},
    "Temperature":  {"unit", "value"},
    "Percentage":   {"unit", "value"},
    "Duration":     {"unit", "value"},
    # Currency: + ISO4217
    "Currency":     {"unit", "value", "ISO4217"},
    # Number: numberKind + value
    "Number":       {"numberKind", "value"},
    # NumberRange: rangeKind + minimum + maximum
    "NumberRange":  {"rangeKind", "minimum", "maximum"},
    # Ordinal
    "Ordinal":      {"offset", "relativeTo", "value"},
    # Date/Time/Range
    "Date":         {"timex", "value"},
    "DateTime":     {"timex", "value"},
    "Time":         {"timex", "value"},
    "DateRange":    {"timex", "value", "rangeKind", "minimum", "maximum"},
    "TimeRange":    {"timex", "value"},
    "DateTimeRange":{"timex", "value"},
    "SetTemporal":  {"timex", "value"},
    # Information (data size only): unit + value. Inne Information bez metadata.
    "Information":  {"unit", "value"},
    # Pozostałe typy: brak metadata
}


def _clean_metadata(entity_type: str, metadata: dict | None) -> dict | None:
    """Zostaw tylko pola dozwolone dla danego typu. Odrzuć resztę."""
    if not metadata:
        return None
    allowed = METADATA_FIELDS_BY_TYPE.get(entity_type)
    if not allowed:
        return None  # ten typ nie ma metadata schema → odrzuć całość
    cleaned = {k: v for k, v in metadata.items() if k in allowed}
    # odrzuć fałszywe wypełnienia: unit:"Unspecified" + brak innych info
    if cleaned.get("unit") == "Unspecified" and len(cleaned) <= 2 and not cleaned.get("ISO4217"):
        # zostaw `value` ale wyzeruj fałszywą jednostkę
        cleaned.pop("unit", None)
    return cleaned or None


def enrich_entity(entity: dict) -> dict:
    """Dodaj pola `category` i `strength`; oczyść `metadata` do dozwolonych pól per typ."""
    t = entity.get("type", "Other")
    cat, strength = TYPE_TO_CATEGORY.get(t, ("Other", "weak"))
    out = {**entity, "category": cat, "strength": strength}
    cleaned_md = _clean_metadata(t, entity.get("metadata"))
    if cleaned_md is not None:
        out["metadata"] = cleaned_md
    else:
        out.pop("metadata", None)  # usuń metadata jeśli było puste/zabronione
    return out


def dedup_entities(entities: list[dict]) -> list[dict]:
    """Dedup encji w obrębie pojedynczego artykułu.

    Klucz dedupu: (name.lower(), type) — case-insensitive po nazwie.
    Encja z tą samą nazwą ale różnym typem jest zachowana (model świadomie
    zaklasyfikował ją kontekstowo).

    Globalny dedup (między artykułami) NIE robimy — encje typu "jogurt" są
    raz dishem, raz ingredientem zależnie od kontekstu artykułu (decyzja D15).

    Zachowuje pierwsze wystąpienie + jego kolejność.
    """
    seen = set()
    out = []
    for e in entities:
        key = (e.get("name", "").lower(), e.get("type", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


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
        raw = res["parsed"].get("entities", [])
        deduped = dedup_entities(raw)
        enriched = [enrich_entity(e) for e in deduped]
        record.update({
            "category": res["parsed"].get("category"),
            "language": res["parsed"].get("language"),
            "entities": enriched,
            "entities_raw_count": len(raw),  # ile model wygenerował przed dedupem
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
