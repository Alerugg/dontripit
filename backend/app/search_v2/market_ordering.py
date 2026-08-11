from __future__ import annotations

import re


ALLOWED_SEARCH_SORTS = {
    "relevance",
    "price_desc",
    "price_asc",
    "number_asc",
    "number_desc",
    "name_asc",
    "name_desc",
}


def normalize_search_sort(value: object) -> str:
    sort = str(value or "relevance").strip().lower()
    if sort not in ALLOWED_SEARCH_SORTS:
        raise ValueError(f"Unsupported search sort: {sort}")
    return sort


def _safe_game_slug(value: object) -> str:
    slug = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ValueError("Invalid game slug for Cardmarket ordering")
    return slug


def current_cardmarket_price_join(*, print_id: str, game_slug: str) -> str:
    """Return the accepted exact Cardmarket reference plus its current price.

    The physical mapping is resolved once per candidate Print. Price is optional
    and must belong to the latest Cardmarket PriceGuide capture for the game and
    carry that same idProduct. Search can therefore order globally before
    pagination and render the exact market link without a second API round-trip.
    """
    slug = _safe_game_slug(game_slug)
    game_id_sql = f"(SELECT id FROM games WHERE slug = '{slug}')"
    return f"""
      LEFT JOIN LATERAL (
        SELECT
          ref.external_product_id AS cardmarket_external_product_id,
          ref.id_product AS cardmarket_id_product,
          ref.product_name AS cardmarket_product_name,
          ref.website_path AS cardmarket_website_path,
          COALESCE(
            NULLIF(ps.price_mid, 0),
            NULLIF(ps.price_market, 0),
            NULLIF(ps.price_last, 0),
            NULLIF(ps.price_low, 0)
          ) AS cardmarket_price,
          ps.currency AS cardmarket_currency,
          ps.as_of AS cardmarket_as_of
        FROM LATERAL (
          SELECT
            MIN(e.id) AS external_product_id,
            MIN(e.external_id) AS id_product,
            MIN(e.name) AS product_name,
            MIN(e.website_path) AS website_path
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id = l.external_product_id
          WHERE l.print_id = {print_id}
            AND e.source = 'cardmarket'
            AND e.product_group = 'single'
            AND e.game_id = {game_id_sql}
            AND l.link_status IN ('accepted', 'mapped', 'exact')
          HAVING COUNT(DISTINCT e.id) = 1
        ) ref
        LEFT JOIN LATERAL (
          SELECT ps.*
          FROM price_snapshots ps
          JOIN price_sources src ON src.id = ps.source_id AND src.name = 'cardmarket'
          WHERE ps.entity_type = 'print'
            AND ps.entity_id = {print_id}
            AND ps.currency = 'EUR'
            AND ps.as_of = (
              SELECT MAX(mp.as_of)
              FROM external_market_price_snapshots mp
              JOIN external_catalog_products ep ON ep.id = mp.external_product_id
              WHERE ep.source = 'cardmarket'
                AND ep.product_group = 'single'
                AND ep.game_id = {game_id_sql}
            )
            AND COALESCE(ps.raw_json ->> 'idProduct', '') = ref.id_product
            AND COALESCE(
              NULLIF(ps.price_mid, 0),
              NULLIF(ps.price_market, 0),
              NULLIF(ps.price_last, 0),
              NULLIF(ps.price_low, 0)
            ) IS NOT NULL
          ORDER BY ps.id DESC
          LIMIT 1
        ) ps ON TRUE
        LIMIT 1
      ) cm ON TRUE
    """


def print_order_sql(sort: str, *, default: str) -> str:
    sort = normalize_search_sort(sort)
    number = "COALESCE(NULLIF(substring(COALESCE(p.collector_number,'') from '([0-9]+)[^0-9]*$'), '')::bigint, 9223372036854775807)"
    tie = "lower(c.name) ASC, lower(COALESCE(s.code,'')) ASC, lower(COALESCE(p.collector_number,'')) ASC, p.id ASC"
    if sort == "price_desc":
        return f"cm.cardmarket_price DESC NULLS LAST, {tie}"
    if sort == "price_asc":
        return f"cm.cardmarket_price ASC NULLS LAST, {tie}"
    if sort == "number_asc":
        return f"{number} ASC, lower(COALESCE(p.collector_number,'')) ASC, lower(c.name) ASC, p.id ASC"
    if sort == "number_desc":
        return f"{number} DESC, lower(COALESCE(p.collector_number,'')) DESC, lower(c.name) ASC, p.id ASC"
    if sort == "name_asc":
        return f"lower(c.name) ASC, {number} ASC, lower(COALESCE(p.collector_number,'')) ASC, p.id ASC"
    if sort == "name_desc":
        return f"lower(c.name) DESC, {number} ASC, lower(COALESCE(p.collector_number,'')) ASC, p.id ASC"
    return default
