from __future__ import annotations

from sqlalchemy import text

from app.search_v2.market_ordering import current_cardmarket_price_join, normalize_search_sort, print_order_sql
from app.search_v2.normalization import normalize_search_text


SCALAR_FILTERS = {
    "set": "lower(psp.normalized_set_code)",
    "collector_number": "lower(psp.normalized_collector_number)",
    "language": "lower(psp.language)",
    "rarity": "lower(psp.rarity)",
    "finish": "lower(psp.exact_variant)",
    "layout": "lower(csp.attributes_json->>'layout')",
    "artist": "lower(psp.attributes_json->>'artist')",
    "set_type": "lower(psp.attributes_json->>'set_type')",
    "frame": "lower(psp.attributes_json->>'frame')",
    "border_color": "lower(psp.attributes_json->>'border_color')",
}
ARRAY_FILTERS = {
    "color_identity": ("card", "color_identity"),
    "card_type": ("card", "card_types"),
    "keyword": ("card", "keywords"),
    "frame_effect": ("print", "frame_effects"),
    "promo_type": ("print", "promo_types"),
}
BOOLEAN_FILTERS = {"promo", "full_art", "textless", "reserved"}
RANGE_FILTERS = {
    "release_year": ("print", "release_year", "integer"),
    "mana_value": ("card", "mana_value", "numeric"),
}
ALLOWED_FILTERS = set(SCALAR_FILTERS) | set(ARRAY_FILTERS) | BOOLEAN_FILTERS | set(RANGE_FILTERS)


def _values(value) -> list[str]:
    raw = list(value) if isinstance(value, (list, tuple, set)) else [value]
    out, seen = [], set()
    for item in raw:
        clean = str(item or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            out.append(clean)
    return out


def _boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _range(value, key: str, cast: str):
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object with min/max")
    low, high = value.get("min"), value.get("max")
    parser = int if cast == "integer" else float
    try:
        low = parser(low) if low not in (None, "") else None
        high = parser(high) if high not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} min/max must be numeric") from exc
    if low is not None and high is not None and low > high:
        raise ValueError(f"{key} min cannot exceed max")
    return low, high


def advanced_mtg_search(session, *, filters: dict, query: str | None = None, sort: str = "relevance", has_price: bool = False, limit: int = 50, offset: int = 0) -> dict:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("MTG advanced search requires PostgreSQL")
    sort = normalize_search_sort(sort)
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    unknown = sorted(set(filters) - ALLOWED_FILTERS)
    if unknown:
        raise ValueError(f"Unsupported MTG filters: {', '.join(unknown)}")

    bounded_limit = max(1, min(int(limit or 50), 100))
    bounded_offset = max(0, int(offset or 0))
    conditions = ["g.slug='mtg'"]
    params: dict[str, object] = {"limit": bounded_limit, "offset": bounded_offset}

    q_norm = normalize_search_text(query or "")
    if q_norm:
        params["q"] = f"%{q_norm}%"
        params["q_code"] = f"%{q_norm.replace(' ', '-')}%"
        conditions.append(
            "(csp.normalized_name LIKE :q OR psp.normalized_collector_number LIKE :q_code OR psp.normalized_set_code LIKE :q_code)"
        )

    for key, value in filters.items():
        if value in (None, "", [], {}):
            continue
        if key in SCALAR_FILTERS:
            values = [normalize_search_text(v).replace(" ", "-") if key in {"set", "collector_number"} else str(v).strip().lower() for v in _values(value)]
            if values:
                params[f"f_{key}"] = values
                conditions.append(f"{SCALAR_FILTERS[key]} = ANY(:f_{key})")
            continue

        if key in ARRAY_FILTERS:
            scope, json_key = ARRAY_FILTERS[key]
            column = "csp.attributes_json" if scope == "card" else "psp.attributes_json"
            values = [str(v).strip().lower() for v in _values(value)]
            if values:
                params[f"f_{key}"] = values
                conditions.append(
                    f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE({column}->'{json_key}','[]'::jsonb)) v WHERE lower(v)=ANY(:f_{key}))"
                )
            continue

        if key in BOOLEAN_FILTERS:
            params[f"f_{key}"] = _boolean(value)
            conditions.append(f"COALESCE((psp.attributes_json->>'{key}')::boolean,false)=:f_{key}")
            continue

        if key in RANGE_FILTERS:
            scope, json_key, cast = RANGE_FILTERS[key]
            column = "csp.attributes_json" if scope == "card" else "psp.attributes_json"
            low, high = _range(value, key, cast)
            sql_cast = "integer" if cast == "integer" else "numeric"
            expr = f"NULLIF({column}->>'{json_key}','')::{sql_cast}"
            if low is not None:
                params[f"{key}_min"] = low
                conditions.append(f"{expr}>=:{key}_min")
            if high is not None:
                params[f"{key}_max"] = high
                conditions.append(f"{expr}<=:{key}_max")

    market_join = current_cardmarket_price_join(print_id="p.id", game_slug="mtg")
    if has_price:
        conditions.append("cm.cardmarket_price IS NOT NULL")
    where_sql = " AND ".join(conditions)
    order_sql = print_order_sql(sort, default="lower(c.name) ASC, lower(s.code) ASC, lower(COALESCE(p.collector_number,'')) ASC, lower(COALESCE(psp.exact_variant,'')) ASC, p.id ASC")
    sql = text(f"""
        WITH matched AS MATERIALIZED (
          SELECT psp.print_id,psp.card_id,psp.attributes_json AS print_attributes,
                 csp.attributes_json AS card_attributes,COUNT(*) OVER() AS total_count,
                 cm.cardmarket_external_product_id, cm.cardmarket_id_product, cm.cardmarket_product_name, cm.cardmarket_website_path,
                 cm.cardmarket_price,cm.cardmarket_currency,cm.cardmarket_as_of,
                 ROW_NUMBER() OVER (ORDER BY {order_sql}) AS sort_position
          FROM print_search_profiles psp
          JOIN card_search_profiles csp ON csp.card_id=psp.card_id
          JOIN games g ON g.id=psp.game_id
          JOIN prints p ON p.id=psp.print_id
          JOIN cards c ON c.id=psp.card_id
          JOIN sets s ON s.id=p.set_id
          {market_join}
          WHERE {where_sql}
          ORDER BY {order_sql} LIMIT :limit OFFSET :offset
        )
        SELECT matched.total_count,p.id AS print_id,c.id AS card_id,c.card_key,c.name,
               s.code AS set_code,s.name AS set_name,p.collector_number,p.language,p.rarity,
               psp.exact_variant,psp.variant_family,matched.card_attributes,matched.print_attributes,
               matched.cardmarket_external_product_id,matched.cardmarket_id_product,matched.cardmarket_product_name,matched.cardmarket_website_path,
               matched.cardmarket_price,matched.cardmarket_currency,matched.cardmarket_as_of,
               (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC,pi.id LIMIT 1) AS primary_image_url
        FROM matched
        JOIN prints p ON p.id=matched.print_id JOIN cards c ON c.id=matched.card_id
        JOIN sets s ON s.id=p.set_id JOIN print_search_profiles psp ON psp.print_id=p.id
        ORDER BY matched.sort_position
    """)
    rows = session.execute(sql, params).mappings().all()
    total = int(rows[0]["total_count"] or 0) if rows else 0
    items = []
    for row in rows:
        attrs = dict(row["card_attributes"] or {})
        attrs.update(row["print_attributes"] or {})
        items.append({
            "type":"print","print_id":row["print_id"],"card_id":row["card_id"],"card_key":row["card_key"],
            "name":row["name"],"game":"mtg","set_code":row["set_code"],"set_name":row["set_name"],
            "collector_number":row["collector_number"],"language":row["language"],"rarity":row["rarity"],
            "exact_variant":row["exact_variant"],"variant_family":row["variant_family"],
            "primary_image_url":row["primary_image_url"],"attributes":attrs,
            "cardmarket_external_product_id":row["cardmarket_external_product_id"],
            "cardmarket_id_product":row["cardmarket_id_product"],
            "cardmarket_product_name":row["cardmarket_product_name"],
            "cardmarket_website_path":row["cardmarket_website_path"],
            "cardmarket_price":float(row["cardmarket_price"]) if row["cardmarket_price"] is not None else None,
            "cardmarket_currency":row["cardmarket_currency"],
            "cardmarket_as_of":row["cardmarket_as_of"].isoformat() if hasattr(row["cardmarket_as_of"],"isoformat") else row["cardmarket_as_of"],
        })
    return {"items":items,"count":len(items),"total":total,"limit":bounded_limit,"offset":bounded_offset,"filters":filters,"query":str(query or "").strip() or None,"sort":sort,"has_price":bool(has_price)}
