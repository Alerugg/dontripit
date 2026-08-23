from __future__ import annotations

from sqlalchemy import text

from app.onepiece_don_media import onepiece_don_proxy_url
from app.search_v2.normalization import normalize_search_text


_ALL_DON_TOKENS = {"don", "don card"}


def onepiece_don_market_page(
    session,
    *,
    query: str,
    limit: int = 24,
    offset: int = 0,
) -> dict:
    """Search certified source-owned DON!! identities without inventing Prints.

    `onepiece_don_market_items` is deliberately separate from the canonical
    Card/Print graph. Until an artwork has a deterministic physical crosswalk,
    this function returns a `don_market` row with source identifiers and never
    fabricates a collector number, language, rarity, Card id, or Print id.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("One Piece DON source search requires PostgreSQL")

    q_norm = normalize_search_text(query or "")
    limit = max(1, min(int(limit or 24), 100))
    offset = max(0, int(offset or 0))
    if not q_norm:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "next_offset": None,
            "identity_scope": "source_owned",
        }

    params: dict[str, object] = {
        "source": "cardmarket",
        "query": q_norm,
        "contains": f"%{q_norm}%",
        "limit": limit,
        "offset": offset,
    }
    subject_predicate = "TRUE" if q_norm in _ALL_DON_TOKENS else "m.subject_normalized LIKE :contains"

    sql = text(
        f"""
        WITH latest_source AS MATERIALIZED (
          SELECT max(source_as_of) AS source_as_of
          FROM onepiece_don_market_items
          WHERE source = :source
        ),
        matched AS MATERIALIZED (
          SELECT
            m.id,
            m.metacard_external_id,
            m.representative_external_product_id,
            m.name,
            m.subject,
            m.subject_normalized,
            m.product_ids_json,
            m.product_count,
            m.source_as_of,
            m.official_item_id,
            m.mapping_source,
            m.mapping_confidence
          FROM onepiece_don_market_items m
          JOIN latest_source latest ON latest.source_as_of = m.source_as_of
          WHERE m.source = :source
            AND ({subject_predicate})
        ),
        paged AS MATERIALIZED (
          SELECT matched.*, COUNT(*) OVER ()::bigint AS total
          FROM matched
          ORDER BY
            CASE WHEN subject_normalized = :query THEN 0 ELSE 1 END,
            lower(COALESCE(subject, name)) ASC,
            metacard_external_id ASC
          LIMIT :limit OFFSET :offset
        )
        SELECT
          paged.*,
          market.external_id AS cardmarket_id_product,
          market.product_name AS cardmarket_product_name,
          market.website_path AS cardmarket_website_path,
          market.category_id AS cardmarket_category_id,
          market.price AS cardmarket_price,
          market.price_variant AS cardmarket_price_variant,
          market.currency AS cardmarket_currency,
          market.as_of AS cardmarket_as_of
        FROM paged
        LEFT JOIN LATERAL (
          SELECT
            e.external_id,
            e.name AS product_name,
            e.website_path,
            e.category_id,
            current_price.price,
            current_price.price_variant,
            current_price.currency,
            current_price.as_of
          FROM jsonb_array_elements_text(COALESCE(paged.product_ids_json, '[]'::jsonb)) product_id(value)
          JOIN games g ON g.slug = 'onepiece'
          JOIN external_catalog_products e
            ON e.source = 'cardmarket'
           AND e.game_id = g.id
           AND e.product_group = 'single'
           AND e.external_id = product_id.value
          LEFT JOIN LATERAL (
            SELECT
              COALESCE(
                NULLIF(ps.price_mid, 0),
                NULLIF(ps.price_market, 0),
                NULLIF(ps.price_last, 0),
                NULLIF(ps.price_low, 0)
              ) AS price,
              ps.price_variant,
              ps.currency,
              ps.as_of
            FROM external_market_price_snapshots ps
            WHERE ps.external_product_id = e.id
              AND ps.currency = 'EUR'
              AND COALESCE(
                NULLIF(ps.price_mid, 0),
                NULLIF(ps.price_market, 0),
                NULLIF(ps.price_last, 0),
                NULLIF(ps.price_low, 0)
              ) IS NOT NULL
            ORDER BY ps.as_of DESC,
                     COALESCE(
                       NULLIF(ps.price_mid, 0),
                       NULLIF(ps.price_market, 0),
                       NULLIF(ps.price_last, 0),
                       NULLIF(ps.price_low, 0)
                     ) ASC,
                     ps.price_variant ASC,
                     ps.id DESC
            LIMIT 1
          ) current_price ON TRUE
          ORDER BY
            (current_price.price IS NULL) ASC,
            current_price.price ASC NULLS LAST,
            CASE WHEN e.external_id = paged.representative_external_product_id THEN 0 ELSE 1 END,
            e.external_id ASC
          LIMIT 1
        ) market ON TRUE
        ORDER BY
          CASE WHEN paged.subject_normalized = :query THEN 0 ELSE 1 END,
          lower(COALESCE(paged.subject, paged.name)) ASC,
          paged.metacard_external_id ASC
        """
    )
    rows = session.execute(sql, params).mappings().all()
    total = int(rows[0]["total"] or 0) if rows else 0

    items: list[dict] = []
    for row in rows:
        product_id = str(row["cardmarket_id_product"] or row["representative_external_product_id"] or "").strip()
        image_url = onepiece_don_proxy_url(row["metacard_external_id"])

        items.append(
            {
                "type": "don_market",
                "identity_scope": "source_owned",
                "game": "onepiece",
                "card_id": None,
                "print_id": None,
                "name": row["name"],
                "subject": row["subject"],
                "subject_normalized": row["subject_normalized"],
                "metacard_external_id": str(row["metacard_external_id"]),
                "representative_external_product_id": str(row["representative_external_product_id"]),
                "product_count": int(row["product_count"] or 0),
                "official_item_id": row["official_item_id"],
                "mapping_source": row["mapping_source"],
                "mapping_confidence": row["mapping_confidence"],
                "collector_number": None,
                "language": None,
                "rarity": None,
                "set_code": None,
                "primary_image_url": image_url,
                "cardmarket_id_product": product_id or None,
                "cardmarket_product_name": row["cardmarket_product_name"],
                "cardmarket_website_path": row["cardmarket_website_path"],
                "cardmarket_price": float(row["cardmarket_price"]) if row["cardmarket_price"] is not None else None,
                "cardmarket_price_variant": row["cardmarket_price_variant"],
                "cardmarket_price_scope": "lowest_latest_nonzero_source_product_guide_in_metacard",
                "cardmarket_currency": row["cardmarket_currency"],
                "cardmarket_as_of": row["cardmarket_as_of"].isoformat() if hasattr(row["cardmarket_as_of"], "isoformat") else row["cardmarket_as_of"],
                "source_as_of": row["source_as_of"].isoformat() if hasattr(row["source_as_of"], "isoformat") else row["source_as_of"],
            }
        )

    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
        "identity_scope": "source_owned",
    }
