from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text


def normal_pokemon_search(session, *, query: str, limit: int = 24) -> list[dict]:
    """Fast Google-like Pokémon search: rank Cards first, resolve one Print second.

    Pokémon has many physical variants per source card. Ranking all Prints makes
    common names such as Pikachu unnecessarily expensive and overweights cards
    merely because they have many finishes. This path ranks one CardSearchProfile
    per canonical identity, then resolves the best physical Print with a lateral
    lookup and reports the exact variant count separately.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Fast Pokémon natural search requires PostgreSQL")

    q = str(query or "").strip()
    if not q:
        return []
    q_norm = normalize_search_text(q)
    tokens = [token for token in q_norm.split() if len(token) >= 2][:8]
    bounded_limit = max(1, min(int(limit or 24), 100))
    # matched_cards is already relevance-ordered before family reranking. Four
    # pages of headroom is enough to preserve the top results while avoiding the
    # old 240-300 correlated family-count lookups on every typo/fuzzy request.
    candidate_limit = max(80, bounded_limit * 4)

    token_where = " AND ".join(
        f"csp.search_text LIKE :token_{idx}" for idx in range(len(tokens))
    )
    token_bonus = " + ".join(
        f"CASE WHEN csp.search_text LIKE :token_{idx} THEN 35.0 ELSE 0.0 END"
        for idx in range(len(tokens))
    ) or "0.0"
    token_params = {f"token_{idx}": f"%{token}%" for idx, token in enumerate(tokens)}

    candidate_predicate = """
      csp.normalized_name = :q_norm
      OR csp.normalized_name LIKE :prefix
      OR csp.normalized_name LIKE :contains
      OR csp.search_text LIKE :contains
      OR similarity(csp.normalized_name, :q_norm) >= 0.20
    """
    if token_where:
        candidate_predicate = f"({candidate_predicate}) OR ({token_where})"

    sql = text(
        f"""
        WITH matched_cards AS MATERIALIZED (
          SELECT
            csp.card_id,
            csp.game_id,
            csp.normalized_name,
            csp.attributes_json,
            COUNT(*) OVER (
              PARTITION BY csp.game_id, csp.normalized_name
            ) AS name_identity_count,
            (
              CASE WHEN csp.normalized_name = :q_norm THEN 5000.0 ELSE 0.0 END +
              CASE WHEN csp.normalized_name LIKE :prefix THEN 2200.0 ELSE 0.0 END +
              CASE WHEN (' ' || csp.normalized_name || ' ') LIKE :word THEN 1700.0 ELSE 0.0 END +
              CASE WHEN csp.normalized_name LIKE :contains THEN 1200.0 ELSE 0.0 END +
              CASE WHEN csp.search_text LIKE :contains THEN 350.0 ELSE 0.0 END +
              {token_bonus} +
              similarity(csp.normalized_name, :q_norm) * 900.0
            ) AS relevance_score
          FROM card_search_profiles csp
          JOIN games g ON g.id=csp.game_id
          WHERE g.slug='pokemon'
            AND ({candidate_predicate})
        ),
        pre_candidates AS MATERIALIZED (
          SELECT
            card_id,
            game_id,
            normalized_name,
            attributes_json,
            name_identity_count,
            relevance_score
          FROM matched_cards
          ORDER BY relevance_score DESC, card_id ASC
          LIMIT :candidate_limit
        ),
        candidates AS MATERIALIZED (
          SELECT
            card_id,
            attributes_json,
            (
              relevance_score +
              LEAST(
                name_identity_count + (
                  SELECT COUNT(*)
                  FROM card_search_profiles related
                  WHERE related.game_id=pre_candidates.game_id
                    AND related.normalized_name LIKE pre_candidates.normalized_name || ' %'
                ),
                50
              ) * 32.0
            ) AS score
          FROM pre_candidates
          -- Short prefixes naturally favor shorter words under trigram similarity.
          -- Bounded catalog-family coverage (base name plus named variants) is a
          -- data-derived tie-breaker; no individual character is hard-coded.
          ORDER BY score DESC, card_id ASC
          LIMIT :limit
        )
        SELECT
          c.id AS card_id,
          c.card_key,
          c.name,
          'pokemon' AS game,
          best.print_id,
          best.set_code,
          best.set_name,
          best.collector_number,
          best.language,
          best.rarity,
          best.exact_variant,
          best.variant_family,
          best.attributes_json,
          best.primary_image_url,
          best.variant_count,
          candidates.score
        FROM candidates
        JOIN cards c ON c.id=candidates.card_id
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
            psp.attributes_json,
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
          WHERE psp.card_id=candidates.card_id
          ORDER BY (p.tcgdex_id IS NOT NULL) DESC, p.id ASC
          LIMIT 1
        ) best ON TRUE
        ORDER BY candidates.score DESC, c.name ASC, c.id ASC
        LIMIT :limit
        """
    )
    params = {
        "q_norm": q_norm,
        "prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "word": f"% {q_norm} %",
        "candidate_limit": candidate_limit,
        "limit": bounded_limit,
        **token_params,
    }
    rows = session.execute(sql, params).mappings().all()
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
            "attributes": row["attributes_json"] or {},
            "score": round(float(row["score"] or 0), 4),
        }
        for row in rows
    ]
