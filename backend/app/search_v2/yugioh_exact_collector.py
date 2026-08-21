from __future__ import annotations

import re

from sqlalchemy import text

from app.search_v2.normalization import normalize_language, normalize_search_text


SUPPORTED_DISPLAY_LANGUAGES = {"en", "es", "ja"}
_COLLECTOR_RE = re.compile(r"^[a-z0-9]+(?:[\s_-][a-z0-9]+)+$", re.IGNORECASE)


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


def _collector_code(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw or not _COLLECTOR_RE.fullmatch(raw) or not any(ch.isdigit() for ch in raw):
        return None
    normalized = normalize_search_text(raw)
    return re.sub(r"-+", "-", normalized.replace(" ", "-")).strip("-") or None


def exact_yugioh_collector_search(
    session,
    *,
    query: str,
    game: str | None,
    limit: int = 24,
    language: str | None = None,
) -> list[dict] | None:
    """Resolve an exact Yu-Gi-Oh collector code without fuzzy ranking.

    ``None`` means the query is not collector-shaped. A list, including an
    empty list, means it is collector-shaped and therefore fails closed instead
    of falling through to unrelated name/similarity matches.
    """
    if game != "yugioh":
        return None
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh exact collector search requires PostgreSQL")

    q_code = _collector_code(query)
    if not q_code:
        return None

    display_language = _display_language(language)
    bounded_limit = max(1, min(int(limit or 24), 100))

    rows = session.execute(
        text(
            """
            WITH exact_cards AS MATERIALIZED (
              SELECT DISTINCT psp.card_id
              FROM print_search_profiles psp
              JOIN prints p ON p.id=psp.print_id
              JOIN games g ON g.id=psp.game_id
              WHERE g.slug='yugioh'
                AND psp.normalized_collector_number=:q_code
                AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                AND (
                  :display_language IS NULL
                  OR lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ','))
                )
              ORDER BY psp.card_id ASC
              LIMIT :limit
            )
            SELECT
              c.id AS card_id,
              c.card_key,
              c.name AS canonical_name,
              COALESCE(best.localized_card_name, c.name) AS display_name,
              best.display_language,
              best.available_languages,
              csp.attributes_json AS card_attributes,
              best.print_id,
              best.set_code,
              COALESCE(best.localized_set_name, best.canonical_set_name) AS set_name,
              best.collector_number,
              best.language,
              best.rarity,
              best.exact_variant,
              best.variant_family,
              best.release_names_json,
              best.print_attributes,
              best.primary_image_url,
              best.variant_count
            FROM exact_cards ec
            JOIN cards c ON c.id=ec.card_id
            JOIN card_search_profiles csp ON csp.card_id=c.id
            JOIN LATERAL (
              SELECT
                psp.print_id,
                s.code AS set_code,
                s.name AS canonical_set_name,
                p.collector_number,
                lower(coalesce(p.language,'')) AS language,
                p.rarity,
                psp.exact_variant,
                psp.variant_family,
                psp.release_names_json,
                psp.attributes_json AS print_attributes,
                loc.card_name AS localized_card_name,
                loc.set_name AS localized_set_name,
                COALESCE(loc.language, lower(coalesce(p.language,''))) AS display_language,
                (
                  SELECT array_agg(DISTINCT lower(p2.language) ORDER BY lower(p2.language))
                  FROM prints p2
                  JOIN print_search_profiles psp2 ON psp2.print_id=p2.id
                  WHERE p2.card_id=p.card_id
                    AND psp2.normalized_collector_number=:q_code
                    AND lower(coalesce(p2.language,'')) IN ('en','es','ja')
                ) AS available_languages,
                (
                  SELECT pi.url
                  FROM print_images pi
                  WHERE pi.print_id=psp.print_id
                  ORDER BY pi.is_primary DESC, pi.id ASC
                  LIMIT 1
                ) AS primary_image_url,
                COUNT(*) OVER () AS variant_count
              FROM print_search_profiles psp
              JOIN prints p ON p.id=psp.print_id
              JOIN sets s ON s.id=p.set_id
              LEFT JOIN LATERAL (
                SELECT lower(pl.language) AS language, pl.card_name, pl.set_name
                FROM print_localizations pl
                WHERE pl.print_id=p.id
                  AND lower(pl.language)=lower(coalesce(p.language,''))
                ORDER BY pl.id ASC
                LIMIT 1
              ) loc ON TRUE
              WHERE psp.card_id=c.id
                AND psp.normalized_collector_number=:q_code
                AND lower(coalesce(p.language,'')) IN ('en','es','ja')
                AND (
                  :display_language IS NULL
                  OR lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ','))
                )
              ORDER BY
                CASE lower(coalesce(p.language,'')) WHEN 'en' THEN 0 WHEN 'es' THEN 1 WHEN 'ja' THEN 2 ELSE 3 END,
                p.id ASC
              LIMIT 1
            ) best ON TRUE
            ORDER BY c.id ASC
            LIMIT :limit
            """
        ),
        {
            "q_code": q_code,
            "display_language": display_language,
            "limit": bounded_limit,
        },
    ).mappings().all()

    return [
        {
            "type": "card",
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "name": row["display_name"],
            "canonical_name": row["canonical_name"],
            "game": "yugioh",
            "display_language": row["display_language"],
            "available_languages": list(row["available_languages"] or []),
            "matched_print": {
                "print_id": row["print_id"],
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
                "display_language": row["display_language"],
                "available_languages": list(row["available_languages"] or []),
                "rarity": row["rarity"],
                "exact_variant": row["exact_variant"],
                "variant_family": row["variant_family"],
                "release_names": row["release_names_json"] or [],
                "release_year": (row["print_attributes"] or {}).get("release_year"),
                "primary_image_url": row["primary_image_url"],
            },
            "variant_count": int(row["variant_count"] or 0),
            "attributes": row["card_attributes"] or {},
            "score": 10000.0,
        }
        for row in rows
    ]