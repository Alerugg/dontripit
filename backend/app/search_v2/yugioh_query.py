from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text


def normal_yugioh_search(session, *, query: str, limit: int = 24) -> list[dict]:
    """Rank logical Cards first while still recognizing Print/release codes.

    Yu-Gi-Oh! has many physical Prints per logical Card. Ranking all Prints would
    overweight heavily reprinted cards. This path combines a Card signal with a
    Print/release signal, aggregates to one score per canonical Card, then resolves
    the best matching physical Print in a lateral lookup.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh Search V2 requires PostgreSQL")

    q = str(query or "").strip()
    if not q:
        return []
    q_norm = normalize_search_text(q)
    q_code = q_norm.replace(" ", "-")
    bounded_limit = max(1, min(int(limit or 24), 100))
    candidate_limit = max(100, bounded_limit * 10)

    tokens = [token for token in q_norm.split() if len(token) >= 2][:8]
    token_params = {f"token_{idx}": f"%{token}%" for idx, token in enumerate(tokens)}
    token_card_bonus = " + ".join(
        f"CASE WHEN csp.search_text LIKE :token_{idx} THEN 30.0 ELSE 0.0 END"
        for idx in range(len(tokens))
    ) or "0.0"
    token_print_bonus = " + ".join(
        f"CASE WHEN psp.search_text LIKE :token_{idx} THEN 20.0 ELSE 0.0 END"
        for idx in range(len(tokens))
    ) or "0.0"
    token_card_where = " AND ".join(
        f"csp.search_text LIKE :token_{idx}" for idx in range(len(tokens))
    )
    token_print_where = " AND ".join(
        f"psp.search_text LIKE :token_{idx}" for idx in range(len(tokens))
    )

    card_predicate = """
      csp.normalized_name = :q_norm
      OR csp.normalized_name LIKE :prefix
      OR csp.normalized_name LIKE :contains
      OR csp.search_text LIKE :contains
      OR similarity(csp.normalized_name, :q_norm) >= 0.20
    """
    print_predicate = """
      psp.normalized_collector_number = :q_code
      OR psp.normalized_set_code = :q_code
      OR psp.normalized_name = :q_norm
      OR psp.normalized_name LIKE :prefix
      OR psp.search_text LIKE :contains
    """
    if token_card_where:
        card_predicate = f"({card_predicate}) OR ({token_card_where})"
        print_predicate = f"({print_predicate}) OR ({token_print_where})"

    sql = text(
        f"""
        WITH card_signal AS MATERIALIZED (
          SELECT
            csp.card_id,
            (
              CASE WHEN csp.normalized_name = :q_norm THEN 6000.0 ELSE 0.0 END +
              CASE WHEN csp.normalized_name LIKE :prefix THEN 2600.0 ELSE 0.0 END +
              CASE WHEN (' ' || csp.normalized_name || ' ') LIKE :word THEN 1900.0 ELSE 0.0 END +
              CASE WHEN csp.normalized_name LIKE :contains THEN 1300.0 ELSE 0.0 END +
              CASE WHEN csp.search_text LIKE :contains THEN 300.0 ELSE 0.0 END +
              {token_card_bonus} +
              similarity(csp.normalized_name, :q_norm) * 900.0
            ) AS score
          FROM card_search_profiles csp
          JOIN games g ON g.id=csp.game_id
          WHERE g.slug='yugioh' AND ({card_predicate})
          ORDER BY score DESC, csp.card_id ASC
          LIMIT :candidate_limit
        ),
        print_signal AS MATERIALIZED (
          SELECT
            psp.card_id,
            MAX(
              CASE WHEN psp.normalized_collector_number = :q_code THEN 8000.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_set_code = :q_code THEN 5200.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name = :q_norm THEN 5000.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE :prefix THEN 2200.0 ELSE 0.0 END +
              CASE WHEN psp.search_text LIKE :contains THEN 1000.0 ELSE 0.0 END +
              {token_print_bonus}
            ) AS score
          FROM print_search_profiles psp
          JOIN games g ON g.id=psp.game_id
          WHERE g.slug='yugioh' AND ({print_predicate})
          GROUP BY psp.card_id
          ORDER BY score DESC, psp.card_id ASC
          LIMIT :candidate_limit
        ),
        candidates AS MATERIALIZED (
          SELECT card_id, MAX(score) AS score
          FROM (
            SELECT * FROM card_signal
            UNION ALL
            SELECT * FROM print_signal
          ) signals
          GROUP BY card_id
          ORDER BY score DESC, card_id ASC
          LIMIT :candidate_limit
        )
        SELECT
          c.id AS card_id,
          c.card_key,
          c.name,
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
          candidates.score
        FROM candidates
        JOIN cards c ON c.id=candidates.card_id
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
            psp.release_names_json,
            psp.attributes_json AS print_attributes,
            (
              SELECT pi.url FROM print_images pi
              WHERE pi.print_id=psp.print_id
              ORDER BY pi.is_primary DESC, pi.id ASC
              LIMIT 1
            ) AS primary_image_url,
            COUNT(*) OVER () AS variant_count
          FROM print_search_profiles psp
          JOIN prints p ON p.id=psp.print_id
          JOIN sets s ON s.id=p.set_id
          WHERE psp.card_id=candidates.card_id
          ORDER BY
            (psp.normalized_collector_number = :q_code) DESC,
            (psp.normalized_set_code = :q_code) DESC,
            (psp.search_text LIKE :contains) DESC,
            p.id ASC
          LIMIT 1
        ) best ON TRUE
        ORDER BY candidates.score DESC, c.name ASC, c.id ASC
        LIMIT :limit
        """
    )
    params = {
        "q_norm": q_norm,
        "q_code": q_code,
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
            "game": "yugioh",
            "matched_print": {
                "print_id": row["print_id"],
                "set_code": row["set_code"],
                "set_name": row["set_name"],
                "collector_number": row["collector_number"],
                "language": row["language"],
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
