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


def yugioh_facet_values(
    session,
    *,
    key: str,
    query: str | None = None,
    limit: int = 30,
) -> list[dict]:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh facet values require PostgreSQL")

    key = str(key or "").strip().lower()
    q = normalize_search_text(query or "")
    q_compact = compact_search_text(query or "")
    params: dict[str, object] = {
        "game": "yugioh",
        "limit": _limit(limit),
        "q": f"%{q}%",
        "q_compact": f"%{q_compact}%",
    }

    if key == "set":
        rows = session.execute(
            text(
                """
                SELECT s.code AS value, s.code AS label, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                JOIN sets s ON s.id=p.set_id
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND (
                    :q='%%'
                    OR lower(s.code) LIKE :q
                    OR regexp_replace(lower(s.code),'[^a-z0-9]','','g') LIKE :q_compact
                  )
                GROUP BY s.code
                ORDER BY count DESC, s.code ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    if key == "collector_number":
        rows = session.execute(
            text(
                """
                SELECT p.collector_number AS value, c.name AS card_name, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                JOIN cards c ON c.id=psp.card_id
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND (
                    :q='%%'
                    OR lower(p.collector_number) LIKE :q
                    OR lower(c.name) LIKE :q
                    OR regexp_replace(lower(p.collector_number),'[^a-z0-9]','','g') LIKE :q_compact
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
                "label": f"{row['value']} · {row['card_name']}",
                "count": int(row["count"] or 0),
            }
            for row in rows
        ]

    if key == "release":
        rows = session.execute(
            text(
                """
                SELECT cr.name AS value, cr.name AS label, COUNT(pr.id) AS count
                FROM catalog_releases cr
                JOIN games g ON g.id=cr.game_id
                LEFT JOIN print_releases pr ON pr.release_id=cr.id
                WHERE g.slug=:game
                  AND (:q='%%' OR lower(cr.name) LIKE :q)
                GROUP BY cr.id, cr.name
                ORDER BY count DESC, cr.name ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    if key in {"language", "rarity"}:
        column = "psp.language" if key == "language" else "psp.rarity"
        rows = session.execute(
            text(
                f"""
                SELECT {column} AS value, {column} AS label, COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND {column} IS NOT NULL
                  AND (:q='%%' OR lower(CAST({column} AS text)) LIKE :q)
                GROUP BY {column}
                ORDER BY count DESC, value ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    card_scalars = {
        "card_class": "card_class",
        "card_type": "card_type",
        "frame_type": "frame_type",
        "attribute": "attribute",
        "race": "race",
        "archetype": "archetype",
    }
    if key in card_scalars:
        json_key = card_scalars[key]
        rows = session.execute(
            text(
                f"""
                SELECT csp.attributes_json->>'{json_key}' AS value,
                       csp.attributes_json->>'{json_key}' AS label,
                       COUNT(*) AS count
                FROM card_search_profiles csp
                JOIN games g ON g.id=csp.game_id
                WHERE g.slug=:game
                  AND COALESCE(csp.attributes_json->>'{json_key}','')<>''
                  AND (:q='%%' OR lower(csp.attributes_json->>'{json_key}') LIKE :q)
                GROUP BY csp.attributes_json->>'{json_key}'
                ORDER BY count DESC, value ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    if key == "link_marker":
        rows = session.execute(
            text(
                """
                SELECT marker AS value, marker AS label, COUNT(*) AS count
                FROM card_search_profiles csp
                JOIN games g ON g.id=csp.game_id
                CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(csp.attributes_json->'link_markers','[]'::jsonb)) marker
                WHERE g.slug=:game
                  AND (:q='%%' OR lower(marker) LIKE :q)
                GROUP BY marker
                ORDER BY count DESC, marker ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    if key == "release_year":
        rows = session.execute(
            text(
                """
                SELECT psp.attributes_json->>'release_year' AS value,
                       psp.attributes_json->>'release_year' AS label,
                       COUNT(*) AS count
                FROM print_search_profiles psp
                JOIN games g ON g.id=psp.game_id
                WHERE g.slug=:game
                  AND COALESCE(psp.attributes_json->>'release_year','')<>''
                GROUP BY psp.attributes_json->>'release_year'
                ORDER BY value DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
        return [{"value": row["value"], "label": row["label"], "count": int(row["count"] or 0)} for row in rows]

    raise ValueError(f"Yu-Gi-Oh facet values are not available for: {key}")
