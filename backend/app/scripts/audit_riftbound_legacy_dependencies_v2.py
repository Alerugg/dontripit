from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
from psycopg2 import sql


def normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def count_where(cur, table: str, where_sql: str, params=()) -> int:
    if not table_exists(cur, table):
        return 0
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {} WHERE " + where_sql).format(sql.Identifier(table)), params)
    return int(cur.fetchone()[0])


def ids(cur, query: str, params=()) -> list[int]:
    cur.execute(query, params)
    return [int(row[0]) for row in cur.fetchall()]


def any_ids(values: list[int]) -> list[int]:
    return values or [-1]


def main() -> None:
    database_url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit("No canonical Neon URL configured")

    conn = psycopg2.connect(normalize_url(database_url))
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT id FROM games WHERE slug='riftbound'")
            row = cur.fetchone()
            if not row:
                raise SystemExit("Riftbound game row missing")
            game_id = int(row[0])

            set_ids = ids(cur, "SELECT id FROM sets WHERE game_id=%s ORDER BY id", (game_id,))
            card_ids = ids(cur, "SELECT id FROM cards WHERE game_id=%s ORDER BY id", (game_id,))
            print_ids = ids(
                cur,
                "SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s ORDER BY p.id",
                (game_id,),
            )
            product_ids = ids(cur, "SELECT id FROM products WHERE game_id=%s ORDER BY id", (game_id,)) if table_exists(cur, "products") else []
            product_variant_ids = ids(
                cur,
                "SELECT id FROM product_variants WHERE product_id = ANY(%s) ORDER BY id",
                (any_ids(product_ids),),
            ) if table_exists(cur, "product_variants") else []
            release_ids = ids(cur, "SELECT id FROM catalog_releases WHERE game_id=%s ORDER BY id", (game_id,)) if table_exists(cur, "catalog_releases") else []

            counts = {
                "sets": len(set_ids),
                "cards": len(card_ids),
                "prints": len(print_ids),
                "print_images": count_where(cur, "print_images", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "print_identifiers": count_where(cur, "print_identifiers", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "card_attributes": count_where(cur, "card_attributes", "card_id = ANY(%s)", (any_ids(card_ids),)),
                "print_attributes": count_where(cur, "print_attributes", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "card_search_profiles": count_where(cur, "card_search_profiles", "card_id = ANY(%s)", (any_ids(card_ids),)),
                "print_search_profiles": count_where(cur, "print_search_profiles", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "facet_definitions": count_where(cur, "facet_definitions", "game_id=%s", (game_id,)),
                "prices_by_game": count_where(cur, "prices", "game_id=%s", (game_id,)),
                "prices_by_card": count_where(cur, "prices", "card_id = ANY(%s)", (any_ids(card_ids),)),
                "prices_by_print": count_where(cur, "prices", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "products": len(product_ids),
                "product_variants": len(product_variant_ids),
                "product_images": count_where(cur, "product_images", "product_variant_id = ANY(%s)", (any_ids(product_variant_ids),)),
                "product_identifiers": count_where(cur, "product_identifiers", "product_variant_id = ANY(%s)", (any_ids(product_variant_ids),)),
                "catalog_releases": len(release_ids),
                "print_releases_by_print": count_where(cur, "print_releases", "print_id = ANY(%s)", (any_ids(print_ids),)),
                "print_releases_by_release": count_where(cur, "print_releases", "release_id = ANY(%s)", (any_ids(release_ids),)),
            }

            economic_untyped = {}
            for table in ("price_snapshots", "price_daily_ohlc"):
                if not table_exists(cur, table):
                    continue
                for entity_type, entity_ids in (
                    ("card", card_ids),
                    ("print", print_ids),
                    ("set", set_ids),
                    ("product", product_ids),
                ):
                    economic_untyped[f"{table}:{entity_type}"] = count_where(
                        cur,
                        table,
                        "lower(entity_type)=%s AND entity_id = ANY(%s)",
                        (entity_type, any_ids(entity_ids)),
                    )

            source_records = {}
            if table_exists(cur, "sources") and table_exists(cur, "source_records"):
                cur.execute(
                    """
                    SELECT s.name, COUNT(sr.id)
                    FROM sources s
                    LEFT JOIN source_records sr ON sr.source_id=s.id
                    WHERE lower(s.name) LIKE '%%riftbound%%' OR lower(s.name) LIKE '%%riot%%'
                    GROUP BY s.name
                    ORDER BY s.name
                    """
                )
                source_records = {str(name): int(count) for name, count in cur.fetchall()}

            parent_ids = {
                "games": [game_id],
                "sets": set_ids,
                "cards": card_ids,
                "prints": print_ids,
                "products": product_ids,
                "product_variants": product_variant_ids,
                "catalog_releases": release_ids,
            }

            # Generic single-column FK dependency audit. This catches tables that
            # may have been added to the schema after the explicit checks above.
            cur.execute(
                """
                SELECT
                    child.relname AS child_table,
                    child_att.attname AS child_column,
                    parent.relname AS parent_table,
                    parent_att.attname AS parent_column,
                    con.conname
                FROM pg_constraint con
                JOIN pg_class child ON child.oid=con.conrelid
                JOIN pg_class parent ON parent.oid=con.confrelid
                JOIN LATERAL unnest(con.conkey) WITH ORDINALITY ck(attnum, ord) ON true
                JOIN LATERAL unnest(con.confkey) WITH ORDINALITY fk(attnum, ord) ON fk.ord=ck.ord
                JOIN pg_attribute child_att ON child_att.attrelid=child.oid AND child_att.attnum=ck.attnum
                JOIN pg_attribute parent_att ON parent_att.attrelid=parent.oid AND parent_att.attnum=fk.attnum
                WHERE con.contype='f'
                  AND array_length(con.conkey,1)=1
                  AND parent.relname = ANY(%s)
                ORDER BY parent.relname, child.relname, con.conname
                """,
                (list(parent_ids.keys()),),
            )
            fk_dependencies = []
            for child_table, child_col, parent_table, parent_col, constraint_name in cur.fetchall():
                relevant_ids = parent_ids.get(str(parent_table), [])
                if not relevant_ids:
                    count = 0
                else:
                    query = sql.SQL("SELECT COUNT(*) FROM {} WHERE {} = ANY(%s)").format(
                        sql.Identifier(str(child_table)),
                        sql.Identifier(str(child_col)),
                    )
                    cur.execute(query, (relevant_ids,))
                    count = int(cur.fetchone()[0])
                fk_dependencies.append(
                    {
                        "child_table": str(child_table),
                        "child_column": str(child_col),
                        "parent_table": str(parent_table),
                        "parent_column": str(parent_col),
                        "constraint": str(constraint_name),
                        "matching_rows": count,
                    }
                )

            report = {
                "mode": "read_only",
                "database_writes": 0,
                "game_id": game_id,
                "ids": {
                    "sets": set_ids,
                    "cards": card_ids,
                    "prints": print_ids,
                    "products": product_ids,
                    "product_variants": product_variant_ids,
                    "catalog_releases": release_ids,
                },
                "counts": counts,
                "economic_untyped": economic_untyped,
                "source_records_by_source_name": source_records,
                "fk_dependencies": fk_dependencies,
            }
            conn.rollback()
    finally:
        conn.close()

    output = Path(os.getenv("RIFTBOUND_DEPENDENCY_REPORT", "/tmp/riftbound-legacy-dependencies-v2.json"))
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
