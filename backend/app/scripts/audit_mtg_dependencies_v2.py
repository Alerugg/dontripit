from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _exists(session, table: str) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}).scalar_one())


def _count(session, sql: str, params: dict) -> int:
    return int(session.execute(text(sql), params).scalar_one() or 0)


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='mtg' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "read_only_mtg_dependency_audit_v2",
                "status": "pass",
                "game_present": False,
                "safe_for_shadow_rebuild": True,
                "safe_for_destructive_replace": True,
                "database_writes": 0,
            }
            _write(report_path, report)
            print(json.dumps(report, indent=2))
            return report

        game_id = int(game_id)
        card_ids = [int(v) for v in session.execute(text("SELECT id FROM cards WHERE game_id=:game"), {"game": game_id}).scalars().all()]
        set_ids = [int(v) for v in session.execute(text("SELECT id FROM sets WHERE game_id=:game"), {"game": game_id}).scalars().all()]
        print_ids = [int(v) for v in session.execute(text(
            "SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game"
        ), {"game": game_id}).scalars().all()]
        params = {"game": game_id, "cards": card_ids or [-1], "sets": set_ids or [-1], "prints": print_ids or [-1]}

        counts: dict[str, int] = {
            "sets": len(set_ids),
            "cards": len(card_ids),
            "prints": len(print_ids),
        }
        checks = {
            "print_images": ("print_images", "SELECT COUNT(*) FROM print_images WHERE print_id = ANY(:prints)"),
            "print_identifiers": ("print_identifiers", "SELECT COUNT(*) FROM print_identifiers WHERE print_id = ANY(:prints)"),
            "card_attributes": ("card_attributes", "SELECT COUNT(*) FROM card_attributes WHERE card_id = ANY(:cards)"),
            "print_attributes": ("print_attributes", "SELECT COUNT(*) FROM print_attributes WHERE print_id = ANY(:prints)"),
            "card_search_profiles": ("card_search_profiles", "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game"),
            "print_search_profiles": ("print_search_profiles", "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game"),
            "facet_definitions": ("facet_definitions", "SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game"),
            "search_documents": ("search_documents", "SELECT COUNT(*) FROM search_documents WHERE game_id=:game"),
            "field_provenance_cards": ("field_provenance", "SELECT COUNT(*) FROM field_provenance WHERE entity_type='card' AND entity_id = ANY(:cards)"),
            "field_provenance_prints": ("field_provenance", "SELECT COUNT(*) FROM field_provenance WHERE entity_type='print' AND entity_id = ANY(:prints)"),
            "prices_by_game": ("prices", "SELECT COUNT(*) FROM prices WHERE game_id=:game"),
            "prices_by_card": ("prices", "SELECT COUNT(*) FROM prices WHERE card_id = ANY(:cards)"),
            "prices_by_print": ("prices", "SELECT COUNT(*) FROM prices WHERE print_id = ANY(:prints)"),
            "price_snapshots_card": ("price_snapshots", "SELECT COUNT(*) FROM price_snapshots WHERE entity_type='card' AND entity_id = ANY(:cards)"),
            "price_snapshots_print": ("price_snapshots", "SELECT COUNT(*) FROM price_snapshots WHERE entity_type='print' AND entity_id = ANY(:prints)"),
            "price_daily_ohlc_card": ("price_daily_ohlc", "SELECT COUNT(*) FROM price_daily_ohlc WHERE entity_type='card' AND entity_id = ANY(:cards)"),
            "price_daily_ohlc_print": ("price_daily_ohlc", "SELECT COUNT(*) FROM price_daily_ohlc WHERE entity_type='print' AND entity_id = ANY(:prints)"),
            "products_by_game": ("products", "SELECT COUNT(*) FROM products WHERE game_id=:game"),
            "products_linked_to_sets": ("products", "SELECT COUNT(*) FROM products WHERE set_id = ANY(:sets)"),
            "catalog_releases": ("catalog_releases", "SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game"),
            "print_releases": ("print_releases", "SELECT COUNT(*) FROM print_releases WHERE print_id = ANY(:prints)"),
            "market_observations": ("market_observations", "SELECT COUNT(*) FROM market_observations WHERE print_id = ANY(:prints)"),
            "market_index_snapshots": ("market_index_snapshots", "SELECT COUNT(*) FROM market_index_snapshots WHERE print_id = ANY(:prints)"),
            "holdings_by_print": ("holdings", "SELECT COUNT(*) FROM holdings WHERE print_id = ANY(:prints)"),
        }
        for key, (table, sql) in checks.items():
            counts[key] = _count(session, sql, params) if _exists(session, table) else 0

        # Enumerate every real FK into the shared canonical core and count MTG rows
        # that use it. Views are intentionally excluded because they are derived.
        fk_rows = [dict(row) for row in session.execute(text("""
            SELECT
              tc.table_name AS dependent_table,
              kcu.column_name AS dependent_column,
              ccu.table_name AS referenced_table,
              ccu.column_name AS referenced_column,
              rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.constraint_schema=kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name=tc.constraint_name
             AND ccu.constraint_schema=tc.constraint_schema
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name=tc.constraint_name
             AND rc.constraint_schema=tc.constraint_schema
            WHERE tc.constraint_type='FOREIGN KEY'
              AND tc.table_schema='public'
              AND ccu.table_name IN ('sets','cards','prints')
              AND ccu.column_name='id'
            ORDER BY ccu.table_name, tc.table_name, kcu.column_name
        """)).mappings().all()]

        fk_usage = []
        ids_by_table = {"sets": set_ids or [-1], "cards": card_ids or [-1], "prints": print_ids or [-1]}
        for row in fk_rows:
            table = str(row["dependent_table"])
            column = str(row["dependent_column"])
            referenced = str(row["referenced_table"])
            if not _SAFE_IDENT.fullmatch(table) or not _SAFE_IDENT.fullmatch(column):
                raise AssertionError(f"Unsafe FK metadata identifier: {table}.{column}")
            rows = _count(
                session,
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = ANY(:ids)',
                {"ids": ids_by_table[referenced]},
            )
            fk_usage.append({**row, "mtg_rows": rows})

        source = session.execute(text("SELECT id FROM sources WHERE name='scryfall_mtg' LIMIT 1")).scalar_one_or_none()
        if source is not None and _exists(session, "source_records"):
            counts["scryfall_source_records"] = _count(session, "SELECT COUNT(*) FROM source_records WHERE source_id=:source", {"source": int(source)})
            counts["scryfall_source_record_payload_bytes"] = _count(session, "SELECT COALESCE(SUM(pg_column_size(raw_json)),0) FROM source_records WHERE source_id=:source", {"source": int(source)})
        else:
            counts["scryfall_source_records"] = 0
            counts["scryfall_source_record_payload_bytes"] = 0
        session.rollback()

    durable_keys = (
        "prices_by_game", "prices_by_card", "prices_by_print", "price_snapshots_card", "price_snapshots_print",
        "price_daily_ohlc_card", "price_daily_ohlc_print", "products_by_game", "products_linked_to_sets",
        "catalog_releases", "print_releases", "market_observations", "market_index_snapshots", "holdings_by_print",
    )
    rebuildable_keys = (
        "print_images", "print_identifiers", "card_attributes", "print_attributes", "card_search_profiles",
        "print_search_profiles", "facet_definitions", "search_documents", "field_provenance_cards", "field_provenance_prints",
    )
    durable = {key: counts[key] for key in durable_keys if counts.get(key, 0)}
    rebuildable = {key: counts[key] for key in rebuildable_keys if counts.get(key, 0)}

    # Any FK usage not already classified as rebuildable/durable is surfaced as
    # an unknown replacement blocker rather than silently ignored.
    known_tables = {
        "prints", "print_images", "print_identifiers", "card_attributes", "print_attributes",
        "card_search_profiles", "print_search_profiles", "products", "prices", "print_releases",
    }
    unknown_fk_usage = [row for row in fk_usage if row["mtg_rows"] and row["dependent_table"] not in known_tables]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_mtg_dependency_audit_v2",
        "status": "review_required" if durable or unknown_fk_usage else "pass",
        "game_present": True,
        "legacy_counts": counts,
        "durable_dependencies": durable,
        "rebuildable_dependencies": rebuildable,
        "foreign_key_usage": fk_usage,
        "unknown_fk_usage": unknown_fk_usage,
        "source_record_note": "Scryfall SourceRecords are checksum/provenance records and do not reference canonical Card/Print IDs. Existing historical raw payloads can be retained or compacted separately from identity replacement.",
        "safe_for_shadow_rebuild": True,
        "safe_for_destructive_replace": not durable and not unknown_fk_usage,
        "database_writes": 0,
    }
    _write(report_path, report)
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
