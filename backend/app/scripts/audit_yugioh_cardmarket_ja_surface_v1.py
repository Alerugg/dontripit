from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

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
        application_name="dontripit_ygo_cardmarket_ja_surface_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _jp_market_marker(row: dict) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("name", "website_path", "category")
    ).casefold()
    return (
        "japanese" in haystack
        or "ocg" in haystack
        or "asia region" in haystack
        or "/jp/" in haystack
    )


def _json_keys(value) -> list[str]:
    return sorted(str(key) for key in value.keys()) if isinstance(value, dict) else []


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.variant,p.is_foil,p.print_key,
                       s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
                ORDER BY p.id
                """,
                (game_id,),
            )
            ja_prints = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT DISTINCT p.card_id,e.metacard_external_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status = ANY(%s)
                  AND e.metacard_external_id IS NOT NULL
                  AND c.game_id=%s
                """,
                (game_id, list(ACCEPTED), game_id),
            )
            card_metacards: dict[int, set[str]] = defaultdict(set)
            for row in cur.fetchall():
                card_metacards[int(row["card_id"])].add(str(row["metacard_external_id"]))

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
                SELECT DISTINCT l.print_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status = ANY(%s)
                  AND lower(coalesce(p.language,''))='ja'
                  AND c.game_id=%s
                """,
                (game_id, list(ACCEPTED), game_id),
            )
            linked_ja = {int(row["print_id"]) for row in cur.fetchall()}
            conn.rollback()
    finally:
        conn.close()

    jp_products = [row for row in products if _jp_market_marker(row)]
    jp_by_metacard: dict[str, list[dict]] = defaultdict(list)
    for row in jp_products:
        meta = row.get("metacard_external_id")
        if meta is not None:
            jp_by_metacard[str(meta)].append(row)

    candidate_count_hist = Counter()
    candidate_samples: list[dict] = []
    prints_with_meta = 0
    for print_row in ja_prints:
        metas = card_metacards.get(int(print_row["card_id"]), set())
        if metas:
            prints_with_meta += 1
        candidates: dict[int, dict] = {}
        for meta in metas:
            for product in jp_by_metacard.get(meta, []):
                candidates[int(product["market_row_id"])] = product
        candidate_count_hist[len(candidates)] += 1
        if len(candidate_samples) < 80 and candidates:
            candidate_samples.append(
                {
                    "print": {
                        "print_id": print_row["print_id"],
                        "card_id": print_row["card_id"],
                        "set_code": print_row["set_code"],
                        "set_name": print_row["set_name"],
                        "set_region": print_row["set_region"],
                        "collector_number": print_row["collector_number"],
                        "rarity": print_row["rarity"],
                        "variant": print_row["variant"],
                    },
                    "metacards": sorted(metas),
                    "candidate_count": len(candidates),
                    "candidate_products": [
                        {
                            "market_row_id": product["market_row_id"],
                            "id_product": product["external_id"],
                            "name": product["name"],
                            "website_path": product["website_path"],
                            "expansion_external_id": product["expansion_external_id"],
                            "raw_keys": _json_keys(product.get("raw_json")),
                            "raw_json": product.get("raw_json"),
                        }
                        for product in list(candidates.values())[:8]
                    ],
                }
            )

    raw_key_hist = Counter()
    for product in jp_products:
        raw_key_hist.update(_json_keys(product.get("raw_json")))

    report = {
        "status": "pass",
        "production_writes": 0,
        "ja_prints": len(ja_prints),
        "ja_prints_already_cardmarket_linked": len(linked_ja),
        "ja_prints_unlinked": len(ja_prints) - len(linked_ja),
        "cardmarket_single_products_total": len(products),
        "jp_ocg_marker_products": len(jp_products),
        "jp_ocg_products_with_metacard": sum(1 for row in jp_products if row.get("metacard_external_id") is not None),
        "ja_prints_with_known_cardmarket_metacard": prints_with_meta,
        "candidate_products_per_ja_print_histogram": {str(k): v for k, v in sorted(candidate_count_hist.items())},
        "jp_product_raw_key_histogram": dict(raw_key_hist.most_common()),
        "jp_product_samples": [
            {
                "market_row_id": row["market_row_id"],
                "id_product": row["external_id"],
                "name": row["name"],
                "website_path": row["website_path"],
                "metacard_external_id": row["metacard_external_id"],
                "expansion_external_id": row["expansion_external_id"],
                "raw_keys": _json_keys(row.get("raw_json")),
                "raw_json": row.get("raw_json"),
            }
            for row in jp_products[:60]
        ],
        "candidate_samples": candidate_samples,
    }
    output = os.getenv("YGO_CARDMARKET_JA_SURFACE_OUTPUT", "/tmp/yugioh-cardmarket-ja-surface-v1.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
