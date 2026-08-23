from __future__ import annotations

"""Certified Cardmarket media identity for source-owned One Piece DON!! rows.

Cardmarket singles image URLs require an expansion-directory token between the
numeric category and product IDs. These current ``idExpansion -> token`` pairs
were certified read-only on 2026-08-23 by probing Cardmarket's current official
One Piece expansion-code universe and accepting only unique, decodable first-
party image responses for exact current DON products (Actions run 32651576221).

Unknown future expansions deliberately fail closed until certified. This module
only resolves media paths; it never creates canonical Card/Print identity.
"""

CARDMARKET_IMAGE_HOST = "product-images.s3.cardmarket.com"

CARDMARKET_DON_EXPANSION_TOKENS: dict[str, str] = {
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


def cardmarket_don_source_url(
    *,
    category_id: str | int | None,
    expansion_external_id: str | int | None,
    product_id: str | int | None,
) -> str | None:
    category = str(category_id or "").strip()
    expansion = str(expansion_external_id or "").strip()
    product = str(product_id or "").strip()
    token = CARDMARKET_DON_EXPANSION_TOKENS.get(expansion)
    if not category.isdigit() or not product.isdigit() or not token:
        return None
    return f"https://{CARDMARKET_IMAGE_HOST}/{category}/{token}/{product}/{product}.jpg"


def onepiece_don_proxy_path(metacard_external_id: str | int | None) -> str | None:
    metacard_id = str(metacard_external_id or "").strip()
    if not metacard_id.isdigit():
        return None
    return f"/media/onepiece/don/{metacard_id}/cardmarket-image"
