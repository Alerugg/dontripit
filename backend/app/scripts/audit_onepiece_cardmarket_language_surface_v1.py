from __future__ import annotations

import json
import os
from collections import Counter

import psycopg2
from psycopg2.extras import RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_onepiece_cardmarket_language_surface_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _asia_marker(row: dict) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("name", "website_path", "category")).casefold()
    return (
        "asia region" in text
        or "asia-region" in text
        or "japanese" in text
        or "/jp/" in text
        or "op16-jp" in text
    )


def _raw_keys(value):
    return sorted(str(key) for key in value.keys()) if isinstance(value, dict) else []


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1")
            game_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                SELECT lower(coalesce(p.language,'')) AS language,s.region,COUNT(*) AS prints,
                       COUNT(*) FILTER (WHERE EXISTS (
                         SELECT 1 FROM external_catalog_print_links l
                         JOIN external_catalog_products e ON e.id=l.external_product_id
                         WHERE l.print_id=p.id AND e.source='cardmarket' AND e.game_id=%s
                           AND e.product_group='single' AND l.link_status = ANY(%s)
                       )) AS linked_prints
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s
                GROUP BY lower(coalesce(p.language,'')),s.region
                ORDER BY 1,2
                """,
                (game_id, list(ACCEPTED), game_id),
            )
            print_surface = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT e.id AS market_row_id,e.external_id,e.name,e.category,e.website_path,
                       e.metacard_external_id,e.expansion_external_id,e.raw_json,e.last_seen_at
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                ORDER BY e.id
                """,
                (game_id,),
            )
            products = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT c.id AS card_id,c.name,p.id AS print_id,p.language,p.collector_number,p.variant,p.rarity,
                       s.code AS set_code,s.name AS set_name,s.region,
                       e.external_id AS id_product,e.name AS market_name,e.website_path,l.link_status,l.confidence
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                LEFT JOIN external_catalog_print_links l ON l.print_id=p.id AND l.link_status = ANY(%s)
                LEFT JOIN external_catalog_products e ON e.id=l.external_product_id
                  AND e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                WHERE c.game_id=%s AND upper(coalesce(p.collector_number,'')) LIKE '%%OP16-119%%'
                ORDER BY p.language,p.variant,p.id
                """,
                (list(ACCEPTED), game_id, game_id),
            )
            op16_119 = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    asia_products = [row for row in products if _asia_marker(row)]
    key_hist = Counter()
    for row in asia_products:
        key_hist.update(_raw_keys(row.get("raw_json")))

    report = {
        "status": "pass",
        "production_writes": 0,
        "print_surface": print_surface,
        "total_prints": sum(int(row["prints"] or 0) for row in print_surface),
        "accepted_linked_prints": sum(int(row["linked_prints"] or 0) for row in print_surface),
        "cardmarket_single_products_total": len(products),
        "asia_jp_marker_products": len(asia_products),
        "asia_jp_products_with_metacard": sum(1 for row in asia_products if row.get("metacard_external_id") is not None),
        "asia_product_raw_key_histogram": dict(key_hist.most_common()),
        "asia_product_samples": [
            {
                "market_row_id": row["market_row_id"],
                "id_product": row["external_id"],
                "name": row["name"],
                "website_path": row["website_path"],
                "metacard_external_id": row["metacard_external_id"],
                "expansion_external_id": row["expansion_external_id"],
                "raw_keys": _raw_keys(row.get("raw_json")),
                "raw_json": row.get("raw_json"),
            }
            for row in asia_products[:80]
        ],
        "op16_119": op16_119,
    }
    output = os.getenv("ONEPIECE_CARDMARKET_LANGUAGE_SURFACE_OUTPUT", "/tmp/onepiece-cardmarket-language-surface-v1.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
