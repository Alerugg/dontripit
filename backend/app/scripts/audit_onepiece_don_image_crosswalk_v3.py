from __future__ import annotations

"""V3 of the read-only One Piece DON physical-image crosswalk.

V2 used an incomplete Cardmarket S3 path of
``category/idProduct/idProduct.jpg`` and therefore classified all current DON
source images as HTTP 403. Cardmarket singles use an expansion directory token:
``category/token/idProduct/idProduct.jpg``.

The mapping below is not inferred from product names. Each current Cardmarket
``idExpansion -> token`` pair was certified on 2026-08-23 by probing the current
official One Piece expansion-code universe and accepting only a unique
first-party, decodable image response for exact current idProducts. Evidence:
GitHub Actions run 32651576221 (0 ambiguous; 161/164 product images readable).

All Bandai PDF integrity checks, perceptual hashes, thresholds, read-only DB
policy and fail-closed incomplete-source behavior remain owned by V2.
"""

from pathlib import Path

from psycopg2.extras import RealDictCursor

from app.scripts import audit_onepiece_don_image_crosswalk_v2 as v2


CERTIFIED_EXPANSION_TOKENS = {
    "5229": "OP01",
    "5244": "OPPR",
    "5262": "STP",
    "5263": "OP02",
    "5303": "UP",
    "5312": "JDG",
    "5364": "OP03",
    "5510": "UP-JP",
    "5551": "OP06-JP",
    "5587": "OP07-JP",
    "5598": "STP-JP",
    "5744": "OP08-JP",
    "5804": "PRB01-JP",
    "5887": "OP09-JP",
    "5975": "OP10-JP",
    "6034": "OP11-JP",
    "6157": "OP12-JP",
    "6233": "PRB02-JP",
    "6277": "OP13-JP",
    "6379": "EB03-JP",
    "6411": "OP14-JP",
    "6432": "OP14",
    "6457": "OP16",
    "6492": "OP17",
    "6501": "DEMO",
    "6504": "OP15-JP",
}


def _market_rows(cur: RealDictCursor) -> list[dict]:
    cur.execute(
        """
        SELECT
          m.metacard_external_id,
          m.representative_external_product_id,
          m.name,
          m.subject,
          m.subject_normalized,
          m.product_count,
          m.source_as_of,
          e.category_id,
          e.expansion_external_id
        FROM onepiece_don_market_items m
        JOIN games g ON g.slug='onepiece'
        JOIN external_catalog_products e
          ON e.source='cardmarket'
         AND e.game_id=g.id
         AND e.product_group='single'
         AND e.external_id=m.representative_external_product_id
        WHERE m.source='cardmarket'
          AND m.source_as_of=(
            SELECT max(source_as_of)
            FROM onepiece_don_market_items
            WHERE source='cardmarket'
          )
        ORDER BY m.metacard_external_id
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    if len(rows) < 150:
        raise AssertionError({"market_rows": len(rows), "minimum": 150})

    unknown = sorted(
        {
            str(row.get("expansion_external_id") or "")
            for row in rows
            if str(row.get("expansion_external_id") or "") not in CERTIFIED_EXPANSION_TOKENS
        }
    )
    if unknown:
        raise AssertionError({"uncertified_cardmarket_expansion_tokens": unknown})
    return rows


def _market_image(row: dict) -> tuple[dict, dict]:
    product_id = str(row["representative_external_product_id"] or "")
    category_id = str(row.get("category_id") or "")
    expansion_id = str(row.get("expansion_external_id") or "")
    token = CERTIFIED_EXPANSION_TOKENS.get(expansion_id)
    if not product_id.isdigit() or not category_id.isdigit() or not token:
        raise ValueError("Cardmarket image path requires certified category/product/expansion identity")

    url = (
        f"https://{v2.CARDMARKET_IMAGE_HOST}/"
        f"{category_id}/{token}/{product_id}/{product_id}.jpg"
    )
    enriched = dict(row)
    enriched["cardmarket_image_token"] = token
    enriched["cardmarket_image_url"] = url
    return enriched, v2._fingerprint(v2._download(url))


_base_rank = v2._rank


def _rank(row: dict, market_fp: dict, official: list[dict]) -> dict:
    result = _base_rank(row, market_fp, official)
    result["expansion_external_id"] = str(row.get("expansion_external_id") or "")
    result["cardmarket_image_token"] = row.get("cardmarket_image_token")
    result["cardmarket_image_url"] = row.get("cardmarket_image_url")
    return result


# Patch only Cardmarket source-path resolution. The V2 main function retains
# every safety rule and intentionally remains fail-closed if any current source
# image is unreadable.
v2._market_rows = _market_rows
v2._market_image = _market_image
v2._rank = _rank


if __name__ == "__main__":
    raise SystemExit(v2.main())
