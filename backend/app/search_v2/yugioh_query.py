from __future__ import annotations

from sqlalchemy import text

from app.search_v2.normalization import normalize_language, normalize_search_text
from app.search_v2.yugioh_exact_collector import exact_yugioh_collector_search


SUPPORTED_DISPLAY_LANGUAGES = {"en", "es", "ja"}


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


def normal_yugioh_search(
    session,
    *,
    query: str,
    limit: int = 24,
    language: str | None = None,
) -> list[dict]:
    """Rank logical Cards while returning an exact physical print.

    ``prints.language`` is authoritative for the physical language. EN uses the
    canonical Card/Set names; ES/JA use the PrintLocalization attached to that
    exact print. The selected language therefore filters real physical prints,
    never an English row relabeled as another language.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("Yu-Gi-Oh Search V2 requires PostgreSQL")

    q = str(query or "").strip()
    if not q:
        return []
    display_language = _display_language(language)

    exact = exact_yugioh_collector_search(
        session,
        query=q,
        game="yugioh",
        limit=limit,
        language=display_language,
    )
    if exact is not None:
        return exact

    q_norm = normalize_search_text(q)
    q_code = q_norm.replace(" ", "-")
    q_raw = q.casefold()
    bounded_limit = max(1, min(int(limit or 24), 100))
    # Each signal is independently score-ordered before this cap. Four times the
    # requested page (with a small floor for suggestions) leaves ample reranking
    # headroom without hydrating hundreds of losing Cards on every fuzzy query.
    candidate_limit = max(60, bounded_limit * 4)

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

    # normalize_search_text is intentionally ASCII-oriented. For a pure CJK
    # query q_norm is empty; never turn that into LIKE '%' over every profile.
    # Japanese exact/contains matching is handled by localized_signal below,
    # which preserves q_raw Unicode and reads exact PrintLocalization rows.
    if q_norm:
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
    else:
        card_predicate = "FALSE"
        print_predicate = "FALSE"

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
          JOIN prints pp ON pp.id=psp.print_id
          JOIN games g ON g.id=psp.game_id
          WHERE g.slug='yugioh'
            AND (:display_language IS NULL OR lower(coalesce(pp.language,''))=ANY(string_to_array(:display_language, ',')))
            AND ({print_predicate})
          GROUP BY psp.card_id
          ORDER BY score DESC, psp.card_id ASC
          LIMIT :candidate_limit
        ),
        localized_signal AS MATERIALIZED (
          SELECT
            p.card_id,
            MAX(
              CASE WHEN lower(pl.card_name) = :q_raw THEN 6500.0 ELSE 0.0 END +
              CASE WHEN left(lower(pl.card_name), length(:q_raw)) = :q_raw THEN 3000.0 ELSE 0.0 END +
              CASE WHEN position(:q_raw in lower(pl.card_name)) > 0 THEN 1700.0 ELSE 0.0 END
            ) AS score
          FROM print_localizations pl
          JOIN prints p ON p.id=pl.print_id
          JOIN cards c ON c.id=p.card_id
          JOIN games g ON g.id=c.game_id
          WHERE g.slug='yugioh'
            AND pl.card_name IS NOT NULL
            AND lower(pl.language)=lower(coalesce(p.language,''))
            AND lower(coalesce(p.language,'')) IN ('es','ja')
            AND (:display_language IS NULL OR lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ',')))
            AND lower(pl.card_name) LIKE :localized_contains
          GROUP BY p.card_id
          ORDER BY score DESC, p.card_id ASC
          LIMIT :candidate_limit
        ),
        candidates AS MATERIALIZED (
          SELECT card_id, MAX(score) AS score
          FROM (
            SELECT * FROM card_signal
            UNION ALL
            SELECT * FROM print_signal
            UNION ALL
            SELECT * FROM localized_signal
          ) signals
          GROUP BY card_id
          ORDER BY score DESC, card_id ASC
          LIMIT :candidate_limit
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
          best.variant_count,
          candidates.score
        FROM candidates
        JOIN cards c ON c.id=candidates.card_id
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
              WHERE p2.card_id=p.card_id AND lower(coalesce(p2.language,'')) IN ('en','es','ja')
            ) AS available_languages,
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
          LEFT JOIN LATERAL (
            SELECT lower(pl.language) AS language, pl.card_name, pl.set_name
            FROM print_localizations pl
            WHERE pl.print_id=p.id
              AND lower(pl.language)=lower(coalesce(p.language,''))
            ORDER BY pl.id ASC
            LIMIT 1
          ) loc ON TRUE
          WHERE psp.card_id=candidates.card_id
            AND lower(coalesce(p.language,'')) IN ('en','es','ja')
            AND (:display_language IS NULL OR lower(coalesce(p.language,''))=ANY(string_to_array(:display_language, ',')))
          ORDER BY
            (loc.card_name IS NOT NULL AND lower(loc.card_name)=:q_raw) DESC,
            (loc.card_name IS NOT NULL AND position(:q_raw in lower(loc.card_name)) > 0) DESC,
            (psp.normalized_collector_number = :q_code) DESC,
            (psp.normalized_set_code = :q_code) DESC,
            (psp.search_text LIKE :contains) DESC,
            CASE lower(coalesce(p.language,'')) WHEN 'en' THEN 0 WHEN 'es' THEN 1 WHEN 'ja' THEN 2 ELSE 3 END,
            p.id ASC
          LIMIT 1
        ) best ON TRUE
        ORDER BY candidates.score DESC, display_name ASC, c.id ASC
        LIMIT :limit
        """
    )
    params = {
        "q_norm": q_norm,
        "q_code": q_code,
        "q_raw": q_raw,
        "display_language": display_language,
        "prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "localized_contains": f"%{q_raw}%",
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
            "score": round(float(row["score"] or 0), 4),
        }
        for row in rows
    ]
