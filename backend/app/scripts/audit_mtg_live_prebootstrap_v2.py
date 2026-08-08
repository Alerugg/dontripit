from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text


GAME_SLUG_CANDIDATES = ("mtg", "magic-the-gathering", "magic")


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("No database URL configured")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _scalar(conn, sql: str, **params) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _table_exists(conn, table_name: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"public.{table_name}"},
        ).scalar_one()
    )


def _count_if(conn, table: str, where_sql: str, **params) -> int | None:
    if not _table_exists(conn, table):
        return None
    return _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {where_sql}", **params)


def run(*, output_path: Path) -> dict:
    engine = create_engine(_db_url(), pool_pre_ping=True)

    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))

            games = [
                {"id": int(row.id), "slug": str(row.slug), "name": str(row.name)}
                for row in conn.execute(text("SELECT id, slug, name FROM games ORDER BY id"))
            ]
            by_slug = {row["slug"]: row for row in games}
            game = next((by_slug[slug] for slug in GAME_SLUG_CANDIDATES if slug in by_slug), None)
            if game is None:
                raise SystemExit(f"Could not find MTG game slug. Existing games: {[g['slug'] for g in games]}")
            game_id = game["id"]

            counts: dict[str, int | None] = {}
            counts["sets"] = _scalar(conn, "SELECT COUNT(*) FROM sets WHERE game_id=:game_id", game_id=game_id)
            counts["cards"] = _scalar(conn, "SELECT COUNT(*) FROM cards WHERE game_id=:game_id", game_id=game_id)
            counts["prints"] = _scalar(
                conn,
                "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id",
                game_id=game_id,
            )
            counts["card_attributes"] = _count_if(
                conn,
                "card_attributes",
                "card_id IN (SELECT id FROM cards WHERE game_id=:game_id)",
                game_id=game_id,
            )
            counts["print_attributes"] = _count_if(
                conn,
                "print_attributes",
                "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )
            counts["print_images"] = _count_if(
                conn,
                "print_images",
                "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )
            counts["print_identifiers"] = _count_if(
                conn,
                "print_identifiers",
                "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )
            counts["products"] = _count_if(conn, "products", "game_id=:game_id", game_id=game_id)
            counts["search_documents"] = _count_if(conn, "search_documents", "game_id=:game_id", game_id=game_id)
            counts["prices_by_game"] = _count_if(conn, "prices", "game_id=:game_id", game_id=game_id)
            counts["prices_by_card"] = _count_if(
                conn,
                "prices",
                "card_id IN (SELECT id FROM cards WHERE game_id=:game_id)",
                game_id=game_id,
            )
            counts["prices_by_print"] = _count_if(
                conn,
                "prices",
                "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )

            # Polymorphic economics/provenance tables are not protected by FKs,
            # so inspect them explicitly before any destructive rebuild.
            counts["price_snapshots_card"] = _count_if(
                conn,
                "price_snapshots",
                "entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=:game_id)",
                game_id=game_id,
            )
            counts["price_snapshots_print"] = _count_if(
                conn,
                "price_snapshots",
                "entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )
            counts["price_daily_ohlc_card"] = _count_if(
                conn,
                "price_daily_ohlc",
                "entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=:game_id)",
                game_id=game_id,
            )
            counts["price_daily_ohlc_print"] = _count_if(
                conn,
                "price_daily_ohlc",
                "entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )
            counts["field_provenance_card"] = _count_if(
                conn,
                "field_provenance",
                "entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=:game_id)",
                game_id=game_id,
            )
            counts["field_provenance_print"] = _count_if(
                conn,
                "field_provenance",
                "entity_type='print' AND entity_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id)",
                game_id=game_id,
            )

            fk_rows = []
            for row in conn.execute(text("""
                SELECT
                  con.conname,
                  child.relname AS child_table,
                  parent.relname AS parent_table,
                  pg_get_constraintdef(con.oid) AS definition
                FROM pg_constraint con
                JOIN pg_class child ON child.oid=con.conrelid
                JOIN pg_class parent ON parent.oid=con.confrelid
                JOIN pg_namespace nsp ON nsp.oid=child.relnamespace
                WHERE con.contype='f'
                  AND nsp.nspname='public'
                  AND parent.relname IN ('cards','prints','sets')
                ORDER BY parent.relname, child.relname, con.conname
            """)):
                fk_rows.append(
                    {
                        "constraint": str(row.conname),
                        "child_table": str(row.child_table),
                        "parent_table": str(row.parent_table),
                        "definition": str(row.definition),
                    }
                )

            table_sizes = {}
            for table in (
                "sets", "cards", "prints", "card_attributes", "print_attributes",
                "print_images", "print_identifiers", "prices", "price_snapshots",
                "price_daily_ohlc", "field_provenance", "search_documents",
            ):
                if _table_exists(conn, table):
                    table_sizes[table] = _scalar(
                        conn,
                        "SELECT pg_total_relation_size(CAST(:table_name AS regclass))",
                        table_name=table,
                    )

            database_bytes = _scalar(conn, "SELECT pg_database_size(current_database())")
            alembic_version = str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())

            economics_keys = [
                "prices_by_game", "prices_by_card", "prices_by_print",
                "price_snapshots_card", "price_snapshots_print",
                "price_daily_ohlc_card", "price_daily_ohlc_print",
            ]
            economics_rows = sum(int(counts.get(key) or 0) for key in economics_keys)

            report = {
                "status": "pass",
                "mode": "read_only",
                "database_writes": 0,
                "game": game,
                "alembic_version": alembic_version,
                "database_bytes": database_bytes,
                "counts": counts,
                "economics_rows_detected": economics_rows,
                "safe_to_replace_without_economics_loss": economics_rows == 0,
                "foreign_keys_to_catalog_identity": fk_rows,
                "table_sizes_bytes": table_sizes,
            }
            tx.rollback()
        except Exception:
            tx.rollback()
            raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only live MTG prebootstrap audit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(output_path=args.output)


if __name__ == "__main__":
    main()
