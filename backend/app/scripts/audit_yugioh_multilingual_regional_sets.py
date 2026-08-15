#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping

import ijson
from sqlalchemy import create_engine, text

LANGS = ("en", "es", "ja")
REGION_PREFIXES = {
    "EN", "SP", "ES", "JP", "AE", "DE", "FR", "IT", "PT", "KR", "NA", "EU", "AU", "CA",
}


def s(v: Any) -> str:
    return str(v or "").strip()


def sl(v: Any) -> str:
    return s(v).lower()


def mapping(v: Any) -> Mapping[str, Any]:
    return v if isinstance(v, Mapping) else {}


def iter_cards(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as fh:
        first = b""
        while True:
            ch = fh.read(1)
            if not ch:
                break
            if not ch.isspace():
                first = ch
                break
        fh.seek(0)
        if first == b"[":
            yield from (x for x in ijson.items(fh, "item") if isinstance(x, dict))
        elif first == b"{":
            yield from (x for _k, x in ijson.kvitems(fh, "") if isinstance(x, dict))
        else:
            raise ValueError("Unsupported YAML Yugi aggregate JSON shape")


def family(code: str) -> str:
    c = s(code).upper()
    return c.split("-", 1)[0].strip() if "-" in c else c


def exact_code(code: str) -> bool:
    c = s(code)
    return bool(c) and not any(ch in c for ch in ("?", "*"))


def slot(code: str) -> str:
    """Normalize regional collector marker while preserving the printed slot.

    Examples: LOB-EN001 / LOB-SP001 / LOB-JP001 -> 001.
    Historic OCG numbers like 301-001 remain 001. We use this only as a
    diagnostic cross-region overlap signal, never as identity for writes.
    """
    c = s(code).upper()
    tail = c.split("-", 1)[1] if "-" in c else c
    if len(tail) >= 3 and tail[:2] in REGION_PREFIXES:
        tail = tail[2:]
    return tail


def logical_identity(card: Mapping[str, Any], ordinal: int) -> str:
    konami = s(card.get("konami_id"))
    password = s(card.get("password"))
    if konami:
        return f"konami:{konami}"
    if password:
        return f"password:{password}"
    return f"row:{ordinal}"


def pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 4) if den else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return round(len(a & b) / len(union), 6) if union else 0.0


def classify(shared: bool, card_jaccard: float, slot_pair_jaccard: float, target_rows: int) -> str:
    if not shared:
        return "regional_only_family"
    # A same family code used by both regions with substantial exact-card or
    # same-card/same-slot overlap is compatible with the project's existing
    # family-level Set abstraction. The thresholds are diagnostic gates only;
    # no fuzzy name match or write decision is made here.
    if slot_pair_jaccard >= 0.60 or card_jaccard >= 0.75:
        return "shared_family_high_overlap"
    if slot_pair_jaccard >= 0.20 or card_jaccard >= 0.40:
        return "shared_family_medium_overlap_review"
    if target_rows <= 2:
        return "shared_family_tiny_sample_review"
    return "shared_code_low_overlap_collision_risk"


def run(cards_path: Path, report_path: Path, source_head: str, source_last_modified: str) -> dict[str, Any]:
    per_lang: dict[str, dict[str, list[dict[str, str]]]] = {
        lang: defaultdict(list) for lang in LANGS
    }
    source_cards = 0
    for card in iter_cards(cards_path):
        source_cards += 1
        identity = logical_identity(card, source_cards)
        sets = mapping(card.get("sets"))
        for lang in LANGS:
            rows = sets.get(lang) or []
            if isinstance(rows, Mapping):
                rows = [rows]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                code = s(row.get("set_number")).upper()
                if not exact_code(code):
                    continue
                fam = family(code)
                if not fam:
                    continue
                per_lang[lang][fam].append({
                    "identity": identity,
                    "code": code,
                    "slot": slot(code),
                    "name": s(row.get("set_name")),
                })

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        ro = sl(conn.execute(text("SHOW transaction_read_only")).scalar_one())
        if ro not in {"on", "true", "1"}:
            raise AssertionError(f"transaction_read_only={ro!r}")
        gid = conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one()
        db_sets = {
            s(code).upper(): {"id": int(sid), "name": s(name), "yugioh_id": s(yid)}
            for sid, code, name, yid in conn.execute(text(
                "SELECT id, code, name, yugioh_id FROM sets WHERE game_id=:g"
            ), {"g": gid}) if s(code)
        }
        tx.rollback()

    family_details: dict[str, Any] = {}
    classifications = {"es": Counter(), "ja": Counter()}
    missing = {"es": Counter(), "ja": Counter()}
    low_risk = {"es": [], "ja": []}
    medium_review = {"es": [], "ja": []}

    all_families = sorted(set(per_lang["en"]) | set(per_lang["es"]) | set(per_lang["ja"]))
    for fam in all_families:
        entry: dict[str, Any] = {
            "db_set": db_sets.get(fam),
            "languages": {},
            "comparisons": {},
        }
        for lang in LANGS:
            rows = per_lang[lang].get(fam, [])
            identities = {r["identity"] for r in rows}
            slot_pairs = {f"{r['identity']}|{r['slot']}" for r in rows if r["slot"]}
            names = Counter(r["name"] for r in rows if r["name"])
            entry["languages"][lang] = {
                "rows": len(rows),
                "cards": len(identities),
                "set_numbers": len({r["code"] for r in rows}),
                "slot_pairs": len(slot_pairs),
                "names": names.most_common(5),
            }

        en_rows = per_lang["en"].get(fam, [])
        en_cards = {r["identity"] for r in en_rows}
        en_pairs = {f"{r['identity']}|{r['slot']}" for r in en_rows if r["slot"]}
        for lang in ("es", "ja"):
            target_rows = per_lang[lang].get(fam, [])
            if not target_rows:
                continue
            target_cards = {r["identity"] for r in target_rows}
            target_pairs = {f"{r['identity']}|{r['slot']}" for r in target_rows if r["slot"]}
            shared = bool(en_rows)
            card_j = jaccard(en_cards, target_cards) if shared else 0.0
            pair_j = jaccard(en_pairs, target_pairs) if shared else 0.0
            cls = classify(shared, card_j, pair_j, len(target_rows))
            classifications[lang][cls] += 1
            if fam not in db_sets:
                missing[lang][fam] += len(target_rows)
            comparison = {
                "classification": cls,
                "db_set_exists": fam in db_sets,
                "shared_with_en": shared,
                "card_overlap": len(en_cards & target_cards),
                "card_union": len(en_cards | target_cards),
                "card_jaccard": card_j,
                "slot_pair_overlap": len(en_pairs & target_pairs),
                "slot_pair_union": len(en_pairs | target_pairs),
                "slot_pair_jaccard": pair_j,
                "target_rows": len(target_rows),
            }
            entry["comparisons"][lang] = comparison
            if cls == "shared_code_low_overlap_collision_risk":
                low_risk[lang].append((fam, comparison))
            elif "review" in cls:
                medium_review[lang].append((fam, comparison))
        family_details[fam] = entry

    # Same code can represent a regional product with different contents; that
    # is acceptable only if the existing project deliberately treats Set.code
    # as a family abstraction. Low-overlap reuse is held back for review.
    targets: dict[str, Any] = {}
    for lang in ("es", "ja"):
        target_families = set(per_lang[lang])
        shared_en = target_families & set(per_lang["en"])
        missing_families = sorted(f for f in target_families if f not in db_sets)
        safe_missing = [f for f in missing_families if family_details[f]["comparisons"][lang]["classification"] != "shared_code_low_overlap_collision_risk"]
        collision_missing = [f for f in missing_families if family_details[f]["comparisons"][lang]["classification"] == "shared_code_low_overlap_collision_risk"]
        targets[lang] = {
            "physical_families": len(target_families),
            "shared_family_codes_with_en": len(shared_en),
            "regional_only_family_codes": len(target_families - set(per_lang["en"])),
            "existing_db_family_codes": len(target_families & set(db_sets)),
            "missing_db_family_codes": len(missing_families),
            "missing_db_family_rows": sum(missing[lang].values()),
            "missing_db_family_top": missing[lang].most_common(50),
            "safe_missing_family_candidates": len(safe_missing),
            "collision_risk_missing_family_candidates": len(collision_missing),
            "classifications": dict(classifications[lang]),
            "low_overlap_collision_risk_count": len(low_risk[lang]),
            "low_overlap_collision_risk": [
                {"family": fam, **cmp, "names": family_details[fam]["languages"]}
                for fam, cmp in sorted(low_risk[lang], key=lambda x: (x[1]["slot_pair_jaccard"], x[1]["card_jaccard"], x[0]))[:100]
            ],
            "review_count": len(medium_review[lang]),
            "review_samples": [
                {"family": fam, **cmp}
                for fam, cmp in sorted(medium_review[lang], key=lambda x: (x[1]["slot_pair_jaccard"], x[1]["card_jaccard"], x[0]))[:100]
            ],
        }

    report = {
        "mode": "read_only_yugioh_regional_set_family_semantics",
        "production_writes": 0,
        "database_transaction_read_only": True,
        "source": {
            "project": "DawnbrandBots/yaml-yugi",
            "master_head_sha": source_head,
            "http_last_modified": source_last_modified or None,
            "cards_seen": source_cards,
        },
        "model_constraint": {
            "set_uniqueness": "Set is unique by (game_id, code); code is currently collector-number family",
            "policy": "Never fabricate JP:/ES: prefixes merely to bypass uniqueness. Same-code low-overlap families are blocked for semantic review.",
            "comparison_signal": "official Konami logical-card membership plus region-normalized collector slot; set names are diagnostic only",
        },
        "database_inventory": {"yugioh_sets": len(db_sets)},
        "targets": targets,
        "family_details": family_details,
    }
    # This is intentionally strict. Medium/tiny samples can be represented by
    # the current family abstraction but remain visible; only strong low-overlap
    # same-code collisions block a writer.
    report["gates"] = {
        "read_only_enforced": True,
        "es_missing_families_have_no_code_collision": targets["es"]["collision_risk_missing_family_candidates"] == 0,
        "ja_missing_families_have_no_code_collision": targets["ja"]["collision_risk_missing_family_candidates"] == 0,
        "es_existing_shared_codes_no_low_overlap_risk": not any(cmp["db_set_exists"] for _fam, cmp in low_risk["es"]),
        "ja_existing_shared_codes_no_low_overlap_risk": not any(cmp["db_set_exists"] for _fam, cmp in low_risk["ja"]),
    }
    report["gate_pass"] = all(report["gates"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": report["mode"], "production_writes": 0, "source": report["source"],
        "database_inventory": report["database_inventory"], "targets": targets,
        "gates": report["gates"], "gate_pass": report["gate_pass"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--source-head", default="")
    ap.add_argument("--source-last-modified", default="")
    args = ap.parse_args()
    run(args.cards, args.report, args.source_head, args.source_last_modified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
