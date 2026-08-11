from __future__ import annotations

from sqlalchemy import text

from app.search_v2.market_ordering import current_cardmarket_price_join, normalize_search_sort, print_order_sql
from app.search_v2.normalization import normalize_search_text


SCALAR_FILTERS = {
    "set": ("lower(psp.normalized_set_code)", "print"),
    "collector_number": ("lower(psp.normalized_collector_number)", "print"),
    "language": ("lower(psp.language)", "print"),
    "rarity": ("lower(psp.rarity)", "print"),
    "card_class": ("lower(csp.attributes_json->>'card_class')", "card"),
    "card_type": ("lower(csp.attributes_json->>'card_type')", "card"),
    "frame_type": ("lower(csp.attributes_json->>'frame_type')", "card"),
    "attribute": ("lower(csp.attributes_json->>'attribute')", "card"),
    "race": ("lower(csp.attributes_json->>'race')", "card"),
    "archetype": ("lower(csp.attributes_json->>'archetype')", "card"),
}

RANGE_FILTERS = {
    "release_year": "(psp.attributes_json->>'release_year')::int",
    "level": "(csp.attributes_json->>'level')::int",
    "rank": "(csp.attributes_json->>'rank')::int",
    "atk": "(csp.attributes_json->>'atk')::int",
    "def": "(csp.attributes_json->>'def')::int",
    "pendulum_scale": "(csp.attributes_json->>'pendulum_scale')::int",
    "link_value": "(csp.attributes_json->>'link_value')::int",
}

ALLOWED_FILTERS = set(SCALAR_FILTERS) | set(RANGE_FILTERS) | {"release", "link_marker"}


def _as_values(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    result = []
    seen = set()
    for item in raw:
        clean = str(item or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)
    return result


def _range(value, key: str) -> tuple[int | None, int | None]:
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object with min/max")
    low = value.get("min")
    high = value.get("max")
    try:
        low = int(low) if low not in (None, "") else None
        high = int(high) if high not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} min/max must be integers") from exc
    if low is not None and high is not None and low > high:
        raise ValueError(f"{key} min cannot exceed max")
    return low, high


def advanced_yugioh_search(
    session,
    *,
    filters: dict,
    query: str | None = None,
    sort: str = "relevance",
    has_price: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh advanced search requires PostgreSQL")

    sort = normalize_search_sort(sort)
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    unknown = sorted(set(filters) - ALLOWED_FILTERS)
    if unknown:
        raise ValueError(f"Unsupported Yu-Gi-Oh filters: {', '.join(unknown)}")

    try:
        bounded_limit = max(1, min(int(limit or 50), 100))
        bounded_offset = max(0, int(offset or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit/offset must be integers") from exc

    conditions = ["g.slug='yugioh'"]
    params: dict[str, object] = {"limit": bounded_limit, "offset": bounded_offset}

    q_norm = normalize_search_text(query or "")
    if q_norm:
        # Advanced `q` is intentionally identity-only. Gameplay dimensions such as
        # archetype/attribute/race and release metadata have dedicated facets. Using
        # the broad card/print search_text here caused `Dark Magician` to match an
        # unrelated card merely because its archetype or release contained that text.
        conditions.append(
            "(csp.normalized_name LIKE :q OR psp.normalized_collector_number LIKE :q_code OR psp.normalized_set_code LIKE :q_code)"
        )
        params["q"] = f"%{q_norm}%"
        params["q_code"] = f"%{q_norm.replace(' ', '-')}%"

    for key, value in filters.items():
        if value in (None, "", [], {}):
            continue
        if key in SCALAR_FILTERS:
            values = _as_values(value)
            if not values:
                continue
            if key in {"set", "collector_number"}:
                normalized = [normalize_search_text(item).replace(" ", "-").lower() for item in values]
            else:
                normalized = [item.lower() for item in values]
            param = f"f_{key}"
            conditions.append(f"{SCALAR_FILTERS[key][0]} = ANY(:{param})")
            params[param] = normalized
            continue

        if key in RANGE_FILTERS:
            low, high = _range(value, key)
            expr = RANGE_FILTERS[key]
            exists_guard = expr.split("::int", 1)[0] + " IS NOT NULL"
            conditions.append(exists_guard)
            if low is not None:
                conditions.append(f"{expr} >= :{key}_min")
                params[f"{key}_min"] = low
            if high is not None:
                conditions.append(f"{expr} <= :{key}_max")
                params[f"{key}_max"] = high
            continue

        if key == "release":
            values = [item.lower() for item in _as_values(value)]
            if values:
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.release_names_json,'[]'::jsonb)) release_name WHERE lower(release_name)=ANY(:f_release))"
                )
                params["f_release"] = values
            continue

        if key == "link_marker":
            values = [item.lower() for item in _as_values(value)]
            if values:
                conditions.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(csp.attributes_json->'link_markers','[]'::jsonb)) marker WHERE lower(marker)=ANY(:f_link_marker))"
                )
                params["f_link_marker"] = values
            continue

    market_join = current_cardmarket_price_join(print_id="p.id", game_slug="yugioh")
    if has_price:
        conditions.append("cm.cardmarket_price IS NOT NULL")
    where_sql = " AND ".join(conditions)
    order_sql = print_order_sql(sort, default="lower(c.name) ASC, lower(s.code) ASC, lower(COALESCE(p.collector_number,'')) ASC, lower(COALESCE(p.rarity,'')) ASC, p.id ASC")
    sql = text(
        f"""
        WITH matched AS MATERIALIZED (
          SELECT
            psp.print_id,
            psp.card_id,
            psp.release_names_json,
            psp.attributes_json AS print_attributes,
            csp.attributes_json AS card_attributes,
            COUNT(*) OVER () AS total_count,
            cm.cardmarket_price, cm.cardmarket_currency, cm.cardmarket_as_of,
            ROW_NUMBER() OVER (ORDER BY {order_sql}) AS sort_position
          FROM print_search_profiles psp
          JOIN card_search_profiles csp ON csp.card_id=psp.card_id
          JOIN games g ON g.id=psp.game_id
          JOIN prints p ON p.id=psp.print_id
          JOIN cards c ON c.id=psp.card_id
          JOIN sets s ON s.id=p.set_id
          {market_join}
          WHERE {where_sql}
          ORDER BY {order_sql}
          LIMIT :limit OFFSET :offset
        )
        SELECT
          matched.total_count,
          p.id AS print_id,
          c.id AS card_id,
          c.card_key,
          c.name,
          s.code AS set_code,
          s.name AS set_name,
          p.collector_number,
          p.language,
          p.rarity,
          psp.exact_variant,
          psp.variant_family,
          matched.release_names_json,
          matched.card_attributes,
          matched.print_attributes,
          matched.cardmarket_price, matched.cardmarket_currency, matched.cardmarket_as_of,
          (
            SELECT pi.url FROM print_images pi
            WHERE pi.print_id=p.id
            ORDER BY pi.is_primary DESC, pi.id ASC
            LIMIT 1
          ) AS primary_image_url
        FROM matched
        JOIN prints p ON p.id=matched.print_id
        JOIN cards c ON c.id=matched.card_id
        JOIN sets s ON s.id=p.set_id
        JOIN print_search_profiles psp ON psp.print_id=p.id
        ORDER BY matched.sort_position ASC
        """
    )
    rows = session.execute(sql, params).mappings().all()
    total = int(rows[0]["total_count"] or 0) if rows else 0
    items = []
    for row in rows:
        attrs = dict(row["card_attributes"] or {})
        attrs.update(row["print_attributes"] or {})
        attrs["release_names"] = row["release_names_json"] or []
        items.append(
            {
                "type": "print",
                "print_id": row["print_id"],
                "card_id": row["card_id"],
                "card_key": row["card_key"],
                "name": row["name"],
                "game": "yugioh",
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
                "rarity": row["rarity"],
                "exact_variant": row["exact_variant"],
                "variant_family": row["variant_family"],
                "primary_image_url": row["primary_image_url"],
                "attributes": attrs,
                "cardmarket_price": float(row["cardmarket_price"]) if row["cardmarket_price"] is not None else None,
                "cardmarket_currency": row["cardmarket_currency"],
                "cardmarket_as_of": row["cardmarket_as_of"].isoformat() if hasattr(row["cardmarket_as_of"], "isoformat") else row["cardmarket_as_of"],
            }
        )

    return {
        "items": items,
        "count": len(items),
        "total": total,
        "limit": bounded_limit,
        "offset": bounded_offset,
        "filters": filters,
        "query": str(query or "").strip() or None,
        "sort": sort,
        "has_price": bool(has_price),
    }
