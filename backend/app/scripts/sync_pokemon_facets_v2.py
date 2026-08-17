from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.facets import facets_for_game


EXPECTED_FACETS = 23
REQUIRED_QUICK_FILTERS = {
    "types",
    "stage",
    "rarity",
    "regulation_mark",
    "finish",
    "stamp",
}


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def run(*, report_path: Path | None = None) -> dict:
    definitions = facets_for_game("pokemon")
    if len(definitions) != EXPECTED_FACETS:
        raise AssertionError(
            f"Pokémon facet contract moved: {len(definitions)} != {EXPECTED_FACETS}"
        )

    by_key = {str(row["key"]): dict(row) for row in definitions}
    missing_required = sorted(REQUIRED_QUICK_FILTERS - set(by_key))
    if missing_required:
        raise AssertionError(f"Required Pokémon facets missing from code: {missing_required}")

    not_quick = sorted(
        key for key in REQUIRED_QUICK_FILTERS if not bool(by_key[key].get("quick_filter", False))
    )
    if not_quick:
        raise AssertionError(f"Required Pokémon quick filters are not marked quick: {not_quick}")

    stamp = by_key["stamp"]
    if stamp.get("source_path") != "attributes.stamps":
        raise AssertionError(f"Stamp source path moved unexpectedly: {stamp.get('source_path')!r}")
    if not bool(stamp.get("active", True)):
        raise AssertionError("Stamp facet must be active for final Pokémon certification")

    db.init_engine()
    with db.SessionLocal() as session:
        with session.begin():
            game_id = int(
                session.execute(
                    text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")
                ).scalar_one()
            )

            before = int(
                session.execute(
                    text("SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game"),
                    {"game": game_id},
                ).scalar_one()
            )

            session.execute(
                text("DELETE FROM facet_definitions WHERE game_id=:game"),
                {"game": game_id},
            )

            for definition in definitions:
                row = dict(definition)
                options = row.get("options_json")
                session.execute(
                    text(
                        """
                        INSERT INTO facet_definitions (
                          game_id, scope, key, label, value_type, ui_type, group_name,
                          source_path, multi_value, filterable, sortable, searchable,
                          quick_filter, display_order, options_json, active,
                          created_at, updated_at
                        ) VALUES (
                          :game_id, :scope, :key, :label, :value_type, :ui_type, :group_name,
                          :source_path, :multi_value, :filterable, :sortable, :searchable,
                          :quick_filter, :display_order, CAST(:options_json AS jsonb), :active,
                          now(), now()
                        )
                        """
                    ),
                    {
                        "game_id": game_id,
                        "scope": row["scope"],
                        "key": row["key"],
                        "label": row["label"],
                        "value_type": row["value_type"],
                        "ui_type": row["ui_type"],
                        "group_name": row.get("group_name"),
                        "source_path": row["source_path"],
                        "multi_value": bool(row.get("multi_value", False)),
                        "filterable": bool(row.get("filterable", True)),
                        "sortable": bool(row.get("sortable", False)),
                        "searchable": bool(row.get("searchable", False)),
                        "quick_filter": bool(row.get("quick_filter", False)),
                        "display_order": int(row.get("display_order", 0)),
                        "options_json": json.dumps(options, ensure_ascii=False)
                        if options is not None
                        else "null",
                        "active": bool(row.get("active", True)),
                    },
                )

            after = int(
                session.execute(
                    text("SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game"),
                    {"game": game_id},
                ).scalar_one()
            )
            if after != EXPECTED_FACETS:
                raise AssertionError(f"Facet sync postcondition failed: {after}")

            quick_rows = session.execute(
                text(
                    """
                    SELECT key, quick_filter, active, source_path
                    FROM facet_definitions
                    WHERE game_id=:game AND key = ANY(:keys)
                    ORDER BY key
                    """
                ),
                {"game": game_id, "keys": sorted(REQUIRED_QUICK_FILTERS)},
            ).mappings().all()
            quick_by_key = {str(row["key"]): dict(row) for row in quick_rows}

            if set(quick_by_key) != REQUIRED_QUICK_FILTERS:
                raise AssertionError(
                    f"Required quick-filter rows missing after sync: "
                    f"{sorted(REQUIRED_QUICK_FILTERS - set(quick_by_key))}"
                )
            invalid = sorted(
                key
                for key, row in quick_by_key.items()
                if not bool(row["quick_filter"]) or not bool(row["active"])
            )
            if invalid:
                raise AssertionError(f"Required quick filters inactive after sync: {invalid}")
            if quick_by_key["stamp"]["source_path"] != "attributes.stamps":
                raise AssertionError("Stamp source path was not persisted correctly")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "pokemon_facet_definition_sync_only",
        "game": "pokemon",
        "facet_definitions_before": before,
        "facet_definitions_after": EXPECTED_FACETS,
        "required_quick_filters": sorted(REQUIRED_QUICK_FILTERS),
        "stamp": {
            "quick_filter": True,
            "active": True,
            "source_path": "attributes.stamps",
        },
        "canonical_card_or_print_rows_touched": 0,
        "search_profiles_rebuilt": False,
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
