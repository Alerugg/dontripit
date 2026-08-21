from __future__ import annotations

import re

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text


_STRUCTURED_ID_RE = re.compile(r"^[a-z0-9]+(?:[\s_-][a-z0-9]+)+$", re.IGNORECASE)


def _structured_code(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw or not _STRUCTURED_ID_RE.fullmatch(raw):
        return None
    # Avoid treating ordinary hyphenated names as identifiers. Real catalog
    # identifiers in these games contain at least one digit.
    if not any(ch.isdigit() for ch in raw):
        return None
    normalized = normalize_search_text(raw)
    return re.sub(r"-+", "-", normalized.replace(" ", "-")).strip("-") or None


def exact_structured_identifier_search(
    session,
    *,
    query: str,
    game: str | None,
    limit: int = 24,
) -> list[dict] | None:
    """Resolve a structured catalog identifier without invoking fuzzy search.

    ``None`` means the query is not identifier-shaped and the normal name/fuzzy
    engine should run. A list (including an empty list) means the query is an
    identifier and must fail closed instead of returning unrelated fuzzy cards.

    One Piece keeps its dedicated collector parser because it also accepts
    compact promo forms such as P135/P 135. Yu-Gi-Oh keeps its localization-aware
    exact collector ranking. This fast path currently targets Pokémon and MTG,
    where exact IDs otherwise fall through to the natural-name engine.
    """
    if game not in {"pokemon", "mtg"}:
        return None
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Exact structured identifier search requires PostgreSQL")

    q_code = _structured_code(query)
    if not q_code:
        return None

    bounded_limit = max(1, min(int(limit or 24), 100))
    pokemon_card_key = f"pokemon:tcgdex:{q_code}" if game == "pokemon" else ""

    rows = session.execute(
        text(
            """
            WITH candidates AS MATERIALIZED (
              SELECT c.id AS card_id, 0 AS source_rank
              FROM cards c
              JOIN games g ON g.id=c.game_id
              WHERE g.slug=:game
                AND :pokemon_card_key <> ''
                AND lower(c.card_key)=:pokemon_card_key

              UNION ALL

              SELECT psp.card_id, 1 AS source_rank
              FROM print_search_profiles psp
              JOIN games g ON g.id=psp.game_id
              WHERE g.slug=:game
                AND (
                  psp.normalized_collector_number=:q_code
                  OR (
                    psp.normalized_set_code IS NOT NULL
                    AND psp.normalized_collector_number IS NOT NULL
                    AND psp.normalized_set_code || '-' || psp.normalized_collector_number=:q_code
                  )
                )
            ),
            ranked_cards AS MATERIALIZED (
              SELECT card_id, MIN(source_rank) AS source_rank
              FROM candidates
              GROUP BY card_id
              ORDER BY source_rank ASC, card_id ASC
              LIMIT :limit
            )
            SELECT
              c.id AS card_id,
              c.card_key,
              c.name,
              g.slug AS game,
              csp.attributes_json AS card_attributes,
              best.print_id,
              best.set_code,
              best.set_name,
              best.collector_number,
              best.language,
              best.rarity,
              best.exact_variant,
              best.variant_family,
              best.primary_image_url,
              best.variant_count
            FROM ranked_cards rc
            JOIN cards c ON c.id=rc.card_id
            JOIN games g ON g.id=c.game_id
            JOIN card_search_profiles csp ON csp.card_id=c.id
            JOIN LATERAL (
              SELECT
                psp.print_id,
                s.code AS set_code,
                s.name AS set_name,
                p.collector_number,
                p.language,
                p.rarity,
                psp.exact_variant,
                psp.variant_family,
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
              WHERE psp.card_id=c.id
              ORDER BY
                (psp.normalized_collector_number=:q_code) DESC,
                (
                  psp.normalized_set_code IS NOT NULL
                  AND psp.normalized_collector_number IS NOT NULL
                  AND psp.normalized_set_code || '-' || psp.normalized_collector_number=:q_code
                ) DESC,
                CASE lower(coalesce(p.language,'')) WHEN 'en' THEN 0 ELSE 1 END,
                p.id ASC
              LIMIT 1
            ) best ON TRUE
            ORDER BY rc.source_rank ASC, c.id ASC
            LIMIT :limit
            """
        ),
        {
            "game": game,
            "q_code": q_code,
            "pokemon_card_key": pokemon_card_key,
            "limit": bounded_limit,
        },
    ).mappings().all()

    return [
        {
            "type": "card",
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "name": row["name"],
            "game": row["game"],
            "matched_print": {
                "print_id": row["print_id"],
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
                "rarity": row["rarity"],
                "exact_variant": row["exact_variant"],
                "variant_family": row["variant_family"],
                "primary_image_url": row["primary_image_url"],
            },
            "variant_count": int(row["variant_count"] or 0),
            "attributes": row["card_attributes"] or {},
            "score": 10000.0,
        }
        for row in rows
    ]