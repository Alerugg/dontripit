from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
import logging
import re

from app import db

search_bp = Blueprint("search", __name__)
logger = logging.getLogger(__name__)

_PUBLIC_SEARCH_KEYS = (
    "type",
    "id",
    "title",
    "subtitle",
    "game",
    "set_code",
    "collector_number",
    "variant",
    "primary_image_url",
)


def _to_public_search_row(row: dict) -> dict:
    return {key: row.get(key) for key in _PUBLIC_SEARCH_KEYS}


def _normalize_query(raw_query: str) -> str:
    return " ".join(raw_query.lower().split())


def _is_exact_code_query(raw_query: str) -> bool:
    normalized = "".join(raw_query.strip().split())
    if len(normalized) < 3 or len(normalized) > 16:
        return False

    if re.fullmatch(r"[A-Za-z0-9]{2,8}[-_][A-Za-z0-9]{1,8}", normalized) is None:
        return False

    return any(char.isalpha() for char in normalized) and any(char.isdigit() for char in normalized)


def _looks_like_code_query(raw_query: str) -> bool:
    stripped = raw_query.strip()
    normalized = "".join(stripped.split())
    if len(normalized) < 2 or len(normalized) > 16:
        return False

    if _is_exact_code_query(raw_query):
        return True

    if re.fullmatch(r"[a-zA-Z0-9]{2,8}[-_][a-zA-Z0-9]{1,8}", normalized):
        return True

    has_alpha = any(char.isalpha() for char in normalized)
    has_digit = any(char.isdigit() for char in normalized)
    if has_alpha and has_digit:
        return True

    if normalized.isalpha() and normalized.upper() == normalized and 2 <= len(normalized) <= 5:
        return True

    tokens = [token for token in re.split(r"[\s/]+", stripped) if token]
    if len(tokens) >= 2:
        structured_tokens = sum(
            1
            for token in tokens
            if re.fullmatch(r"[A-Za-z]{1,5}[0-9]{1,4}", token)
            or re.fullmatch(r"[A-Za-z0-9]{2,8}[-_][A-Za-z0-9]{1,8}", token)
        )
        if structured_tokens >= (len(tokens) / 2):
            return True

    return False


def _search_mode(query_length: int, is_text_query: int) -> str:
    if query_length <= 1:
        return "exact"
    if query_length <= 3 and is_text_query == 1:
        return "prefix"
    return "broad"


def _looks_like_set_prefix_query(raw_query: str) -> bool:
    normalized = "".join(raw_query.strip().split()).lower()
    if len(normalized) < 3 or len(normalized) > 10:
        return False

    if any(char.isdigit() for char in normalized):
        return True

    if "-" in normalized or "_" in normalized:
        return True

    if normalized.isalpha() is False:
        return False

    # Keep short alpha-only prefixes (e.g. LOB) eligible for set intent, but
    # avoid broad activation on longer natural-language fragments like
    # "cha"/"char" that should stay in name-intent mode.
    return 3 <= len(normalized) <= 3


def _short_query_search_rows(
    session,
    *,
    q_norm: str,
    game: str,
    result_type: str | None,
    limit: int,
    offset: int,
    is_set_intent_query: int,
):
    space_pos_fn = "strpos" if session.bind.dialect.name == "postgresql" else "instr"
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
    }
    sql = text(
        f"""
        WITH card_print_counts AS (
          SELECT p.card_id, CAST(COUNT(*) AS FLOAT) AS print_count
          FROM prints p
          GROUP BY p.card_id
        ),
        base AS (
          SELECT
            sd.doc_type AS type,
            sd.object_id AS id,
            sd.title,
            COALESCE(sd.subtitle, '') AS subtitle,
            g.slug AS game,
            s.code AS set_code,
            p.collector_number,
            p.variant,
            COALESCE(cpc.print_count, 0.0) AS card_print_count,
            COALESCE(
              (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1),
              (
                SELECT pi2.url
                FROM print_images pi2
                JOIN prints p2 ON p2.id = pi2.print_id
                WHERE p2.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
                ORDER BY pi2.is_primary DESC, pi2.id ASC
                LIMIT 1
              ),
              CASE
                WHEN sd.doc_type = 'set' AND g.slug = 'riftbound' THEN CASE lower(COALESCE(s.code, ''))
                  WHEN 'rb1' THEN '/images/riftbound/rb1-placeholder.svg'
                  WHEN 'rb2' THEN '/images/riftbound/rb2-placeholder.svg'
                  WHEN 'ogn' THEN '/images/riftbound/ogn-placeholder.svg'
                  ELSE '/images/riftbound/rb1-placeholder.svg'
                END
                ELSE NULL
              END
            ) AS primary_image_url,
            lower(sd.title) AS title_l,
            lower(COALESCE(p.collector_number, '')) AS collector_l,
            lower(COALESCE(s.code, '')) AS set_code_l
          FROM search_documents sd
          JOIN games g ON g.id = sd.game_id
          LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
          LEFT JOIN card_print_counts cpc ON cpc.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
          LEFT JOIN sets s ON (
            (sd.doc_type = 'print' AND s.id = p.set_id)
            OR (sd.doc_type = 'set' AND s.id = sd.object_id)
          )
          WHERE (:game = '' OR g.slug = :game)
            AND (:type IS NULL OR sd.doc_type = :type)
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
            ) AS set_code_rank
            ,ROW_NUMBER() OVER (
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
            END AS prefix_word_rank
          FROM base
          WHERE (
            title_l LIKE :title_prefix
            OR collector_l LIKE :title_prefix
            OR set_code_l LIKE :title_prefix
            OR (:enable_contains = 1 AND title_l LIKE :contains)
            OR (:enable_contains = 1 AND collector_l LIKE :contains)
            OR (:enable_contains = 1 AND set_code_l LIKE :contains)
          )
        )
        SELECT type, id, title, subtitle, game, set_code, collector_number, variant, primary_image_url
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
          type_rank ASC,
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


def _fallback_search_rows(session, *, like: str, game: str, result_type: str | None, limit: int, offset: int):
    fallback = text(
        """
        SELECT * FROM (
          SELECT 'card' AS type, c.id, c.name AS title, '' AS subtitle, g.slug AS game,
                 NULL AS set_code, NULL AS collector_number, NULL AS variant,
                 (
                   SELECT pi.url
                   FROM print_images pi
                   JOIN prints p ON p.id = pi.print_id
                   WHERE p.card_id = c.id
                   ORDER BY pi.is_primary DESC, pi.id ASC
                   LIMIT 1
                 ) AS primary_image_url
          FROM cards c JOIN games g ON g.id = c.game_id WHERE lower(c.name) LIKE :like
          UNION ALL
          SELECT 'set', s.id, s.name, s.code, g.slug,
                 NULL, NULL, NULL,
                 CASE
                   WHEN g.slug = 'riftbound' THEN CASE lower(s.code)
                     WHEN 'rb1' THEN '/images/riftbound/rb1-placeholder.svg'
                     WHEN 'rb2' THEN '/images/riftbound/rb2-placeholder.svg'
                     WHEN 'ogn' THEN '/images/riftbound/ogn-placeholder.svg'
                     ELSE '/images/riftbound/rb1-placeholder.svg'
                   END
                   ELSE NULL
                 END
          FROM sets s JOIN games g ON g.id = s.game_id WHERE lower(s.name) LIKE :like OR lower(s.code) LIKE :like
          UNION ALL
          SELECT 'print', p.id, c.name, (s.code || ' #' || p.collector_number), g.slug,
                 s.code, p.collector_number, p.variant,
                 (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1)
          FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=s.game_id
          WHERE lower(c.name) LIKE :like OR lower(p.collector_number) LIKE :like OR lower(s.code) LIKE :like
        ) t WHERE (:game = '' OR t.game = :game)
          AND (:type IS NULL OR t.type = :type)
        LIMIT :limit OFFSET :offset
        """
    )
    return session.execute(
        fallback,
        {"like": like, "game": game, "type": result_type, "limit": limit, "offset": offset},
    ).mappings().all()


def _fallback_suggest_rows(session, *, q: str, game: str, limit: int):
    term = _normalize_query(q)
    short_query = len(term) <= 1
    is_code_like_query = 1 if _looks_like_code_query(q) else 0
    is_exact_code_query = 1 if _is_exact_code_query(q) else 0
    is_text_query = 1 if is_code_like_query == 0 else 0
    code_prefix = term.split("-", 1)[0].split("_", 1)[0]
    params = {
        "q": term,
        "code_prefix": code_prefix,
        "prefix": f"{term}%",
        "contains": f"{term}%" if short_query else f"%{term}%",
        "token_prefix": f"% {term}%",
        "is_code_like_query": is_code_like_query,
        "is_exact_code_query": is_exact_code_query,
        "is_text_query": is_text_query,
        "game": game,
        "limit": limit,
    }
    sql = text(
        """
        WITH card_print_counts AS (
          SELECT p.card_id, CAST(COUNT(*) AS FLOAT) AS print_count
          FROM prints p
          GROUP BY p.card_id
        ),
        scored AS (
          SELECT
            sd.doc_type AS type,
            sd.object_id AS id,
            sd.title,
            COALESCE(sd.subtitle, '') AS subtitle,
            g.slug AS game,
            s.code AS set_code,
            p.collector_number,
            p.variant,
            COALESCE(
              (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1),
              (SELECT pi2.url FROM print_images pi2 JOIN prints p2 ON p2.id = pi2.print_id WHERE p2.card_id = p.card_id AND pi2.is_primary IS TRUE ORDER BY pi2.id LIMIT 1),
              CASE
                WHEN sd.doc_type = 'set' AND g.slug = 'riftbound' THEN CASE lower(COALESCE(s.code, ''))
                  WHEN 'rb1' THEN '/images/riftbound/rb1-placeholder.svg'
                  WHEN 'rb2' THEN '/images/riftbound/rb2-placeholder.svg'
                  WHEN 'ogn' THEN '/images/riftbound/ogn-placeholder.svg'
                  ELSE '/images/riftbound/rb1-placeholder.svg'
                END
                ELSE NULL
              END
            ) AS primary_image_url,
            (
              CASE WHEN :is_text_query = 1 AND sd.doc_type = 'card' AND lower(sd.title) = :q THEN 2500.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND sd.doc_type = 'print' AND lower(sd.title) = :q THEN 1700.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND lower(sd.title) LIKE :prefix THEN 1700.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND (' ' || lower(sd.title)) LIKE :token_prefix THEN 820.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND lower(sd.title) LIKE :contains THEN 420.0 ELSE 0.0 END +
              CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(COALESCE(p.collector_number, '')) = :q THEN 4200.0 ELSE 0.0 END +
              CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'set' AND lower(COALESCE(s.code, '')) = :q THEN 3200.0 ELSE 0.0 END +
              CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(COALESCE(s.code, '')) = :code_prefix THEN 700.0 ELSE 0.0 END +
              CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'card' THEN -220.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(p.collector_number, '')) LIKE :prefix THEN 1600.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(s.code, '')) LIKE :prefix THEN 1450.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(p.collector_number, '')) LIKE :contains THEN 360.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(s.code, '')) LIKE :contains THEN 300.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(s.code, '')) LIKE :prefix THEN (120.0 / (1 + abs(length(lower(COALESCE(s.code, ''))) - length(:q)))) ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND lower(COALESCE(p.collector_number, '')) LIKE :prefix THEN (90.0 / (1 + abs(length(lower(COALESCE(p.collector_number, ''))) - length(:q)))) ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND sd.doc_type = 'card' THEN 260.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND sd.doc_type = 'print' THEN -140.0 ELSE 0.0 END +
              CASE WHEN :is_text_query = 1 AND sd.doc_type = 'set' THEN -180.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND sd.doc_type = 'print' THEN 180.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND sd.doc_type = 'set' THEN 120.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 AND sd.doc_type = 'card' THEN -90.0 ELSE 0.0 END +
              CASE WHEN :is_code_like_query = 1 THEN -35.0 * (
                ROW_NUMBER() OVER (
                  PARTITION BY lower(COALESCE(s.code, ''))
                  ORDER BY
                    CASE WHEN lower(COALESCE(p.collector_number, '')) = :q THEN 0 ELSE 1 END,
                    length(COALESCE(p.collector_number, '')) ASC,
                    lower(COALESCE(p.collector_number, '')) ASC,
                    sd.object_id ASC
                ) - 1
              ) ELSE 0.0 END +
              (CASE WHEN COALESCE(cpc.print_count, 0) > 50 THEN 50 ELSE COALESCE(cpc.print_count, 0) END) * 5.0 +
              CASE WHEN sd.doc_type = 'card' THEN (220.0 / (sd.object_id + 20.0)) ELSE 0.0 END
            ) AS score,
            ROW_NUMBER() OVER (
              PARTITION BY lower(sd.title)
              ORDER BY
                (
                  CASE WHEN lower(sd.title) = :q THEN 1 ELSE 0 END
                ) DESC,
                (
                  CASE WHEN lower(COALESCE(p.collector_number, '')) = :q THEN 1 ELSE 0 END
                ) DESC,
                (
                  CASE WHEN sd.doc_type = 'card' THEN 1 ELSE 0 END
                ) DESC,
                sd.object_id ASC
            ) AS title_rank
          FROM search_documents sd
          JOIN games g ON g.id = sd.game_id
          LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
          LEFT JOIN card_print_counts cpc ON cpc.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
          LEFT JOIN sets s ON (
            (sd.doc_type = 'print' AND s.id = p.set_id)
            OR (sd.doc_type = 'set' AND s.id = sd.object_id)
          )
          WHERE (
            lower(sd.title) LIKE :contains
            OR lower(COALESCE(p.collector_number, '')) LIKE :contains
            OR lower(COALESCE(s.code, '')) LIKE :contains
          )
          AND (:game = '' OR g.slug = :game)
        )
        SELECT *
        FROM scored
        WHERE title_rank <= 2
        ORDER BY score DESC, (CASE WHEN type = 'card' THEN 0 WHEN type = 'print' THEN 1 ELSE 2 END), game ASC, title ASC, id ASC
        LIMIT :limit
        """
    )
    return session.execute(sql, params).mappings().all()


@search_bp.get("/api/search")
@search_bp.get("/api/v1/search")
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify({"error": "q is required (min 1 char)"}), 400

    game = request.args.get("game", "").strip()
    result_type = (request.args.get("type") or "").strip() or None
    query_length = len(_normalize_query(q))
    short_query_mode = 1 if query_length <= 4 else 0
    default_limit = 8 if query_length == 1 else 14 if query_length == 2 else 24
    max_limit = 12 if query_length == 1 else 24 if query_length == 2 else 100
    limit = min(max(request.args.get("limit", default=default_limit, type=int) or default_limit, 1), max_limit)
    offset = max(request.args.get("offset", default=0, type=int) or 0, 0)

    q_normalized = _normalize_query(q)
    is_exact_code_query = 1 if _is_exact_code_query(q) else 0
    is_code_like_query = 1 if _looks_like_code_query(q) else 0
    is_text_query = 1 if is_code_like_query == 0 else 0
    search_mode = _search_mode(query_length, is_text_query)
    is_prefix_mode = 1 if search_mode == "prefix" else 0
    is_exact_mode = 1 if search_mode == "exact" else 0
    code_prefix = q_normalized.split("-", 1)[0].split("_", 1)[0]
    is_set_intent_query = 1 if _looks_like_set_prefix_query(q) else 0

    with db.SessionLocal() as session:
        try:
            if short_query_mode == 1:
                rows = _short_query_search_rows(
                    session,
                    q_norm=q_normalized,
                    game=game,
                    result_type=result_type,
                    limit=limit,
                    offset=offset,
                    is_set_intent_query=is_set_intent_query,
                )
            elif session.bind.dialect.name == "postgresql":
                sql = text(
                    """
                    WITH query AS (SELECT plainto_tsquery('simple', :q) AS term)
                    SELECT sd.doc_type AS type, sd.object_id AS id, sd.title, sd.subtitle,
                           g.slug AS game,
                           (
                               CASE WHEN :is_text_query = 1 AND sd.doc_type = 'card' AND lower(sd.title) = :q_norm THEN 1200.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND sd.doc_type = 'print' AND lower(sd.title) = :q_norm THEN 850.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND lower(sd.title) LIKE :q_norm || '%' THEN 360.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND (' ' || lower(sd.title)) LIKE '% ' || :q_norm || '%' THEN 210.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND lower(sd.title) LIKE '%' || :q_norm || '%' THEN 140.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(coalesce(p.collector_number, '')) = :q_norm THEN 2600.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'set' AND lower(coalesce(s.code, '')) = :q_norm THEN 1700.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(coalesce(s.code, '')) = :code_prefix THEN 420.0 ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND lower(coalesce(p.collector_number, '')) LIKE :q_norm || '%' THEN 920.0 ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND lower(coalesce(s.code, '')) LIKE :q_norm || '%' THEN 760.0 ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND sd.doc_type = 'card' THEN -120.0 ELSE 0.0 END +
                               ts_rank(
                                   to_tsvector(
                                       'simple',
                                       coalesce(sd.title, '') || ' ' || coalesce(sd.subtitle, '') || ' ' || coalesce(sd.tsv, '')
                                   ),
                                   query.term
                               )
                           ) AS score,
                           s.code AS set_code, p.collector_number, p.variant,
                           COALESCE(
                               (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary = true ORDER BY pi.id LIMIT 1),
                               (
                                   SELECT pi2.url
                                   FROM print_images pi2
                                   JOIN prints p2 ON p2.id = pi2.print_id
                                   WHERE p2.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
                                   ORDER BY pi2.is_primary DESC, pi2.id ASC
                                   LIMIT 1
                               )
                           ) AS primary_image_url
                    FROM search_documents sd
                    CROSS JOIN query
                    JOIN games g ON g.id = sd.game_id
                    LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
                    LEFT JOIN sets s ON p.set_id = s.id
                    WHERE to_tsvector(
                            'simple',
                            coalesce(sd.title, '') || ' ' || coalesce(sd.subtitle, '') || ' ' || coalesce(sd.tsv, '')
                          ) @@ query.term
                      AND (:game = '' OR g.slug = :game)
                      AND (:type IS NULL OR sd.doc_type = :type)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                )
                rows = session.execute(
                    sql,
                    {
                        "q": q,
                        "q_norm": q_normalized,
                        "is_code_like_query": is_code_like_query,
                        "is_exact_code_query": is_exact_code_query,
                        "is_text_query": is_text_query,
                        "is_short_query": short_query_mode,
                        "is_prefix_mode": is_prefix_mode,
                        "is_exact_mode": is_exact_mode,
                        "code_prefix": code_prefix,
                        "game": game,
                        "type": result_type,
                        "limit": limit,
                        "offset": offset,
                    },
                ).mappings().all()
            else:
                like = f"{q.lower()}%" if query_length <= 2 else f"%{q.lower()}%"
                sql = text(
                    """
                    SELECT sd.doc_type AS type, sd.object_id AS id, sd.title, coalesce(sd.subtitle, '') AS subtitle,
                           g.slug AS game,
                           (
                               CASE WHEN :is_text_query = 1 AND sd.doc_type = 'card' AND lower(sd.title) = :q_norm THEN 1200.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND sd.doc_type = 'print' AND lower(sd.title) = :q_norm THEN 850.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND lower(sd.title) LIKE :q_norm || '%' THEN (CASE WHEN :is_prefix_mode = 1 THEN 3200.0 WHEN :is_short_query = 1 THEN 1200.0 ELSE 360.0 END) ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND (' ' || lower(sd.title)) LIKE '% ' || :q_norm || '%' THEN (CASE WHEN :is_prefix_mode = 1 THEN 1400.0 WHEN :is_short_query = 1 THEN 300.0 ELSE 210.0 END) ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND :is_short_query = 0 AND lower(sd.title) LIKE '%' || :q_norm || '%' THEN 140.0 ELSE 0.0 END +
                               CASE WHEN :is_text_query = 1 AND :is_prefix_mode = 1 AND lower(sd.title) LIKE '%' || :q_norm || '%' AND lower(sd.title) NOT LIKE :q_norm || '%' THEN -850.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(coalesce(p.collector_number, '')) = :q_norm THEN 2600.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'set' AND lower(coalesce(s.code, '')) = :q_norm THEN 1700.0 ELSE 0.0 END +
                               CASE WHEN :is_exact_code_query = 1 AND sd.doc_type = 'print' AND lower(coalesce(s.code, '')) = :code_prefix THEN 420.0 ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND lower(coalesce(p.collector_number, '')) LIKE :q_norm || '%' THEN 920.0 ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND lower(coalesce(s.code, '')) LIKE :q_norm || '%' THEN 760.0 ELSE 0.0 END +
                               CASE WHEN :is_short_query = 1 AND :is_code_like_query = 0 AND sd.doc_type = 'card' THEN (CASE WHEN :is_prefix_mode = 1 THEN 650.0 ELSE 120.0 END) ELSE 0.0 END +
                               CASE WHEN :is_short_query = 1 AND :is_code_like_query = 0 AND sd.doc_type = 'print' THEN (CASE WHEN :is_prefix_mode = 1 THEN -260.0 ELSE -35.0 END) ELSE 0.0 END +
                               CASE WHEN :is_code_like_query = 1 AND sd.doc_type = 'card' THEN -120.0 ELSE 0.0 END +
                               CASE WHEN :is_prefix_mode = 1 AND :game = '' AND g.slug = 'pokemon' THEN 500.0 ELSE 0.0 END +
                               CASE WHEN :is_prefix_mode = 1 AND :game = '' AND g.slug <> 'pokemon' THEN -220.0 ELSE 0.0 END +
                               1.0
                           ) AS score,
                           s.code AS set_code, p.collector_number, p.variant,
                           COALESCE(
                               (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1),
                               (
                                   SELECT pi2.url
                                   FROM print_images pi2
                                   JOIN prints p2 ON p2.id = pi2.print_id
                                   WHERE p2.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
                                   ORDER BY pi2.is_primary DESC, pi2.id ASC
                                   LIMIT 1
                               ),
                               CASE
                                   WHEN sd.doc_type = 'set' AND g.slug = 'riftbound' THEN CASE lower(COALESCE(s.code, ''))
                                       WHEN 'rb1' THEN '/images/riftbound/rb1-placeholder.svg'
                                       WHEN 'rb2' THEN '/images/riftbound/rb2-placeholder.svg'
                                       WHEN 'ogn' THEN '/images/riftbound/ogn-placeholder.svg'
                                       ELSE '/images/riftbound/rb1-placeholder.svg'
                                   END
                                   ELSE NULL
                               END
                           ) AS primary_image_url
                    FROM search_documents sd
                    JOIN games g ON g.id = sd.game_id
                    LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
                    LEFT JOIN sets s ON p.set_id = s.id
                    WHERE lower(coalesce(sd.tsv, sd.title || ' ' || coalesce(sd.subtitle, ''))) LIKE :like
                      AND (
                            :is_short_query = 0
                            OR lower(sd.title) LIKE :q_norm || '%'
                            OR (' ' || lower(sd.title)) LIKE '% ' || :q_norm || '%'
                            OR lower(coalesce(p.collector_number, '')) LIKE :q_norm || '%'
                            OR lower(coalesce(s.code, '')) LIKE :q_norm || '%'
                          )
                      AND (:game = '' OR g.slug = :game)
                      AND (:type IS NULL OR sd.doc_type = :type)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                )
                rows = session.execute(
                    sql,
                    {
                        "q": q,
                        "q_norm": q_normalized,
                        "is_code_like_query": is_code_like_query,
                        "is_exact_code_query": is_exact_code_query,
                        "is_text_query": is_text_query,
                        "is_short_query": short_query_mode,
                        "is_prefix_mode": is_prefix_mode,
                        "is_exact_mode": is_exact_mode,
                        "code_prefix": code_prefix,
                        "like": like,
                        "game": game,
                        "type": result_type,
                        "limit": limit,
                        "offset": offset,
                    },
                ).mappings().all()
        except ProgrammingError:
            logger.exception(
                "ProgrammingError in search short-query branch; falling back to LIKE search",
                extra={
                    "q": q,
                    "q_normalized": q_normalized,
                    "short_query_mode": short_query_mode,
                    "game": game,
                    "result_type": result_type,
                },
            )
            session.rollback()
            like = f"{q.lower()}%" if query_length <= 2 else f"%{q.lower()}%"
            rows = _fallback_search_rows(session, like=like, game=game, result_type=result_type, limit=limit, offset=offset)

        if not rows:
            like = f"{q.lower()}%" if query_length <= 2 else f"%{q.lower()}%"
            rows = _fallback_search_rows(session, like=like, game=game, result_type=result_type, limit=limit, offset=offset)

    return jsonify([_to_public_search_row(dict(row)) for row in rows])


@search_bp.get("/api/search/suggest")
@search_bp.get("/api/v1/search/suggest")
def suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])

    game = request.args.get("game", "").strip()
    max_limit = 6 if len(q) == 1 else 10
    limit = min(max(request.args.get("limit", default=10, type=int) or 10, 1), max_limit)

    with db.SessionLocal() as session:
        try:
            rows = _fallback_suggest_rows(session, q=q, game=game, limit=limit)
        except ProgrammingError:
            session.rollback()
            rows = _fallback_suggest_rows(session, q=q, game=game, limit=limit)

    return jsonify([_to_public_search_row(dict(row)) for row in rows])
