from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_search_text


def _tokens(query: str) -> list[str]:
    return [token for token in normalize_search_text(query).split() if token][:6]


def normal_mtg_search(session, *, query: str, limit: int = 24) -> list[dict]:
    """Fast logical-Card MTG search without extra MTG-specific indexes.

    Name/alias discovery scans the much smaller Card projection (37k rows).
    Exact set/collector hints use the already-indexed Print projection columns,
    then only candidate Cards pay the LATERAL representative-Print lookup.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("MTG Search V2 requires PostgreSQL")

    raw_query = str(query or "").strip()
    if not raw_query:
        return []
    bounded_limit = max(1, min(int(limit or 24), 100))
    q_norm = normalize_search_text(raw_query)
    q_code = q_norm.replace(" ", "-")
    tokens = _tokens(raw_query)
    params: dict[str, object] = {
        "q_norm": q_norm,
        "q_code": q_code,
        "prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "limit": bounded_limit,
        "candidate_limit": max(80, bounded_limit * 8),
    }

    token_name_bonus = []
    token_print_checks = []
    for index, token in enumerate(tokens):
        params[f"token_{index}"] = token
        params[f"token_like_{index}"] = f"%{token}%"
        params[f"token_code_{index}"] = token.replace(" ", "-")
        token_name_bonus.append(
            f"CASE WHEN csp.normalized_name LIKE :token_like_{index} THEN 35.0 "
            f"WHEN csp.search_text LIKE :token_like_{index} THEN 12.0 ELSE 0.0 END"
        )
        token_print_checks.append(
            f"psp.normalized_set_code=:token_code_{index} OR "
            f"psp.normalized_collector_number=:token_code_{index}"
        )

    token_bonus_sql = " + ".join(token_name_bonus) or "0.0"
    print_token_predicate = " OR ".join(token_print_checks) or "FALSE"

    sql = text(
        f"""
        WITH card_candidates AS MATERIALIZED (
          SELECT csp.card_id,
                 (CASE WHEN csp.normalized_name=:q_norm THEN 10000.0 ELSE 0.0 END +
                  CASE WHEN csp.normalized_name LIKE :prefix THEN 4200.0 ELSE 0.0 END +
                  CASE WHEN csp.normalized_name LIKE :contains THEN 1800.0 ELSE 0.0 END +
                  CASE WHEN csp.search_text LIKE :contains THEN 550.0 ELSE 0.0 END +
                  {token_bonus_sql}) AS score
          FROM card_search_profiles csp
          JOIN games g ON g.id=csp.game_id
          WHERE g.slug='mtg'
            AND (
              csp.normalized_name=:q_norm OR
              csp.normalized_name LIKE :prefix OR
              csp.normalized_name LIKE :contains OR
              csp.search_text LIKE :contains
            )
          ORDER BY score DESC,csp.card_id ASC
          LIMIT :candidate_limit
        ),
        print_candidates AS MATERIALIZED (
          SELECT psp.card_id,
                 MAX(
                   CASE WHEN psp.normalized_collector_number=:q_code THEN 8500.0 ELSE 0.0 END +
                   CASE WHEN psp.normalized_set_code=:q_code THEN 6500.0 ELSE 0.0 END +
                   CASE WHEN ({print_token_predicate}) THEN 900.0 ELSE 0.0 END
                 ) AS score
          FROM print_search_profiles psp
          JOIN games g ON g.id=psp.game_id
          WHERE g.slug='mtg'
            AND (
              psp.normalized_collector_number=:q_code OR
              psp.normalized_set_code=:q_code OR
              ({print_token_predicate})
            )
          GROUP BY psp.card_id
          ORDER BY score DESC,psp.card_id ASC
          LIMIT :candidate_limit
        ),
        candidates AS MATERIALIZED (
          SELECT card_id,MAX(score) AS score
          FROM (
            SELECT card_id,score FROM card_candidates
            UNION ALL
            SELECT card_id,score FROM print_candidates
          ) signals
          GROUP BY card_id
          ORDER BY score DESC,card_id ASC
          LIMIT :candidate_limit
        )
        SELECT
          c.id AS card_id,c.card_key,c.name,
          csp.attributes_json,
          chosen.print_id,chosen.set_code,chosen.set_name,chosen.collector_number,
          chosen.language,chosen.rarity,chosen.exact_variant,chosen.variant_family,
          chosen.primary_image_url,chosen.variant_count,candidates.score
        FROM candidates
        JOIN cards c ON c.id=candidates.card_id
        JOIN card_search_profiles csp ON csp.card_id=c.id
        JOIN LATERAL (
          SELECT
            p.id AS print_id,s.code AS set_code,s.name AS set_name,p.collector_number,
            p.language,p.rarity,psp.exact_variant,psp.variant_family,
            (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id
             ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1) AS primary_image_url,
            COUNT(*) OVER () AS variant_count
          FROM print_search_profiles psp
          JOIN prints p ON p.id=psp.print_id
          JOIN sets s ON s.id=p.set_id
          WHERE psp.card_id=candidates.card_id
          ORDER BY
            (psp.normalized_collector_number=:q_code) DESC,
            (psp.normalized_set_code=:q_code) DESC,
            (psp.exact_variant='nonfoil') DESC,
            p.id ASC
          LIMIT 1
        ) chosen ON TRUE
        ORDER BY candidates.score DESC,c.name ASC,c.id ASC
        LIMIT :limit
        """
    )
    rows = session.execute(sql, params).mappings().all()
    return [
        {
            "type":"card",
            "card_id":row["card_id"],
            "card_key":row["card_key"],
            "name":row["name"],
            "game":"mtg",
            "matched_print":{
                "print_id":row["print_id"],
                "set_code":row["set_code"],
                "set_name":row["set_name"],
                "collector_number":row["collector_number"],
                "language":row["language"],
                "rarity":row["rarity"],
                "exact_variant":row["exact_variant"],
                "variant_family":row["variant_family"],
                "primary_image_url":row["primary_image_url"],
            },
            "variant_count":int(row["variant_count"] or 0),
            "attributes":row["attributes_json"] or {},
            "score":round(float(row["score"] or 0),4),
        }
        for row in rows
    ]
