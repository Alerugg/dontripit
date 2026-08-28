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


def _set_collector_parts(q_code: str) -> tuple[str, str] | None:
    """Split canonical set-collector IDs without building indexed columns at query time."""
    if "-" not in q_code:
        return None
    set_code, collector = q_code.split("-", 1)
    if not set_code or not collector:
        return None
    return set_code, collector


def exact_structured_identifier_search(
    session,
    *,
    query: str,
    game: str | None,
    limit: int = 24,
) -> list[dict] | None:
    """Resolve Pokémon/MTG structured IDs on indexable equality predicates.

    ``None`` means the query is not identifier-shaped and the normal name/fuzzy
    engine should run. A list (including an empty list) means the query is an
    identifier and must fail closed instead of returning unrelated fuzzy cards.

    Both games resolve through the indexed print-search projection. This is
    required for Pokémon because canonical identity consolidation can replace a
    TCGdex-shaped Card key while the exact physical set + collector identity is
    preserved on the Print projection.
    """
    if game not in {"pokemon", "mtg"}:
        return None
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Exact structured identifier search requires PostgreSQL")

    q_code = _structured_code(query)
    if not q_code:
        return None
    parts = _set_collector_parts(q_code)
    if parts is None:
        return []

    set_code, collector = parts
    bounded_limit = max(1, min(int(limit or 24), 100))

    candidate_sql = """
      SELECT DISTINCT psp.card_id, 0 AS source_rank
      FROM print_search_profiles psp
      JOIN games g ON g.id=psp.game_id
      WHERE g.slug=:game
        AND psp.normalized_set_code=:set_code
        AND psp.normalized_collector_number=:collector
      ORDER BY psp.card_id ASC
      LIMIT :limit
    """

    rows = session.execute(
        text(
            f"""
            WITH ranked_cards AS MATERIALIZED (
              {candidate_sql}
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
            LEFT JOIN card_search_profiles csp ON csp.card_id=c.id
            JOIN LATERAL (
              SELECT
                p.id AS print_id,
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
                  WHERE pi.print_id=p.id
                  ORDER BY pi.is_primary DESC, pi.id ASC
                  LIMIT 1
                ) AS primary_image_url,
                COUNT(*) OVER () AS variant_count
              FROM prints p
              JOIN sets s ON s.id=p.set_id
              LEFT JOIN print_search_profiles psp ON psp.print_id=p.id
              WHERE p.card_id=c.id
              ORDER BY
                (
                  lower(s.code)=:set_code
                  AND lower(p.collector_number)=:collector
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
            "set_code": set_code,
            "collector": collector,
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
