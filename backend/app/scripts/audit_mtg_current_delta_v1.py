from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app import db
from app.models import Game
from app.scripts.reconcile_mtg_current_source_v1 import load_snapshot, _production_state


def _build_unbounded_delta(*, source_sets, source_cards, source_prints, prod_sets, prod_cards, prod_prints):
    new_sets = sorted(set(source_sets) - set(prod_sets))
    new_cards = sorted(set(source_cards) - set(prod_cards))
    new_prints = sorted(set(source_prints) - set(prod_prints))
    production_extra_sets = sorted(set(prod_sets) - set(source_sets))
    production_extra_cards = sorted(set(prod_cards) - set(source_cards))
    production_extra_prints = sorted(set(prod_prints) - set(source_prints))

    collector_corrections = []
    forbidden = []
    for print_key in sorted(set(source_prints) & set(prod_prints)):
        src = source_prints[print_key]
        prod = prod_prints[print_key]
        fields = {
            "card_key": (src.get("card_key"), prod.get("card_key")),
            "set_code": (src.get("set_code"), prod.get("set_code")),
            "language": (src.get("language"), prod.get("language")),
            "rarity": (src.get("rarity"), prod.get("rarity")),
            "is_foil": (bool(src.get("is_foil")), bool(prod.get("is_foil"))),
            "variant": (src.get("variant"), prod.get("variant")),
            "scryfall_id": (src.get("scryfall_id"), prod.get("scryfall_id")),
        }
        differing = [name for name, (expected, actual) in fields.items() if expected != actual]
        collector_diff = str(src.get("collector_number") or "") != str(prod.get("collector_number") or "")
        if differing:
            forbidden.append({"print_key": print_key, "fields": differing})
        elif collector_diff:
            collector_corrections.append(print_key)

    return {
        "new_sets": new_sets,
        "new_cards": new_cards,
        "new_prints": new_prints,
        "collector_corrections": collector_corrections,
        "forbidden_mismatches": forbidden,
        "production_extra_sets": production_extra_sets,
        "production_extra_cards": production_extra_cards,
        "production_extra_prints": production_extra_prints,
    }


def run(*, snapshot_dir: Path, output: Path) -> dict:
    source_sets, source_cards, source_prints, manifest = load_snapshot(snapshot_dir)
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
        prod_sets, prod_cards, prod_prints = _production_state(session, game.id)
        delta = _build_unbounded_delta(
            source_sets=source_sets,
            source_cards=source_cards,
            source_prints=source_prints,
            prod_sets=prod_sets,
            prod_cards=prod_cards,
            prod_prints=prod_prints,
        )
        session.rollback()

    new_print_rows = [source_prints[key] for key in delta["new_prints"]]
    by_set = Counter(str(row.get("set_code") or "") for row in new_print_rows)
    by_variant = Counter(str(row.get("variant") or "") for row in new_print_rows)
    by_language = Counter(str(row.get("language") or "") for row in new_print_rows)
    by_card = Counter(str(row.get("card_key") or "") for row in new_print_rows)
    by_scryfall = Counter(str(row.get("scryfall_id") or "") for row in new_print_rows)

    finish_multiplicity = Counter(by_scryfall.values())
    set_release = {
        code: (source_sets.get(code) or {}).get("release_date")
        for code in by_set
    }
    new_prints_by_set = [
        {
            "set_code": code,
            "count": count,
            "release_date": set_release.get(code),
            "set_is_new": code in set(delta["new_sets"]),
        }
        for code, count in by_set.most_common()
    ]

    parent_state = Counter()
    for row in new_print_rows:
        card_key = str(row.get("card_key") or "")
        set_code = str(row.get("set_code") or "")
        card_new = card_key in set(delta["new_cards"])
        set_new = set_code in set(delta["new_sets"])
        if card_new and set_new:
            parent_state["new_card_and_set"] += 1
        elif card_new:
            parent_state["new_card_existing_set"] += 1
        elif set_new:
            parent_state["existing_card_new_set"] += 1
        else:
            parent_state["existing_card_and_set"] += 1

    report = {
        "status": "pass" if not delta["forbidden_mismatches"] else "fail",
        "mode": "read-only-audit",
        "production_writes": 0,
        "source": manifest.get("source"),
        "snapshot_schema_version": manifest.get("snapshot_schema_version"),
        "source_counts": manifest.get("counts"),
        "delta_counts": {
            "new_sets": len(delta["new_sets"]),
            "new_cards": len(delta["new_cards"]),
            "new_prints": len(delta["new_prints"]),
            "new_scryfall_objects": len(by_scryfall),
            "collector_corrections": len(delta["collector_corrections"]),
            "forbidden_mismatches": len(delta["forbidden_mismatches"]),
            "production_extra_sets_preserved": len(delta["production_extra_sets"]),
            "production_extra_cards_preserved": len(delta["production_extra_cards"]),
            "production_extra_prints_preserved": len(delta["production_extra_prints"]),
        },
        "new_print_breakdown": {
            "by_variant": dict(sorted(by_variant.items())),
            "by_language": dict(sorted(by_language.items())),
            "finish_multiplicity_per_scryfall_object": {str(k): v for k, v in sorted(finish_multiplicity.items())},
            "parent_state": dict(sorted(parent_state.items())),
            "by_set": new_prints_by_set[:100],
            "top_cards": [{"card_key": key, "count": count} for key, count in by_card.most_common(50)],
        },
        "samples": {
            "new_sets": delta["new_sets"][:50],
            "new_cards": delta["new_cards"][:50],
            "new_prints": delta["new_prints"][:100],
            "collector_corrections": delta["collector_corrections"][:100],
            "forbidden_mismatches": delta["forbidden_mismatches"][:100],
        },
        "safety": {
            "deletes": 0,
            "image_writes": 0,
            "cardmarket_writes": 0,
            "price_writes": 0,
            "production_extras_preserved": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "delta_counts": report["delta_counts"],
        "new_print_breakdown": report["new_print_breakdown"],
        "samples": report["samples"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    if delta["forbidden_mismatches"]:
        raise AssertionError("MTG current delta contains forbidden mismatches")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only unbounded MTG current-source delta audit")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output=args.output)


if __name__ == "__main__":
    main()
