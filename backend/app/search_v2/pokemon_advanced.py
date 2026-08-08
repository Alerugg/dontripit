from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_language, normalize_search_text


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    clean = str(value).strip()
    return [clean] if clean else []


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


def _lower(value: object) -> str:
    return str(value or "").strip().lower()


def advanced_pokemon_search(
    session,
    *,
    filters: dict | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Advanced physical Pokémon Print search over certified Search V2 profiles.

    Filters operate only on the compact PrintSearchProfile projection. Matching
    IDs are counted and paginated before wide JSON/image data is loaded, avoiding
    expensive sorts of thousands of rich rows for common finishes such as Holo.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Advanced Pokémon Search V2 requires PostgreSQL")

    game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
    filters = dict(filters or {})
    params: dict[str, object] = {
        "game_id": game_id,
        "limit": max(1, min(int(limit or 50), 200)),
        "offset": max(0, int(offset or 0)),
    }
    where = ["psp.game_id = :game_id"]

    q_tokens = [token for token in normalize_search_text(query or "").split() if len(token) >= 2][:8]
    for idx, token in enumerate(q_tokens):
        key = f"qtoken_{idx}"
        where.append(f"psp.search_text LIKE :{key}")
        params[key] = f"%{token}%"

    def add_in(column: str, key: str, values: list[str]) -> None:
        clean_values = [value for value in values if value]
        if not clean_values:
            return
        binds = []
        for idx, value in enumerate(clean_values):
            bind = f"{key}_{idx}"
            binds.append(f":{bind}")
            params[bind] = value
        where.append(f"{column} IN ({', '.join(binds)})")

    sets = _as_list(filters.pop("set", None))
    add_in(
        "psp.normalized_set_code",
        "set",
        [normalize_search_text(value).replace(" ", "-") for value in sets],
    )

    collectors = _as_list(filters.pop("collector_number", None))
    add_in(
        "psp.normalized_collector_number",
        "collector",
        [normalize_search_text(value).replace(" ", "-") for value in collectors],
    )

    languages = [normalize_language(value) for value in _as_list(filters.pop("language", None))]
    add_in("psp.language", "language", [value for value in languages if value])
    add_in(
        "lower(COALESCE(psp.rarity,''))",
        "rarity",
        [_lower(value) for value in _as_list(filters.pop("rarity", None))],
    )
    add_in(
        "psp.exact_variant",
        "exact_variant",
        [normalize_search_text(value).replace(" ", "-") for value in _as_list(filters.pop("exact_variant", None))],
    )
    add_in(
        "psp.variant_family",
        "variant_family",
        [_lower(value) for value in _as_list(filters.pop("variant_family", None))],
    )

    for input_key, json_key in (("types", "types"), ("stamp", "stamps")):
        values = _as_list(filters.pop(input_key, None))
        if not values:
            continue
        clauses = []
        for idx, value in enumerate(values):
            bind = f"{input_key}_{idx}"
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.attributes_json -> "
                f"'{json_key}', '[]'::jsonb)) v WHERE lower(v) = :{bind})"
            )
            params[bind] = _lower(value)
        where.append("(" + " OR ".join(clauses) + ")")

    scalar_filters = {
        "category": "category",
        "stage": "stage",
        "trainer_type": "trainer_type",
        "energy_type": "energy_type",
        "evolve_from": "evolve_from",
        "regulation_mark": "regulation_mark",
        "illustrator": "illustrator",
        "series": "series",
        "finish": "finish",
        "foil_pattern": "foil_pattern",
        "variant_subtype": "variant_subtype",
        "release_context": "release_context",
        "size": "size",
    }
    for input_key, json_key in scalar_filters.items():
        values = [_lower(value) for value in _as_list(filters.pop(input_key, None))]
        if not values:
            continue
        binds = []
        for idx, value in enumerate(values):
            bind = f"{input_key}_{idx}"
            binds.append(f":{bind}")
            params[bind] = value
        # Explicit non-empty predicate matches partial expression indexes.
        where.append(f"COALESCE(psp.attributes_json ->> '{json_key}', '') <> ''")
        where.append(
            f"lower(COALESCE(psp.attributes_json ->> '{json_key}', '')) IN ({', '.join(binds)})"
        )

    for key in ("hp", "release_year"):
        value = filters.pop(key, None)
        if value is None:
            continue
        lo, hi = _range(value)
        json_expr = f"psp.attributes_json ->> '{key}'"
        numeric_expr = f"NULLIF({json_expr}, '')::integer"
        where.append(f"COALESCE({json_expr}, '') ~ '^[0-9]+$'")
        if lo is not None:
            params[f"{key}_min"] = lo
            where.append(f"{numeric_expr} >= :{key}_min")
        if hi is not None:
            params[f"{key}_max"] = hi
            where.append(f"{numeric_expr} <= :{key}_max")

    dex_value = filters.pop("dex_id", None)
    if dex_value is not None:
        lo, hi = _range(dex_value)
        clauses = ["value ~ '^[0-9]+$'"]
        if lo is not None:
            params["dex_id_min"] = lo
            clauses.append("value::integer >= :dex_id_min")
        if hi is not None:
            params["dex_id_max"] = hi
            clauses.append("value::integer <= :dex_id_max")
        where.append(
            "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.attributes_json -> 'dex_id', '[]'::jsonb)) value "
            "WHERE " + " AND ".join(clauses) + ")"
        )

    if filters:
        raise ValueError(f"Unsupported Pokémon advanced filters: {sorted(filters)}")

    where_sql = " AND ".join(where)

    total = int(
        session.execute(
            text(f"SELECT COUNT(*) FROM print_search_profiles psp WHERE {where_sql}"),
            params,
        ).scalar_one() or 0
    )

    rows = session.execute(
        text(
            f"""
            WITH matched AS MATERIALIZED (
              SELECT
                psp.print_id,
                psp.card_id,
                c.name AS sort_name,
                s.release_date AS sort_release_date,
                s.code AS sort_set_code,
                p.collector_number AS sort_collector_number,
                COALESCE(psp.variant_family, '') AS sort_variant_family,
                COALESCE(psp.exact_variant, '') AS sort_exact_variant
              FROM print_search_profiles psp
              JOIN prints p ON p.id=psp.print_id
              JOIN cards c ON c.id=psp.card_id
              JOIN sets s ON s.id=p.set_id
              WHERE {where_sql}
              ORDER BY c.name ASC, s.release_date ASC NULLS LAST, s.code ASC,
                       p.collector_number ASC, psp.variant_family ASC,
                       psp.exact_variant ASC, psp.print_id ASC
              LIMIT :limit OFFSET :offset
            )
            SELECT
              psp.print_id,
              psp.card_id,
              c.card_key,
              c.tcgdex_id,
              c.name,
              'pokemon' AS game,
              s.code AS set_code,
              s.name AS set_name,
              p.collector_number,
              psp.language,
              psp.rarity,
              psp.exact_variant,
              psp.variant_family,
              psp.attributes_json,
              (
                SELECT pi.url FROM print_images pi
                WHERE pi.print_id=psp.print_id
                ORDER BY pi.is_primary DESC, pi.id ASC
                LIMIT 1
              ) AS primary_image_url
            FROM matched m
            JOIN print_search_profiles psp ON psp.print_id=m.print_id
            JOIN prints p ON p.id=m.print_id
            JOIN cards c ON c.id=m.card_id
            JOIN sets s ON s.id=p.set_id
            ORDER BY m.sort_name ASC, m.sort_release_date ASC NULLS LAST,
                     m.sort_set_code ASC, m.sort_collector_number ASC,
                     m.sort_variant_family ASC, m.sort_exact_variant ASC,
                     m.print_id ASC
            """
        ),
        params,
    ).mappings().all()

    items = [
        {
            "type": "print",
            "print_id": row["print_id"],
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "tcgdex_id": row["tcgdex_id"],
            "name": row["name"],
            "game": row["game"],
            "set_code": row["set_code"],
            "set_name": row["set_name"],
            "collector_number": row["collector_number"],
            "language": row["language"],
            "rarity": row["rarity"],
            "exact_variant": row["exact_variant"],
            "variant_family": row["variant_family"],
            "attributes": row["attributes_json"] or {},
            "primary_image_url": row["primary_image_url"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": params["limit"], "offset": params["offset"]}
