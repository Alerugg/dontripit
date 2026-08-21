from __future__ import annotations

from sqlalchemy import text


def optimized_short_query_search_rows(
    session,
    *,
    q_norm: str,
    game: str,
    result_type: str | None,
    limit: int,
    offset: int,
    is_set_intent_query: int,
):
    """Search legacy short queries without enriching the entire catalog first.

    The historical query aggregated every row in ``prints`` and evaluated image
    lookups for the full ``search_documents`` view before applying the actual
    query predicate. That makes the 1-4 character path scale with catalog size
    instead of the number of matches.

    This version keeps the public ranking contract intact but first materializes
    only cheap matching candidates. Print counts and image lookups are then
    evaluated for that bounded candidate set. ``prints(card_id)`` and
    ``print_images(print_id)`` are indexed by the core schema.
    """
    from app.routes.search import _is_simple_name_query

    is_postgres = session.bind.dialect.name == "postgresql"
    space_pos_fn = "strpos" if is_postgres else "instr"
    materialized = "MATERIALIZED " if is_postgres else ""
    params = {
        "q_norm": q_norm,
        "q_len": len(q_norm),
        "title_prefix": f"{q_norm}%",
        "contains": f"%{q_norm}%",
        "game": game,
        "type": result_type,
        "limit": limit,
        "offset": offset,
        "is_set_intent_query": is_set_intent_query,
        "enable_contains": 1 if len(q_norm) >= 3 else 0,
        "is_onepiece_simple_name_query": 1 if (game == "onepiece" and _is_simple_name_query(q_norm)) else 0,
        "name_token_contains": f"% {q_norm}%",
        "name_token_hyphen_contains": f"%-{q_norm}%",
    }
    sql = text(
        f"""
        WITH candidate_base AS {materialized}(
          SELECT
            sd.doc_type AS type,
            sd.object_id AS id,
            COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END) AS card_id,
            sd.title,
            COALESCE(sd.subtitle, '') AS subtitle,
            g.slug AS game,
            s.code AS set_code,
            s.name AS set_name,
            p.collector_number,
            p.language,
            p.variant,
            lower(sd.title) AS title_l,
            lower(COALESCE(p.collector_number, '')) AS collector_l,
            lower(COALESCE(s.code, '')) AS set_code_l
          FROM search_documents sd
          JOIN games g ON g.id = sd.game_id
          LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
          LEFT JOIN sets s ON (
            (sd.doc_type = 'print' AND s.id = p.set_id)
            OR (sd.doc_type = 'set' AND s.id = sd.object_id)
          )
          WHERE (:game = '' OR g.slug = :game)
            AND (:type IS NULL OR sd.doc_type = :type)
            AND (
              lower(sd.title) LIKE :title_prefix
              OR lower(COALESCE(p.collector_number, '')) LIKE :title_prefix
              OR lower(COALESCE(s.code, '')) LIKE :title_prefix
              OR (:enable_contains = 1 AND lower(sd.title) LIKE :contains)
              OR (:enable_contains = 1 AND lower(COALESCE(p.collector_number, '')) LIKE :contains)
              OR (:enable_contains = 1 AND lower(COALESCE(s.code, '')) LIKE :contains)
            )
        ),
        candidate_card_ids AS (
          SELECT DISTINCT card_id
          FROM candidate_base
          WHERE card_id IS NOT NULL
        ),
        card_print_counts AS (
          SELECT p.card_id, CAST(COUNT(*) AS FLOAT) AS print_count
          FROM prints p
          JOIN candidate_card_ids candidate ON candidate.card_id = p.card_id
          GROUP BY p.card_id
        ),
        base AS (
          SELECT
            cb.*,
            COALESCE(cpc.print_count, 0.0) AS card_print_count,
            COALESCE(cpc.print_count, 0.0) AS variant_count,
            COALESCE(
              (
                SELECT pi.url
                FROM print_images pi
                WHERE cb.type = 'print'
                  AND pi.print_id = cb.id
                ORDER BY
                  CASE
                    WHEN lower(pi.url) LIKE '%en.onepiece-cardgame.com%' THEN 0
                    WHEN lower(pi.url) LIKE '%example.cdn.onepiece%' THEN 2
                    ELSE 1
                  END,
                  CASE WHEN pi.is_primary IS TRUE THEN 0 ELSE 1 END,
                  pi.id
                LIMIT 1
              ),
              (
                SELECT pi2.url
                FROM print_images pi2
                JOIN prints p2 ON p2.id = pi2.print_id
                WHERE p2.card_id = cb.card_id
                ORDER BY
                  CASE
                    WHEN lower(pi2.url) LIKE '%en.onepiece-cardgame.com%' THEN 0
                    WHEN lower(pi2.url) LIKE '%example.cdn.onepiece%' THEN 2
                    ELSE 1
                  END,
                  pi2.is_primary DESC,
                  pi2.id ASC
                LIMIT 1
              ),
              CASE
                WHEN cb.type = 'set' AND cb.game = 'riftbound' THEN CASE lower(COALESCE(cb.set_code, ''))
                  WHEN 'rb1' THEN '/images/riftbound/rb1-placeholder.svg'
                  WHEN 'rb2' THEN '/images/riftbound/rb2-placeholder.svg'
                  WHEN 'ogn' THEN '/images/riftbound/ogn-placeholder.svg'
                  ELSE '/images/riftbound/rb1-placeholder.svg'
                END
                ELSE NULL
              END
            ) AS primary_image_url
          FROM candidate_base cb
          LEFT JOIN card_print_counts cpc ON cpc.card_id = cb.card_id
        ),
        intent AS (
          SELECT CASE
            WHEN :is_set_intent_query = 1
              AND EXISTS (SELECT 1 FROM base WHERE set_code_l LIKE :title_prefix)
              AND NOT EXISTS (
                SELECT 1
                FROM base
                WHERE type = 'card'
                  AND title_l LIKE :title_prefix
              )
            THEN 1
            ELSE 0
          END AS has_set_prefix_match
        ),
        prefix_next_chars AS (
          SELECT
            substr(title_l, :q_len + 1, 1) AS next_char,
            COUNT(*) AS next_char_count
          FROM base
          WHERE type = 'card'
            AND :q_len = 3
            AND :game <> ''
            AND :q_len <= 3
            AND title_l LIKE :title_prefix
            AND length(title_l) > :q_len
          GROUP BY substr(title_l, :q_len + 1, 1)
        ),
        ranked AS (
          SELECT
            *,
            CASE
              WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'set' AND set_code_l = :q_norm THEN -1
              WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'set' AND set_code_l LIKE :title_prefix THEN 0
              WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'print' AND set_code_l LIKE :title_prefix THEN 1
              WHEN :is_onepiece_simple_name_query = 1
                AND type = 'card'
                AND (
                  title_l = :q_norm
                  OR title_l LIKE :name_token_contains
                  OR title_l LIKE :name_token_hyphen_contains
                )
              THEN 0
              WHEN title_l = :q_norm THEN 0
              WHEN title_l LIKE :q_norm || '%' AND (length(title_l) = length(:q_norm) OR substr(title_l, length(:q_norm) + 1, 1) IN (' ', ',', '-', ':', ';', '.', '/', '(', ')')) THEN 1
              WHEN title_l LIKE :title_prefix THEN 2
              WHEN collector_l = :q_norm THEN 3
              WHEN collector_l LIKE :title_prefix THEN 4
              WHEN set_code_l = :q_norm THEN 5
              WHEN set_code_l LIKE :title_prefix THEN 6
              WHEN :enable_contains = 1 AND title_l LIKE :contains THEN 7
              WHEN :enable_contains = 1 AND (collector_l LIKE :contains OR set_code_l LIKE :contains) THEN 8
              ELSE 9
            END AS rank_bucket,
            CASE
              WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'set' THEN 0
              WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'print' THEN 1
              WHEN type = 'card' THEN 0
              WHEN type = 'print' THEN 1
              ELSE 2
            END AS type_rank,
            ROW_NUMBER() OVER (
              PARTITION BY title_l
              ORDER BY
                CASE
                  WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'set' THEN 0
                  WHEN (SELECT has_set_prefix_match FROM intent) = 1 AND type = 'print' THEN 1
                  WHEN type = 'card' THEN 0
                  WHEN type = 'print' THEN 1
                  ELSE 2
                END,
                CASE
                  WHEN type = 'print' AND game = 'onepiece' AND lower(COALESCE(primary_image_url, '')) LIKE '%en.onepiece-cardgame.com%' THEN 0
                  WHEN type = 'print' AND game = 'onepiece' AND (
                    lower(COALESCE(primary_image_url, '')) LIKE '%placehold.co%'
                    OR lower(COALESCE(primary_image_url, '')) LIKE '%example.cdn.onepiece%'
                  ) THEN 2
                  ELSE 1
                END,
                card_print_count DESC,
                id ASC
            ) AS title_dedupe_rank,
            ROW_NUMBER() OVER (
              PARTITION BY set_code_l
              ORDER BY
                CASE
                  WHEN set_code_l = :q_norm THEN 0
                  WHEN set_code_l LIKE :title_prefix THEN 1
                  WHEN collector_l = :q_norm THEN 2
                  WHEN collector_l LIKE :title_prefix THEN 3
                  WHEN title_l LIKE :title_prefix THEN 4
                  ELSE 5
                END,
                CASE WHEN type = 'set' THEN 0 WHEN type = 'card' THEN 1 ELSE 2 END,
                id ASC
            ) AS set_code_rank,
            ROW_NUMBER() OVER (
              PARTITION BY
                CASE
                  WHEN type = 'set' AND set_code_l LIKE :title_prefix THEN :q_norm
                  ELSE set_code_l
                END
              ORDER BY
                CASE WHEN type = 'set' THEN 0 ELSE 1 END,
                CASE WHEN set_code_l = :q_norm THEN 0 ELSE 1 END,
                length(set_code_l) ASC,
                id ASC
            ) AS set_prefix_group_rank,
            CASE
              WHEN :game = '' AND type = 'card' AND game = 'pokemon' AND :is_set_intent_query = 0 THEN 0
              ELSE 1
            END AS cross_game_name_rank,
            CASE
              WHEN title_l LIKE :title_prefix THEN 0
              WHEN :enable_contains = 1 AND title_l LIKE :contains THEN 1
              ELSE 2
            END AS title_match_rank,
            CASE
              WHEN :q_len > 3
                AND :game <> ''
                AND title_l LIKE :title_prefix
                AND length(title_l) > :q_len
              THEN COALESCE(
                (
                  SELECT -pnc.next_char_count
                  FROM prefix_next_chars pnc
                  WHERE pnc.next_char = substr(title_l, :q_len + 1, 1)
                  LIMIT 1
                ),
                0
              )
              ELSE 0
            END AS prefix_continuation_rank,
            CASE
              WHEN title_l LIKE :title_prefix THEN
                CASE
                  WHEN length(title_l) = :q_len THEN 0
                  WHEN substr(title_l, :q_len + 1, 1) IN (' ', ',', '-', ':', ';', '.', '/', '(', ')') THEN 0
                  WHEN {space_pos_fn}(title_l, ' ') = 0 THEN 0
                  WHEN {space_pos_fn}(title_l, ' ') > 0 AND :q_len >= ({space_pos_fn}(title_l, ' ') - 1) THEN 0
                  ELSE 1
                END
              ELSE 2
            END AS prefix_word_rank,
            CASE
              WHEN game = 'onepiece' AND lower(COALESCE(primary_image_url, '')) LIKE '%en.onepiece-cardgame.com%' THEN 0
              WHEN game = 'onepiece' AND (
                lower(COALESCE(primary_image_url, '')) LIKE '%placehold.co%'
                OR lower(COALESCE(primary_image_url, '')) LIKE '%example.cdn.onepiece%'
              ) THEN 2
              ELSE 1
            END AS image_quality_rank,
            CASE
              WHEN :is_onepiece_simple_name_query = 1
                AND game = 'onepiece'
                AND type = 'card'
                AND title_l LIKE :q_norm || ' %'
                AND (title_l LIKE '% & %' OR title_l LIKE '%/%')
              THEN 1
              ELSE 0
            END AS onepiece_compound_penalty_rank
          FROM base
        )
        SELECT type, id, card_id, title, subtitle, game, set_code, set_name, collector_number, language, variant, variant_count, primary_image_url
        FROM ranked
        WHERE rank_bucket < 9
          AND title_dedupe_rank <= 2
          AND (
            set_code_l = ''
            OR set_code_rank <= CASE WHEN set_code_l LIKE :title_prefix THEN 3 ELSE 6 END
          )
          AND (
            (SELECT has_set_prefix_match FROM intent) = 0
            OR type <> 'set'
            OR set_prefix_group_rank = 1
          )
        ORDER BY
          rank_bucket ASC,
          cross_game_name_rank ASC,
          title_match_rank ASC,
          prefix_continuation_rank ASC,
          prefix_word_rank ASC,
          onepiece_compound_penalty_rank ASC,
          type_rank ASC,
          image_quality_rank ASC,
          card_print_count DESC,
          CASE WHEN type = 'card' THEN id ELSE 0 END ASC,
          length(title) ASC,
          CASE WHEN title_l LIKE :q_norm || '%' AND (length(title_l) = length(:q_norm) OR substr(title_l, length(:q_norm) + 1, 1) IN (' ', ',', '-', ':', ';', '.', '/', '(', ')')) THEN 0 ELSE 1 END ASC,
          title ASC,
          id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    return session.execute(sql, params).mappings().all()


def install_legacy_search_hot_path() -> None:
    """Install the optimized helper without changing the public Flask route."""
    from app.routes import search as search_routes

    search_routes._short_query_search_rows = optimized_short_query_search_rows
