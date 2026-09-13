from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text


def fast_yugioh_name_search(
    session,
    *,
    query: str,
    limit: int = 24,
) -> list[dict] | None:
    """Fast default-language path for ordinary ASCII Yu-Gi-Oh card names.

    Explicit language/CJK/localized requests stay on the complete localization-
    aware engine. This path searches the compact Card projection first, hydrates
    only winners, and uses an OR token anchor so typo queries such as
    ``Blu-Eyes Wite Dragon`` avoid a full print-profile scan.
    """
    if session.bind.dialect.name != "postgresql":
        return None

    raw = str(query or "").strip()
    q_norm = normalize_search_text(raw)
    if not raw or not q_norm:
        return None

    bounded_limit = max(1, min(int(limit or 24), 100))
    candidate_limit = max(48, bounded_limit * 4)
    tokens = [token for token in q_norm.split() if len(token) >= 2][:8]

    params: dict[str, object] = {
        "q_norm": q_norm,
        "q_raw": raw.casefold(),
        "prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "localized_contains": f"%{raw.casefold()}%",
        "limit": bounded_limit,
        "candidate_limit": candidate_limit,
    }
    token_predicates: list[str] = []
    token_scores: list[str] = []
    for index, token in enumerate(tokens):
        params[f"token_{index}"] = f"%{token}%"
        token_predicates.append(f"csp.search_text LIKE :token_{index}")
        token_scores.append(
            f"CASE WHEN csp.normalized_name LIKE :token_{index} THEN 45.0 "
            f"WHEN csp.search_text LIKE :token_{index} THEN 15.0 ELSE 0.0 END"
        )

    token_where = " OR ".join(token_predicates) or "FALSE"
    token_score = " + ".join(token_scores) or "0.0"

    def _card_sql(*, broad_similarity: bool):
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
              WHERE g.slug = 'yugioh'
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
              c.name AS canonical_name,
              c.name AS display_name,
              csp.attributes_json AS card_attributes,
              best.print_id,
              best.set_code,
              best.set_name,
              best.collector_number,
              best.language,
              best.rarity,
              best.exact_variant,
              best.variant_family,
              best.release_names_json,
              best.print_attributes,
              best.primary_image_url,
              best.variant_count,
              best.available_languages,
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
                lower(coalesce(p.language, '')) AS language,
                p.rarity,
                psp.exact_variant,
                psp.variant_family,
                psp.release_names_json,
                psp.attributes_json AS print_attributes,
                (
                  SELECT pi.url
                  FROM print_images pi
                  WHERE pi.print_id = p.id
                  ORDER BY pi.is_primary DESC, pi.id ASC
                  LIMIT 1
                ) AS primary_image_url,
                (
                  SELECT array_agg(DISTINCT lower(p2.language) ORDER BY lower(p2.language))
                  FROM prints p2
                  WHERE p2.card_id = p.card_id
                    AND lower(coalesce(p2.language, '')) IN ('en','es','ja')
                ) AS available_languages,
                COUNT(*) OVER () AS variant_count
              FROM print_search_profiles psp
              JOIN prints p ON p.id = psp.print_id
              JOIN sets s ON s.id = p.set_id
              WHERE psp.card_id = tc.card_id
                AND lower(coalesce(p.language, '')) IN ('en','es','ja')
              ORDER BY
                CASE lower(coalesce(p.language, '')) WHEN 'en' THEN 0 WHEN 'es' THEN 1 WHEN 'ja' THEN 2 ELSE 3 END,
                CASE WHEN lower(coalesce(p.variant, '')) IN ('default','base','') THEN 0 ELSE 1 END,
                p.id ASC
              LIMIT 1
            ) best ON TRUE
            ORDER BY tc.score DESC, lower(c.name) ASC, c.id ASC
            """
        )

    rows = session.execute(_card_sql(broad_similarity=False), params).mappings().all()
    if not rows:
        # A long digit-bearing token that failed every indexed card/name anchor
        # is overwhelmingly a missing collector/code-like request. Exact-code
        # resolution already ran before this helper, so avoid an expensive fuzzy
        # universe scan for that shape.
        if any(ch.isdigit() for ch in q_norm) and len(tokens) <= 2:
            return []
        rows = session.execute(_card_sql(broad_similarity=True), params).mappings().all()

    if not rows:
        # Preserve the old no-language behavior for Spanish localized names, but
        # do so only after the compact canonical index misses. This is a scoped
        # localization lookup rather than the previous global print signal.
        localized_sql = text(
            """
            WITH localized AS MATERIALIZED (
              SELECT
                p.card_id,
                MAX(
                  CASE WHEN lower(pl.card_name) = :q_raw THEN 6500.0 ELSE 0.0 END +
                  CASE WHEN left(lower(pl.card_name), length(:q_raw)) = :q_raw THEN 3000.0 ELSE 0.0 END +
                  CASE WHEN position(:q_raw in lower(pl.card_name)) > 0 THEN 1700.0 ELSE 0.0 END
                ) AS score
              FROM print_localizations pl
              JOIN prints p ON p.id = pl.print_id
              JOIN cards c ON c.id = p.card_id
              JOIN games g ON g.id = c.game_id
              WHERE g.slug = 'yugioh'
                AND lower(coalesce(p.language, '')) IN ('es','ja')
                AND lower(pl.language) = lower(coalesce(p.language, ''))
                AND pl.card_name IS NOT NULL
                AND lower(pl.card_name) LIKE :localized_contains
              GROUP BY p.card_id
              ORDER BY score DESC, p.card_id ASC
              LIMIT :candidate_limit
            )
            SELECT card_id
            FROM localized
            ORDER BY score DESC, card_id ASC
            LIMIT :limit
            """
        )
        localized_ids = [
            int(row["card_id"])
            for row in session.execute(localized_sql, params).mappings().all()
        ]
        if localized_ids:
            # Delegate only actual localization hits to the complete engine so
            # display names/languages remain identical to existing behavior.
            return None
        return []

    return [
        {
            "type": "card",
            "card_id": row["card_id"],
            "card_key": row["card_key"],
            "name": row["display_name"],
            "canonical_name": row["canonical_name"],
            "game": "yugioh",
            "display_language": row["language"],
            "available_languages": list(row["available_languages"] or []),
            "matched_print": {
                "print_id": row["print_id"],
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
                "display_language": row["language"],
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
            "score": round(float(row["score"] or 0), 4),
        }
        for row in rows
    ]
