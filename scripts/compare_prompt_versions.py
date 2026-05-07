"""Phase 4 — porównanie promptów v1 vs v2.

Sprawdza specyficzne problemy znalezione w Phase 4 analysis:
- Anatomia w `structure` / `other`
- Akademic disciplines w `discipline`
- Konsumer products w `other`
- itd.

Ważne: porównanie ma sens tylko jeśli oba pliki dotyczą TYCH SAMYCH URLi.
W praktyce Phase 4 v2 run obejmuje pierwsze 50 URL. v1 ma 100. Bierzemy część wspólną.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.config import RESULT_DIR  # noqa: E402

# Reguły walidacji znalezionych problemów. Zwraca True jeśli encja JEST błędna.
PROBLEM_RULES = {
    "anatomy_as_structure": lambda e: (
        e.get("type") == "structure" and any(k in e.get("name", "").lower()
        for k in ["kręgosłup", "wątroba", "mięsień", "mięśnie", "krwinki",
                  "układ odpornościowy", "układ pokarmowy", "nerwy", "kości"])
    ),
    "academic_as_discipline": lambda e: (
        e.get("type") == "discipline" and e.get("name", "").lower() in
        {"dietetyka", "anatomia", "biomechanika", "medycyna regeneracyjna",
         "biochemia", "fizjologia", "bmi"}
    ),
    "abstract_as_structure": lambda e: (
        e.get("type") == "structure" and "piramid" in e.get("name", "").lower()
    ),
    "kitchen_tool_as_structure": lambda e: (
        e.get("type") == "structure" and e.get("name", "").lower() in
        {"tortownica", "patelnia", "garnek", "blacha"}
    ),
    "product_as_other": lambda e: (
        e.get("type") == "other" and e.get("name", "").lower() in
        {"chusteczki nawilżane", "pieluchy", "podpaski"}
    ),
    "ngo_as_brand": lambda e: (
        e.get("type") == "brand" and "fsc" in e.get("name", "").lower()
    ),
    "condition_as_therapy": lambda e: (
        e.get("type") == "therapy" and e.get("name", "").lower() == "stres oksydacyjny"
    ),
}


def load(path: Path) -> dict[str, dict]:
    by_hash = {}
    if not path.exists():
        return by_hash
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("ok") and r.get("url_hash"):
                    by_hash[r["url_hash"]] = r
            except json.JSONDecodeError:
                continue
    return by_hash


def count_problems(data: dict[str, dict]) -> dict:
    out = {k: 0 for k in PROBLEM_RULES}
    examples = {k: [] for k in PROBLEM_RULES}
    for r in data.values():
        for e in r.get("entities", []):
            for rule_name, rule_fn in PROBLEM_RULES.items():
                if rule_fn(e):
                    out[rule_name] += 1
                    if len(examples[rule_name]) < 3:
                        examples[rule_name].append((r["id"], e["name"], e["type"]))
    return {"counts": out, "examples": examples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1", default=str(RESULT_DIR / "entity_layer_v1.jsonl"))
    parser.add_argument("--v2", default=str(RESULT_DIR / "entity_layer.jsonl"))
    parser.add_argument("--out", default=str(RESULT_DIR / "phase4_compare.md"))
    args = parser.parse_args()

    v1 = load(Path(args.v1))
    v2 = load(Path(args.v2))
    common = set(v1) & set(v2)

    md = ["# Phase 4 — Prompt v1 vs v2 (porównanie problemów)\n"]
    md.append(f"**v1:** `{args.v1}` ({len(v1)} URL)")
    md.append(f"**v2:** `{args.v2}` ({len(v2)} URL)")
    md.append(f"**Wspólne URL:** {len(common)}\n")

    # Filter to common URLs for fair comparison
    v1c = {k: v1[k] for k in common}
    v2c = {k: v2[k] for k in common}

    p1 = count_problems(v1c)
    p2 = count_problems(v2c)

    # Aggregate stats
    n_ent_v1 = sum(len(r.get("entities", [])) for r in v1c.values())
    n_ent_v2 = sum(len(r.get("entities", [])) for r in v2c.values())
    md.append(f"**Encji łącznie:** v1={n_ent_v1}, v2={n_ent_v2}\n")

    md.append("## Problemy w typowaniu (mniej = lepiej)\n")
    md.append("| Problem | v1 | v2 | Δ |")
    md.append("|---|---|---|---|")
    total_v1 = sum(p1["counts"].values())
    total_v2 = sum(p2["counts"].values())
    for rule in PROBLEM_RULES:
        n1 = p1["counts"][rule]
        n2 = p2["counts"][rule]
        delta = n2 - n1
        sign = "✅" if delta < 0 else ("⚠️" if delta > 0 else "─")
        md.append(f"| {rule} | {n1} | {n2} | {delta:+d} {sign} |")
    md.append(f"| **TOTAL** | **{total_v1}** | **{total_v2}** | **{total_v2 - total_v1:+d}** |")
    md.append("")

    md.append("## Przykłady problemów\n")
    for rule in PROBLEM_RULES:
        if p1["examples"][rule] or p2["examples"][rule]:
            md.append(f"### {rule}\n")
            md.append("**v1:**" + (" (brak)" if not p1["examples"][rule] else ""))
            for id_, name, t in p1["examples"][rule]:
                md.append(f"- `{id_}`: '{name}' → {t}")
            md.append("\n**v2:**" + (" (brak)" if not p2["examples"][rule] else ""))
            for id_, name, t in p2["examples"][rule]:
                md.append(f"- `{id_}`: '{name}' → {t}")
            md.append("")

    # Rozkład typów (czy v2 nie zmienił globalnie zbyt mocno?)
    md.append("## Rozkład typów\n")
    types_v1 = Counter()
    types_v2 = Counter()
    for r in v1c.values():
        for e in r.get("entities", []):
            types_v1[e.get("type", "?")] += 1
    for r in v2c.values():
        for e in r.get("entities", []):
            types_v2[e.get("type", "?")] += 1
    all_types = sorted(set(types_v1) | set(types_v2),
                       key=lambda t: -(types_v1[t] + types_v2[t]))
    md.append("| Typ | v1 | v2 | Δ |")
    md.append("|---|---|---|---|")
    for t in all_types:
        n1, n2 = types_v1[t], types_v2[t]
        md.append(f"| {t} | {n1} | {n2} | {n2-n1:+d} |")
    md.append("")

    # Ile encji ZMIENIŁO TYP per URL?
    md.append("## Stabilność typów dla tych samych nazw encji (v1 ↔ v2)\n")
    same_type = 0
    diff_type = 0
    only_v1 = 0
    only_v2 = 0
    for h in common:
        # mapowanie name (lowercase) → type
        e1 = {e["name"].lower(): e["type"] for e in v1c[h].get("entities", []) if e.get("name")}
        e2 = {e["name"].lower(): e["type"] for e in v2c[h].get("entities", []) if e.get("name")}
        for n in e1:
            if n in e2:
                if e1[n] == e2[n]:
                    same_type += 1
                else:
                    diff_type += 1
            else:
                only_v1 += 1
        for n in e2:
            if n not in e1:
                only_v2 += 1
    md.append(f"- Ten sam typ w obu wersjach: **{same_type}**")
    md.append(f"- Różny typ (v1 vs v2): **{diff_type}**")
    md.append(f"- Tylko v1 (model nie wybrał w v2): {only_v1}")
    md.append(f"- Tylko v2 (model wybrał nową w v2): {only_v2}\n")

    Path(args.out).write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
