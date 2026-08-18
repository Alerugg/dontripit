from __future__ import annotations

from sqlalchemy import text

from app.search_v2.market_ordering import current_cardmarket_price_join, normalize_search_sort, print_order_sql
from app.search_v2.normalization import normalize_language, normalize_search_text


SCALAR_FILTERS = {
    "set": ("lower(psp.normalized_set_code)", "print"),
    "collector_number": ("lower(psp.normalized_collector_number)", "print"),
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

SUPPORTED_DISPLAY_LANGUAGES = {"en", "es", "ja"}
ALLOWED_FILTERS = set(SCALAR_FILTERS) | set(RANGE_FILTERS) | {"release", "link_marker", "language"}


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


def _display_language(value: str | None) -> str | None:
    if not value:
        return None
    normalized_values: list[str] = []
    for raw in str(value).split(","):
        clean = raw.strip()
        if not clean:
            continue
        normalized = normalize_language(clean)
        if normalized not in SUPPORTED_DISPLAY_LANGUAGES:
            raise ValueError("Yu-Gi-Oh language must contain only: en, es, ja")
        if normalized not in normalized_values:
            normalized_values.append(normalized)
    return ",".join(normalized_values) or None


def advanced_yugioh_search(
    session,
    *,
    filters: dict,
    query: str | None = None,
    sort: str = "relevance",
    has_price: bool = False,
    limit: int = 50,
    offset: int = 0,
    language: str | None = None,
) -> dict:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh advanced search requires PostgreSQL")

    sort = normalize_search_sort(sort)
    display_language = _display_language(language)
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

    conditions = ["g.slug='yugioh'", "lower(coalesce(p.language,'')) IN ('en','es','ja')"]
    params: dict[str, object] = {
        "limit": bounded_limit,
        "offset": bounded_offset,
        "display_language": display_language,
    }

    if display_language:
        conditions.append(
            "lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ','))"
        )

    q_raw = str(query or "").strip().casefold()
    q_norm = normalize_search_text(query or "")
    if q_raw:
        identity_parts = []
        if q_norm:
            identity_parts.append(
                "csp.normalized_name LIKE :q OR psp.normalized_name LIKE :q OR psp.search_text LIKE :q OR psp.normalized_collector_number LIKE :q_code OR psp.normalized_set_code LIKE :q_code"
            )
            params["q"] = f"%{q_norm}%"
            params["q_code"] = f"%{q_norm.replace(' ', '-')}%"
        identity_parts.append(
            "EXISTS (SELECT 1 FROM print_localizations plq WHERE plq.print_id=p.id AND lower(plq.language)=lower(coalesce(p.language,'')) AND plq.card_name IS NOT NULL AND position(:q_raw in lower(plq.card_name)) > 0)"
        )
        params["q_raw"] = q_raw
        conditions.append(f"({' OR '.join(identity_parts)})")
    else:
        params["q_raw"] = ""

    for key, value in filters.items():
        if value in (None, "", [], {}):
            continue
        if key == "language":
            normalized_languages = []
            raw_languages: list[str] = []
            for item in _as_values(value):
                raw_languages.extend(part.strip() for part in item.split(",") if part.strip())
            for item in raw_languages:
                normalized = normalize_language(item)
                if normalized in SUPPORTED_DISPLAY_LANGUAGES and normalized not in normalized_languages:
                    normalized_languages.append(normalized)
            if not normalized_languages:
                raise ValueError("language must contain one of: en, es, ja")
            conditions.append("lower(coalesce(p.language,''))=ANY(:f_language)")
            params["f_language"] = normalized_languages
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
    order_sql = print_order_sql(
        sort,
        default="lower(c.name) ASC, lower(s.code) ASC, lower(COALESCE(p.collector_number,'')) ASC, lower(COALESCE(p.rarity,'')) ASC, p.id ASC",
    )
    sql = text(
        f"""
        WITH matched AS MATERIALIZED (
          SELECT
            psp.print_id,
            psp.card_id,
            psp.release_names_json,
            psp.attributes_json AS print_attributes,
            csp.attributes_json AS card_attributes,
            c.name AS canonical_name,
            COALESCE(loc.card_name, c.name) AS display_name,
            COALESCE(loc.set_name, s.name) AS display_set_name,
            COALESCE(loc.language, lower(coalesce(p.language,''))) AS display_language,
            (
              SELECT array_agg(DISTINCT lower(p2.language) ORDER BY lower(p2.language))
              FROM prints p2
              WHERE p2.card_id=p.card_id AND lower(coalesce(p2.language,'')) IN ('en','es','ja')
            ) AS available_languages,
            COUNT(*) OVER () AS total_count,
            cm.cardmarket_external_product_id, cm.cardmarket_id_product, cm.cardmarket_product_name, cm.cardmarket_website_path,
            cm.cardmarket_price, cm.cardmarket_currency, cm.cardmarket_as_of,
            ROW_NUMBER() OVER (ORDER BY {order_sql}) AS sort_position
          FROM print_search_profiles psp
          JOIN card_search_profiles csp ON csp.card_id=psp.card_id
          JOIN games g ON g.id=psp.game_id
          JOIN prints p ON p.id=psp.print_id
          JOIN cards c ON c.id=psp.card_id
          JOIN sets s ON s.id=p.set_id
          LEFT JOIN LATERAL (
            SELECT lower(pl.language) AS language, pl.card_name, pl.set_name
            FROM print_localizations pl
            WHERE pl.print_id=p.id
              AND lower(pl.language)=lower(coalesce(p.language,''))
            ORDER BY pl.id ASC
            LIMIT 1
          ) loc ON TRUE
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
          matched.canonical_name,
          matched.display_name,
          matched.display_set_name AS set_name,
          matched.display_language,
          matched.available_languages,
          s.code AS set_code,
          p.collector_number,
          lower(coalesce(p.language,'')) AS language,
          p.rarity,
          psp.exact_variant,
          psp.variant_family,
          matched.release_names_json,
          matched.card_attributes,
          matched.print_attributes,
          matched.cardmarket_external_product_id, matched.cardmarket_id_product, matched.cardmarket_product_name, matched.cardmarket_website_path,
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
                "name": row["display_name"],
                "canonical_name": row["canonical_name"],
                "game": "yugioh",
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
                "display_language": row["display_language"],
                "available_languages": list(row["available_languages"] or []),
                "rarity": row["rarity"],
                "exact_variant": row["exact_variant"],
                "variant_family": row["variant_family"],
                "primary_image_url": row["primary_image_url"],
                "attributes": attrs,
                "cardmarket_external_product_id": row["cardmarket_external_product_id"],
                "cardmarket_id_product": row["cardmarket_id_product"],
                "cardmarket_product_name": row["cardmarket_product_name"],
                "cardmarket_website_path": row["cardmarket_website_path"],
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
        "language": display_language or "all",
    }
