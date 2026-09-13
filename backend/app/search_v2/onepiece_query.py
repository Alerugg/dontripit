from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text
from app.search_v2.query import (
    _find_onepiece_set,
    _parse_onepiece_intent,
    _parse_onepiece_numeric_bounds,
    _query_tokens,
)


def _plain_name_query(query: str) -> tuple[str, list[str]] | None:
    """Return normalized free-text only when One Piece has no structured intent."""
    raw = str(query or "").strip()
    q_norm = normalize_search_text(raw)
    if not q_norm:
        return None

    tokens = _query_tokens(raw)
    set_code = _find_onepiece_set(tokens, raw)
    numeric_bounds = _parse_onepiece_numeric_bounds(raw)
    intent, residual = _parse_onepiece_intent(tokens, set_code, numeric_bounds)
    if intent:
        return None

    # Preserve every free-text term for name ranking. Numeric terms can be part
    # of real character/edition names (for example Gear 5), so they are not
    # rejected merely for containing digits.
    return q_norm, residual or tokens


def fast_onepiece_name_search(
    session,
    *,
    query: str,
    limit: int = 24,
) -> list[dict] | None:
    """Fast logical-card search for plain One Piece names.

    ``None`` means the request carries structured One Piece intent and must be
    delegated to the complete generic engine. Plain name requests search the
    compact card projection first and hydrate physical evidence only for the
    winning cards, avoiding the global print-profile aggregation on every key
    stroke.
    """
    if session.bind.dialect.name != "postgresql":
        return None

    parsed = _plain_name_query(query)
    if parsed is None:
        return None
    q_norm, tokens = parsed
    bounded_limit = max(1, min(int(limit or 24), 100))
    candidate_limit = max(48, bounded_limit * 4)

    params: dict[str, object] = {
        "q_norm": q_norm,
        "prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "limit": bounded_limit,
        "candidate_limit": candidate_limit,
    }
    token_predicates: list[str] = []
    token_scores: list[str] = []
    for index, token in enumerate(tokens[:8]):
        if len(token) < 2:
            continue
        params[f"token_{index}"] = f"%{token}%"
        token_predicates.append(f"csp.search_text LIKE :token_{index}")
        token_scores.append(
            f"CASE WHEN csp.normalized_name LIKE :token_{index} THEN 55.0 "
            f"WHEN csp.search_text LIKE :token_{index} THEN 18.0 ELSE 0.0 END"
        )

    token_where = " OR ".join(token_predicates) or "FALSE"
    token_score = " + ".join(token_scores) or "0.0"

    def _sql(*, broad_similarity: bool):
        broad = "OR similarity(csp.normalized_name, :q_norm) >= 0.20" if broad_similarity else ""
        return text(
            f"""
            WITH candidates AS MATERIALIZED (
              SELECT
                csp.card_id,
                (
                  CASE WHEN csp.normalized_name = :q_norm THEN 10000.0 ELSE 0.0 END +
                  CASE WHEN csp.normalized_name LIKE :prefix THEN 4200.0 ELSE 0.0 END +
                  CASE WHEN csp.normalized_name LIKE :contains THEN 1800.0 ELSE 0.0 END +
                  CASE WHEN csp.search_text LIKE :contains THEN 500.0 ELSE 0.0 END +
                  {token_score} +
                  similarity(csp.normalized_name, :q_norm) * 900.0
                ) AS score
              FROM card_search_profiles csp
              JOIN games g ON g.id = csp.game_id
              WHERE g.slug = 'onepiece'
                AND (
                  csp.normalized_name = :q_norm OR
                  csp.normalized_name LIKE :prefix OR
                  csp.normalized_name LIKE :contains OR
                  csp.search_text LIKE :contains OR
                  ({token_where})
                  {broad}
                )
              ORDER BY score DESC, csp.card_id ASC
              LIMIT :candidate_limit
            ),
            top_candidates AS MATERIALIZED (
              SELECT card_id, score
              FROM candidates
              ORDER BY score DESC, card_id ASC
              LIMIT :limit
            )
            SELECT
              c.id AS card_id,
              c.card_key,
              c.name,
              csp.attributes_json,
              best.print_id,
              best.set_code,
              best.set_name,
              best.collector_number,
              best.language,
              best.rarity,
              best.exact_variant,
              best.variant_family,
              best.primary_image_url,
              best.variant_count,
              tc.score
            FROM top_candidates tc
            JOIN cards c ON c.id = tc.card_id
            JOIN card_search_profiles csp ON csp.card_id = c.id
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
                  WHERE pi.print_id = p.id
                  ORDER BY pi.is_primary DESC, pi.id ASC
                  LIMIT 1
                ) AS primary_image_url,
                COUNT(*) OVER () AS variant_count
              FROM print_search_profiles psp
              JOIN prints p ON p.id = psp.print_id
              JOIN sets s ON s.id = p.set_id
              WHERE psp.card_id = tc.card_id
              ORDER BY
                CASE WHEN lower(coalesce(p.language, '')) = 'en' THEN 0 ELSE 1 END,
                CASE WHEN lower(coalesce(p.variant, '')) IN ('default', 'base', '') THEN 0 ELSE 1 END,
                p.id ASC
              LIMIT 1
            ) best ON TRUE
            ORDER BY tc.score DESC, lower(c.name) ASC, c.id ASC
            """
        )

    rows = session.execute(_sql(broad_similarity=False), params).mappings().all()
    if not rows:
        rows = session.execute(_sql(broad_similarity=True), params).mappings().all()

    return [
        {
            "type": "card",
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "name": row["name"],
            "game": "onepiece",
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
            "attributes": row["attributes_json"] or {},
            "score": round(float(row["score"] or 0), 4),
        }
        for row in rows
    ]
