from __future__ import annotations

import json
import os
from collections import Counter

import psycopg2
from psycopg2.extras import RealDictCursor


TARGET_GAMES = ("yugioh", "onepiece")
ACCEPTED_STATUSES = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_cardmarket_version_grouping_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _fetchall(cur, sql: str, params=None):
    cur.execute(sql, params or ())
    return [dict(row) for row in cur.fetchall()]


def _scalar(cur, sql: str, params=None) -> int:
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return int(next(iter(row.values())) or 0)


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def audit_game(cur, game: str) -> dict:
    game_id_rows = _fetchall(cur, "SELECT id FROM games WHERE slug=%s", (game,))
    if not game_id_rows:
        return {"game": game, "status": "missing_game"}
    game_id = int(game_id_rows[0]["id"])

    total_prints = _scalar(
        cur,
        """
        SELECT COUNT(*) AS n
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
        """,
        (game_id,),
    )

    status_rows = _fetchall(
        cur,
        """
        SELECT l.link_status,l.confidence,COUNT(*) AS links,COUNT(DISTINCT l.print_id) AS prints
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
        GROUP BY l.link_status,l.confidence
        ORDER BY l.link_status,l.confidence
        """,
        (game_id,),
    )

    coverage = _fetchall(
        cur,
        """
        WITH accepted AS (
          SELECT l.print_id,COUNT(DISTINCT e.id) AS product_count
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id
          JOIN cards c ON c.id=p.card_id
          WHERE e.source='cardmarket'
            AND e.game_id=%s
            AND e.product_group='single'
            AND l.link_status = ANY(%s)
            AND c.game_id=%s
          GROUP BY l.print_id
        )
        SELECT
          COUNT(*) FILTER (WHERE product_count=1) AS exactly_one_product,
          COUNT(*) FILTER (WHERE product_count>1) AS ambiguous_products,
          COUNT(*) AS linked_any
        FROM accepted
        """,
        (game_id, list(ACCEPTED_STATUSES), game_id),
    )[0]
    linked_any = int(coverage["linked_any"] or 0)
    exactly_one = int(coverage["exactly_one_product"] or 0)
    ambiguous = int(coverage["ambiguous_products"] or 0)

    product_groups = _fetchall(
        cur,
        """
        WITH accepted AS (
          SELECT DISTINCT e.id,e.external_id,e.name,e.website_path,e.metacard_external_id,
                 e.expansion_external_id,e.last_seen_at,l.print_id,p.card_id,p.language,
                 p.collector_number,p.rarity,p.variant,p.is_foil,p.set_id
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id
          WHERE e.source='cardmarket'
            AND e.game_id=%s
            AND e.product_group='single'
            AND l.link_status = ANY(%s)
        )
        SELECT id,external_id,MAX(name) AS name,MAX(website_path) AS website_path,
               MAX(metacard_external_id) AS metacard_external_id,
               MAX(expansion_external_id) AS expansion_external_id,
               MAX(last_seen_at) AS last_seen_at,
               COUNT(DISTINCT print_id) AS print_count,
               COUNT(DISTINCT card_id) AS card_count,
               COUNT(DISTINCT language) FILTER (WHERE COALESCE(language,'')<>'') AS language_count,
               ARRAY_AGG(DISTINCT lower(language) ORDER BY lower(language)) FILTER (WHERE COALESCE(language,'')<>'') AS languages,
               COUNT(DISTINCT set_id) AS set_count,
               COUNT(DISTINCT collector_number) AS collector_count,
               COUNT(DISTINCT rarity) FILTER (WHERE COALESCE(rarity,'')<>'') AS rarity_count,
               COUNT(DISTINCT variant) FILTER (WHERE COALESCE(variant,'')<>'') AS variant_count,
               COUNT(DISTINCT is_foil) AS finish_count
        FROM accepted
        GROUP BY id,external_id
        ORDER BY language_count DESC,print_count DESC,id
        """,
        (game_id, list(ACCEPTED_STATUSES)),
    )

    language_histogram = Counter(int(row["language_count"] or 0) for row in product_groups)
    multi_language = [row for row in product_groups if int(row["language_count"] or 0) > 1]
    suspicious_multi_card = [row for row in product_groups if int(row["card_count"] or 0) > 1]

    timestamp_rows = _fetchall(
        cur,
        """
        SELECT MIN(last_seen_at) AS min_last_seen,MAX(last_seen_at) AS max_last_seen,
               COUNT(DISTINCT last_seen_at) AS distinct_timestamps,
               COUNT(*) AS product_rows,
               COUNT(*) FILTER (WHERE last_seen_at=(SELECT MAX(e2.last_seen_at) FROM external_catalog_products e2 WHERE e2.source='cardmarket' AND e2.game_id=%s)) AS rows_at_game_max
        FROM external_catalog_products
        WHERE source='cardmarket' AND game_id=%s AND product_group='single'
        """,
        (game_id, game_id),
    )[0]

    language_pairs = _fetchall(
        cur,
        """
        WITH accepted AS (
          SELECT e.id,e.external_id,e.name,l.print_id,p.card_id,p.language,p.collector_number,
                 s.code AS set_code,s.region AS set_region
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id
          JOIN sets s ON s.id=p.set_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
            AND l.link_status = ANY(%s)
        ), multilingual AS (
          SELECT id FROM accepted GROUP BY id HAVING COUNT(DISTINCT language)>1
        )
        SELECT a.id AS market_row_id,a.external_id,a.name,a.card_id,a.language,a.print_id,
               a.collector_number,a.set_code,a.set_region
        FROM accepted a JOIN multilingual m ON m.id=a.id
        ORDER BY a.id,lower(a.language),a.print_id
        LIMIT 80
        """,
        (game_id, list(ACCEPTED_STATUSES)),
    )

    samples = {}
    if game == "yugioh":
        samples["labrynth_cooclock_card_59418"] = _fetchall(
            cur,
            """
            SELECT e.external_id AS id_product,e.name AS market_name,e.website_path,l.link_status,l.confidence,
                   p.id AS print_id,p.language,p.collector_number,p.rarity,p.variant,s.code AS set_code,s.region
            FROM prints p
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN external_catalog_print_links l ON l.print_id=p.id AND l.link_status = ANY(%s)
            LEFT JOIN external_catalog_products e ON e.id=l.external_product_id AND e.source='cardmarket' AND e.product_group='single'
            WHERE p.card_id=59418
            ORDER BY p.language,p.collector_number,p.id
            """,
            (list(ACCEPTED_STATUSES),),
        )
    if game == "onepiece":
        samples["op16_119_catalog_identity"] = _fetchall(
            cur,
            """
            SELECT c.id AS card_id,c.name,p.id AS print_id,p.language,p.collector_number,p.variant,
                   s.code AS set_code,s.region,
                   e.external_id AS id_product,e.name AS market_name,e.website_path,l.link_status,l.confidence
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN external_catalog_print_links l ON l.print_id=p.id AND l.link_status = ANY(%s)
            LEFT JOIN external_catalog_products e ON e.id=l.external_product_id AND e.source='cardmarket' AND e.product_group='single'
            WHERE c.game_id=%s AND upper(COALESCE(p.collector_number,'')) LIKE '%%OP16-119%%'
            ORDER BY c.id,p.language,p.variant,p.id
            LIMIT 80
            """,
            (list(ACCEPTED_STATUSES), game_id),
        )

    return {
        "game": game,
        "total_prints": total_prints,
        "accepted_link_coverage": {
            "linked_any": linked_any,
            "exactly_one_product": exactly_one,
            "ambiguous_products": ambiguous,
            "unlinked": total_prints - linked_any,
        },
        "link_status_distribution": status_rows,
        "cardmarket_single_products_with_accepted_links": len(product_groups),
        "product_language_count_histogram": {str(k): v for k, v in sorted(language_histogram.items())},
        "multi_language_products": len(multi_language),
        "multi_language_examples": multi_language[:20],
        "multi_language_print_examples": language_pairs,
        "products_spanning_multiple_logical_cards": len(suspicious_multi_card),
        "multi_card_examples": suspicious_multi_card[:20],
        "last_seen_semantics": timestamp_rows,
        "samples": samples,
    }


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            report = {
                "status": "pass",
                "production_writes": 0,
                "accepted_statuses": list(ACCEPTED_STATUSES),
                "games": {game: audit_game(cur, game) for game in TARGET_GAMES},
            }
            conn.rollback()
    finally:
        conn.close()

    report = _jsonable(report)
    output = os.getenv("CARDMARKET_GROUPING_AUDIT_OUTPUT", "/tmp/cardmarket-version-grouping-v1.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
