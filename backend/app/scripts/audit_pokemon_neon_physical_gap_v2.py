from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app import db
from app.pokemon_source_inventory import load_inventory
from app.scripts.audit_pokemon_neon_gap import (
    _collect_direct_tcgdex_ids,
    _collect_identifier_tcgdex_ids,
    _pokemon_game,
    _pokemon_print_filter,
)


def run() -> dict:
    inventory = load_inventory()
    physical_cards = inventory.physical_cards
    pocket_cards = inventory.pocket_cards
    physical_ids = set(physical_cards)
    pocket_ids = set(pocket_cards)
    all_source_ids = set(inventory.cards)

    db.init_engine()
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    with db.SessionLocal() as session:
        game = _pokemon_game(session, tables)
        if not game:
            raise AssertionError("Pokémon game row is missing from Neon")
        game_id = int(game["id"])
        joins, predicate = _pokemon_print_filter(inspector, tables)

        direct_rows, direct_evidence = _collect_direct_tcgdex_ids(session, inspector, tables, game_id)
        identifier_rows, identifier_evidence = _collect_identifier_tcgdex_ids(session, inspector, tables, game_id)

        ids_by_print: dict[int, set[str]] = defaultdict(set)
        vias_by_print: dict[int, set[str]] = defaultdict(set)
        for row in direct_rows + identifier_rows:
            external_id = str(row.get("external_id") or "").strip()
            if not external_id:
                continue
            ids_by_print[int(row["print_id"])].add(external_id)
            vias_by_print[int(row["print_id"])].add(str(row.get("via") or ""))

        db_ids = [external_id for values in ids_by_print.values() for external_id in values]
        db_id_counts = Counter(db_ids)
        db_id_set = set(db_ids)

        matched_physical = physical_ids & db_id_set
        missing_physical = sorted(physical_ids - db_id_set)
        pocket_pollution = sorted(db_id_set & pocket_ids)
        stale_db_ids = sorted(db_id_set - all_source_ids)
        duplicate_db_ids = sorted([external_id for external_id, count in db_id_counts.items() if count > 1])

        print_count = int(session.execute(text(
            f"SELECT COUNT(*) FROM prints p {joins} WHERE {predicate}"
        ), {"game_id": game_id}).scalar_one())

        # Set completeness must be measured by Set.tcgdex_id, not by whether a
        # set currently owns a card in TCGdex /cards. Four valid physical source
        # set records (jumbo, rc, sp, wp) intentionally have zero global cards.
        physical_set_ids = {row["set_id"] for row in inventory.physical_sets}
        db_set_ids = {
            str(value)
            for value in session.execute(text(
                "SELECT tcgdex_id FROM sets WHERE game_id=:game_id AND tcgdex_id IS NOT NULL"
            ), {"game_id": game_id}).scalars().all()
            if value
        }
        matched_set_ids = physical_set_ids & db_set_ids
        missing_sets = sorted(physical_set_ids - db_set_ids)

        source_set_expected = Counter(row["set_id"] for row in physical_cards.values() if row.get("set_id"))
        source_set_matched = Counter(physical_cards[source_id]["set_id"] for source_id in matched_physical)
        zero_card_source_sets = sorted([
            set_id for set_id in physical_set_ids if source_set_expected[set_id] == 0
        ])
        partial_sets = [
            {
                "set_id": set_id,
                "set_name": next((row.get("set_name") for row in inventory.physical_sets if row["set_id"] == set_id), None),
                "series": next((row.get("series") for row in inventory.physical_sets if row["set_id"] == set_id), None),
                "source_cards": source_set_expected[set_id],
                "matched_cards": source_set_matched[set_id],
                "missing_cards": source_set_expected[set_id] - source_set_matched[set_id],
            }
            for set_id in sorted(physical_set_ids)
            if source_set_expected[set_id] > 0
            and 0 < source_set_matched[set_id] < source_set_expected[set_id]
        ]
        partial_sets.sort(key=lambda row: (-row["missing_cards"], row["set_id"]))

        cards_table_count = int(session.execute(text(
            "SELECT COUNT(*) FROM cards WHERE game_id=:game_id"
        ), {"game_id": game_id}).scalar_one())
        sets_table_count = int(session.execute(text(
            "SELECT COUNT(*) FROM sets WHERE game_id=:game_id"
        ), {"game_id": game_id}).scalar_one())

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only",
            "classification": "physical_pokemon_tcg_only",
            "source": {
                "physical_sets": len(inventory.physical_sets),
                "physical_cards": len(physical_cards),
                "source_sets_with_zero_cards": len(zero_card_source_sets),
                "pocket_sets_excluded": len(inventory.pocket_sets),
                "pocket_cards_excluded": len(pocket_cards),
                "unassigned_cards": len(inventory.unassigned_cards),
            },
            "neon": {
                "sets": sets_table_count,
                "cards": cards_table_count,
                "prints": print_count,
                "physical_source_sets_by_tcgdex_id": len(matched_set_ids),
                "prints_with_any_tcgdex_identity": len(ids_by_print),
                "prints_without_any_tcgdex_identity": max(0, print_count - len(ids_by_print)),
                "identity_paths": {
                    "direct_columns": direct_evidence,
                    "identifier_table": identifier_evidence,
                },
            },
            "gap": {
                "matched_physical_source_sets": len(matched_set_ids),
                "missing_physical_source_sets": len(missing_sets),
                # Backwards-compatible key retained for the bootstrap workflow.
                # It now correctly means missing canonical set identities.
                "physical_sets_with_zero_matches": len(missing_sets),
                "source_sets_with_zero_cards": len(zero_card_source_sets),
                "matched_physical_source_cards": len(matched_physical),
                "missing_physical_source_cards": len(missing_physical),
                "physical_source_coverage": round(len(matched_physical) / len(physical_ids), 6) if physical_ids else 1.0,
                "physical_sets_partial": len(partial_sets),
                "pocket_ids_in_pokemon_neon": len(pocket_pollution),
                "stale_db_tcgdex_ids": len(stale_db_ids),
                "duplicate_db_tcgdex_ids": len(duplicate_db_ids),
            },
            "missing_physical_set_ids": missing_sets,
            "zero_card_source_set_ids": zero_card_source_sets,
            "partial_sets": partial_sets,
            "pocket_pollution_ids": pocket_pollution[:200],
            "stale_db_id_samples": stale_db_ids[:200],
            "duplicate_db_id_samples": duplicate_db_ids[:200],
            "missing_physical_card_samples": [
                physical_cards[source_id] for source_id in missing_physical[:100]
            ],
            "status": "pass",
        }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
