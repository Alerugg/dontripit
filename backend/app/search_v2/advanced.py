from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import (
    normalize_language,
    normalize_onepiece_collector_number,
    normalize_onepiece_set_code,
    normalize_search_text,
)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    value = str(value).strip()
    return [value] if value else []


def _range(value) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        lo, hi = value.get("min"), value.get("max")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
    else:
        lo = hi = value
    return (
        None if lo in (None, "") else int(lo),
        None if hi in (None, "") else int(hi),
    )


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def advanced_onepiece_search(
    session,
    *,
    filters: dict | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Advanced One Piece Print search using explicit, auditable filters.

    The query is intentionally compiled from a fixed allowlist. Unsupported
    filters fail loudly so the UI can never pretend a filter worked when it was
    silently ignored.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Advanced Search V2 requires PostgreSQL")

    filters = dict(filters or {})
    params: dict[str, object] = {
        "game": "onepiece",
        "limit": max(1, min(int(limit or 50), 200)),
        "offset": max(0, int(offset or 0)),
    }
    where = ["g.slug = :game"]

    q_tokens = [token for token in normalize_search_text(query or "").split() if len(token) >= 2][:8]
    for idx, token in enumerate(q_tokens):
        key = f"qtoken_{idx}"
        where.append(f"psp.search_text LIKE :{key}")
        params[key] = f"%{token}%"

    def add_in(column: str, key: str, values: list[str]) -> None:
        if not values:
            return
        bind_names = []
        for idx, value in enumerate(values):
            bind = f"{key}_{idx}"
            bind_names.append(f":{bind}")
            params[bind] = value
        where.append(f"{column} IN ({', '.join(bind_names)})")

    sets = _as_list(filters.pop("set", None))
    add_in(
        "psp.normalized_set_code",
        "set",
        [normalize_onepiece_set_code(value) or normalize_search_text(value).replace(" ", "-") for value in sets],
    )

    collectors = _as_list(filters.pop("collector_number", None))
    add_in(
        "psp.normalized_collector_number",
        "collector",
        [normalize_onepiece_collector_number(value) or normalize_search_text(value).replace(" ", "-") for value in collectors],
    )

    languages = _as_list(filters.pop("language", None))
    add_in("psp.language", "language", [normalize_language(value) for value in languages if normalize_language(value)])

    rarities = [value.lower() for value in _as_list(filters.pop("rarity", None))]
    add_in("lower(COALESCE(psp.rarity, ''))", "rarity", rarities)

    exact_variants = [value.lower() for value in _as_list(filters.pop("exact_variant", None))]
    add_in("psp.exact_variant", "exact_variant", exact_variants)

    families = [value.lower() for value in _as_list(filters.pop("variant_family", None))]
    add_in("psp.variant_family", "variant_family", families)

    releases = _as_list(filters.pop("release", None))
    if releases:
        clauses = []
        for idx, value in enumerate(releases):
            key = f"release_{idx}"
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.release_names_json, '[]'::jsonb)) rel "
                f"WHERE lower(rel) LIKE :{key})"
            )
            params[key] = f"%{normalize_search_text(value)}%"
        where.append("(" + " OR ".join(clauses) + ")")

    for input_key, json_key in (("color", "color"), ("attribute", "attribute"), ("traits", "traits")):
        values = _as_list(filters.pop(input_key, None))
        if values:
            clauses = []
            for idx, value in enumerate(values):
                key = f"{input_key}_{idx}"
                clauses.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.attributes_json -> "
                    f"'{json_key}', '[]'::jsonb)) v WHERE lower(v) = :{key})"
                )
                params[key] = normalize_search_text(value)
            where.append("(" + " OR ".join(clauses) + ")")

    for input_key, json_key in (("card_type", "card_type"), ("block", "block")):
        values = [normalize_search_text(value) for value in _as_list(filters.pop(input_key, None))]
        if values:
            bind_names = []
            for idx, value in enumerate(values):
                key = f"{input_key}_{idx}"
                bind_names.append(f":{key}")
                params[key] = value
            where.append(f"lower(COALESCE(psp.attributes_json ->> '{json_key}', '')) IN ({', '.join(bind_names)})")

    for key in ("cost", "life", "power", "counter"):
        value = filters.pop(key, None)
        if value is None:
            continue
        lo, hi = _range(value)
        if lo is not None:
            params[f"{key}_min"] = lo
            where.append(
                f"NULLIF(psp.attributes_json ->> '{key}', '')::integer >= :{key}_min"
            )
        if hi is not None:
            params[f"{key}_max"] = hi
            where.append(
                f"NULLIF(psp.attributes_json ->> '{key}', '')::integer <= :{key}_max"
            )

    for input_key, json_key in (
        ("promo", "is_promo"),
        ("sp", "is_sp"),
        ("treasure_rare", "is_treasure_rare"),
    ):
        value = filters.pop(input_key, None)
        if value is not None:
            params[input_key] = _bool(value)
            where.append(
                f"COALESCE((psp.attributes_json ->> '{json_key}')::boolean, false) = :{input_key}"
            )

    if filters:
        raise ValueError(f"Unsupported advanced filters: {sorted(filters)}")

    where_sql = " AND ".join(where)
    base_from = """
      FROM print_search_profiles psp
      JOIN prints p ON p.id = psp.print_id
      JOIN cards c ON c.id = psp.card_id
      JOIN sets s ON s.id = p.set_id
      JOIN games g ON g.id = psp.game_id
    """
    total = int(
        session.execute(text(f"SELECT COUNT(*) {base_from} WHERE {where_sql}"), params).scalar_one() or 0
    )

    sql = text(
        f"""
        SELECT
          psp.print_id,
          psp.card_id,
          c.card_key,
          c.name,
          g.slug AS game,
          s.code AS set_code,
          s.name AS set_name,
          p.collector_number,
          psp.language,
          psp.rarity,
          psp.exact_variant,
          psp.variant_family,
          psp.release_names_json,
          psp.attributes_json,
          (
            SELECT pi.url FROM print_images pi
            WHERE pi.print_id = psp.print_id
            ORDER BY pi.is_primary DESC, pi.id ASC
            LIMIT 1
          ) AS primary_image_url
        {base_from}
        WHERE {where_sql}
        ORDER BY c.name ASC, s.code ASC, p.collector_number ASC,
                 CASE WHEN psp.exact_variant = 'default' THEN 0 ELSE 1 END,
                 psp.exact_variant ASC, psp.print_id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    rows = session.execute(sql, params).mappings().all()
    items = [
        {
            "type": "print",
            "print_id": row["print_id"],
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "name": row["name"],
            "game": row["game"],
            "set_code": row["set_code"],
            "set_name": row["set_name"],
            "collector_number": row["collector_number"],
            "language": row["language"],
            "rarity": row["rarity"],
            "exact_variant": row["exact_variant"],
            "variant_family": row["variant_family"],
            "releases": row["release_names_json"] or [],
            "attributes": row["attributes_json"] or {},
            "primary_image_url": row["primary_image_url"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": params["limit"], "offset": params["offset"]}
