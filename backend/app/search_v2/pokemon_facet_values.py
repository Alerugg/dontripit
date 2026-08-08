from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import compact_search_text, normalize_search_text


MAX_FACET_VALUES = 100


def _limit(value: int | None) -> int:
    try:
        parsed = int(value or 30)
    except (TypeError, ValueError):
        parsed = 30
    return max(1, min(parsed, MAX_FACET_VALUES))


def pokemon_facet_values(
    session,
    *,
    key: str,
    query: str | None = None,
    limit: int = 30,
) -> list[dict]:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Pokémon facet values require PostgreSQL")

    key = str(key or "").strip().lower()
    q = normalize_search_text(query or "")
    q_compact = compact_search_text(query or "")
    params: dict[str, object] = {
        "game": "pokemon",
        "limit": _limit(limit),
        "q": f"%{q}%",
        "q_compact": f"%{q_compact}%",
    }

    scalar_columns = {
        "set": ("s.code", "s.name"),
        "language": ("psp.language", "psp.language"),
        "rarity": ("psp.rarity", "psp.rarity"),
        "exact_variant": ("psp.exact_variant", "psp.exact_variant"),
        "variant_family": ("psp.variant_family", "psp.variant_family"),
    }
    if key in scalar_columns:
        value_expr, label_expr = scalar_columns[key]
        rows = session.execute(
            text(
                f"""
                SELECT {value_expr} AS value, {label_expr} AS label, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                JOIN sets s ON s.id=p.set_id
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND {value_expr} IS NOT NULL
                  AND (
                    :q='%%'
                    OR lower(CAST({label_expr} AS text)) LIKE :q
                    OR lower(CAST({value_expr} AS text)) LIKE :q
                    OR regexp_replace(lower(CAST({value_expr} AS text)), '[^a-z0-9]', '', 'g') LIKE :q_compact
                  )
                GROUP BY {value_expr}, {label_expr}
                ORDER BY count DESC, label ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [
            {"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)}
            for row in rows
            if row["value"] not in (None, "")
        ]

    if key == "collector_number":
        rows = session.execute(
            text(
                """
                SELECT p.collector_number AS value, c.name AS label, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                JOIN cards c ON c.id=psp.card_id
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND (
                    :q='%%'
                    OR lower(p.collector_number) LIKE :q
                    OR lower(c.name) LIKE :q
                    OR regexp_replace(lower(p.collector_number), '[^a-z0-9]', '', 'g') LIKE :q_compact
                  )
                GROUP BY p.collector_number, c.name
                ORDER BY count DESC, p.collector_number ASC, c.name ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [
            {
                "value": row["value"],
                "label": f"{row['value']} · {row['label']}",
                "count": int(row["count"] or 0),
            }
            for row in rows
        ]

    json_arrays = {
        "type": "types",
        "stamps": "stamps",
        "dex_id": "dex_id",
    }
    if key in json_arrays:
        json_key = json_arrays[key]
        rows = session.execute(
            text(
                f"""
                SELECT value, value AS label, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN games g ON g.id=psp.game_id
                CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(psp.attributes_json -> '{json_key}', '[]'::jsonb)) value
                WHERE g.slug=:game
                  AND (:q='%%' OR lower(value) LIKE :q)
                GROUP BY value
                ORDER BY count DESC, value ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [
            {"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)}
            for row in rows
        ]

    json_scalars = {
        "category": "category",
        "stage": "stage",
        "trainer_type": "trainer_type",
        "energy_type": "energy_type",
        "regulation_mark": "regulation_mark",
        "illustrator": "illustrator",
        "series": "series",
        "finish": "finish",
        "foil_pattern": "foil_pattern",
        "variant_subtype": "variant_subtype",
        "release_context": "release_context",
        "size": "size",
        "release_year": "release_year",
    }
    if key in json_scalars:
        json_key = json_scalars[key]
        rows = session.execute(
            text(
                f"""
                SELECT psp.attributes_json ->> '{json_key}' AS value,
                       psp.attributes_json ->> '{json_key}' AS label,
                       COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND COALESCE(psp.attributes_json ->> '{json_key}', '') <> ''
                  AND (:q='%%' OR lower(psp.attributes_json ->> '{json_key}') LIKE :q)
                GROUP BY psp.attributes_json ->> '{json_key}'
                ORDER BY count DESC, value ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [
            {"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)}
            for row in rows
        ]

    raise ValueError(f"Pokémon facet values are not available for: {key}")
