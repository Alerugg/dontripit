from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _table_exists(session, table_name: str) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table_name}"}).scalar_one())


def _count(session, sql: str, params: dict) -> int:
    return int(session.execute(text(sql), params).scalar_one() or 0)


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one_or_none()
        if game_id is None:
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "read_only_yugioh_dependency_audit",
                "game_present": False,
                "status": "pass",
                "safe_for_shadow_rebuild": True,
                "safe_for_destructive_replace": True,
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
        present_tables = []

        checks = {
            "print_images": ("SELECT COUNT(*) FROM print_images WHERE print_id = ANY(:prints)", {}),
            "print_identifiers": ("SELECT COUNT(*) FROM print_identifiers WHERE print_id = ANY(:prints)", {}),
            "card_attributes": ("SELECT COUNT(*) FROM card_attributes WHERE card_id = ANY(:cards)", {}),
            "print_attributes": ("SELECT COUNT(*) FROM print_attributes WHERE print_id = ANY(:prints)", {}),
            "card_search_profiles": ("SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game", {}),
            "print_search_profiles": ("SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game", {}),
            "facet_definitions": ("SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game", {}),
            "search_documents": ("SELECT COUNT(*) FROM search_documents WHERE game_id=:game", {}),
            "field_provenance_cards": (
                "SELECT COUNT(*) FROM field_provenance WHERE entity_type='card' AND entity_id = ANY(:cards)",
                {"table": "field_provenance"},
            ),
            "field_provenance_prints": (
                "SELECT COUNT(*) FROM field_provenance WHERE entity_type='print' AND entity_id = ANY(:prints)",
                {"table": "field_provenance"},
            ),
            "prices_by_game": ("SELECT COUNT(*) FROM prices WHERE game_id=:game", {"table": "prices"}),
            "prices_by_card": ("SELECT COUNT(*) FROM prices WHERE card_id = ANY(:cards)", {"table": "prices"}),
            "prices_by_print": ("SELECT COUNT(*) FROM prices WHERE print_id = ANY(:prints)", {"table": "prices"}),
            "price_snapshots_card": (
                "SELECT COUNT(*) FROM price_snapshots WHERE entity_type='card' AND entity_id = ANY(:cards)",
                {"table": "price_snapshots"},
            ),
            "price_snapshots_print": (
                "SELECT COUNT(*) FROM price_snapshots WHERE entity_type='print' AND entity_id = ANY(:prints)",
                {"table": "price_snapshots"},
            ),
            "price_daily_ohlc_card": (
                "SELECT COUNT(*) FROM price_daily_ohlc WHERE entity_type='card' AND entity_id = ANY(:cards)",
                {"table": "price_daily_ohlc"},
            ),
            "price_daily_ohlc_print": (
                "SELECT COUNT(*) FROM price_daily_ohlc WHERE entity_type='print' AND entity_id = ANY(:prints)",
                {"table": "price_daily_ohlc"},
            ),
            "products_by_game": ("SELECT COUNT(*) FROM products WHERE game_id=:game", {"table": "products"}),
            "products_linked_to_sets": ("SELECT COUNT(*) FROM products WHERE set_id = ANY(:sets)", {"table": "products"}),
            "catalog_releases": ("SELECT COUNT(*) FROM catalog_releases WHERE game_id=:game", {"table": "catalog_releases"}),
            "print_releases": ("SELECT COUNT(*) FROM print_releases WHERE print_id = ANY(:prints)", {"table": "print_releases"}),
            "market_observations": ("SELECT COUNT(*) FROM market_observations WHERE print_id = ANY(:prints)", {"table": "market_observations"}),
            "market_index_snapshots": ("SELECT COUNT(*) FROM market_index_snapshots WHERE print_id = ANY(:prints)", {"table": "market_index_snapshots"}),
            "holdings_by_print": ("SELECT COUNT(*) FROM holdings WHERE print_id = ANY(:prints)", {"table": "holdings"}),
        }

        for key, (sql, metadata) in checks.items():
            table = metadata.get("table") or key
            # synthetic key names such as field_provenance_cards use an explicit table.
            if not _table_exists(session, table):
                counts[key] = 0
                continue
            present_tables.append(table)
            counts[key] = _count(session, sql, params)

        # Product descendants are durable if any YGO products already exist.
        if counts.get("products_by_game", 0) and _table_exists(session, "product_variants"):
            counts["product_variants"] = _count(session, """
                SELECT COUNT(*) FROM product_variants pv
                JOIN products p ON p.id=pv.product_id
                WHERE p.game_id=:game
            """, params)
            if _table_exists(session, "product_images"):
                counts["product_images"] = _count(session, """
                    SELECT COUNT(*) FROM product_images pi
                    JOIN product_variants pv ON pv.id=pi.product_variant_id
                    JOIN products p ON p.id=pv.product_id
                    WHERE p.game_id=:game
                """, params)
            if _table_exists(session, "product_identifiers"):
                counts["product_identifiers"] = _count(session, """
                    SELECT COUNT(*) FROM product_identifiers pi
                    JOIN product_variants pv ON pv.id=pi.product_variant_id
                    JOIN products p ON p.id=pv.product_id
                    WHERE p.game_id=:game
                """, params)

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
            ORDER BY ccu.table_name, tc.table_name, kcu.column_name
        """)).mappings().all()]

        session.rollback()

    durable_keys = [
        "prices_by_game",
        "prices_by_card",
        "prices_by_print",
        "price_snapshots_card",
        "price_snapshots_print",
        "price_daily_ohlc_card",
        "price_daily_ohlc_print",
        "products_by_game",
        "products_linked_to_sets",
        "product_variants",
        "product_images",
        "product_identifiers",
        "catalog_releases",
        "print_releases",
        "market_observations",
        "market_index_snapshots",
        "holdings_by_print",
    ]
    durable_dependencies = {key: int(counts.get(key, 0)) for key in durable_keys if int(counts.get(key, 0)) > 0}

    rebuildable_keys = [
        "print_images",
        "print_identifiers",
        "card_attributes",
        "print_attributes",
        "card_search_profiles",
        "print_search_profiles",
        "facet_definitions",
        "search_documents",
        "field_provenance_cards",
        "field_provenance_prints",
    ]
    rebuildable_dependencies = {
        key: int(counts.get(key, 0)) for key in rebuildable_keys if int(counts.get(key, 0)) > 0
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_yugioh_dependency_audit",
        "game_present": True,
        "status": "review_required" if durable_dependencies else "pass",
        "legacy_counts": counts,
        "durable_dependencies": durable_dependencies,
        "rebuildable_or_source_recreatable_dependencies": rebuildable_dependencies,
        "foreign_keys_into_core_catalog": fk_rows,
        "safe_for_shadow_rebuild": True,
        "safe_for_destructive_replace": not durable_dependencies,
        "replacement_rule": (
            "Build and validate V2 in shadow/staging first. Before deleting legacy YGO rows, export/recreate source-backed identifiers, images, attributes and search projections. "
            "If durable dependencies are nonzero, migrate/remap them to V2 identities before replacement."
        ),
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
