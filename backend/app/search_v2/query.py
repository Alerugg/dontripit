from __future__ import annotations

import re

from sqlalchemy import and_, func, select, text

from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.normalization import (
    compact_search_text,
    normalize_onepiece_collector_number,
    normalize_onepiece_set_code,
    normalize_search_text,
)
from app.search_v2_models import FacetDefinition, PrintSearchProfile


_ONEPIECE_COLORS = {
    "red": "red",
    "green": "green",
    "blue": "blue",
    "purple": "purple",
    "black": "black",
    "yellow": "yellow",
}

_ONEPIECE_CARD_TYPES = {
    "leader": "leader",
    "leaders": "leader",
    "character": "character",
    "characters": "character",
    "event": "event",
    "events": "event",
    "stage": "stage",
    "stages": "stage",
}

_ONEPIECE_LANGUAGES = {
    "english": "en",
    "en": "en",
    "japanese": "jp",
    "jp": "jp",
}

# Single-letter rarities are intentionally not interpreted as natural-search
# intent. They collide too easily with normal words. Exact collectors and the
# Advanced Search can still address every rarity.
_ONEPIECE_RARITIES = {
    "sec": "SEC",
    "sr": "SR",
    "uc": "UC",
    "sp": "SP",
    "tr": "TR",
}

_ONEPIECE_NUMERIC_STATS = {"cost", "life", "power", "counter"}


def _bounded_limit(value: int | None, *, default: int = 24, maximum: int = 100) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _query_tokens(query: str) -> list[str]:
    return [
        token
        for token in normalize_search_text(query).split()
        if len(token) >= 2 or token.isdigit()
    ][:10]


def _find_onepiece_set(tokens: list[str], full_query: str) -> str | None:
    direct = normalize_onepiece_set_code(full_query)
    if direct:
        return direct
    for token in tokens:
        parsed = normalize_onepiece_set_code(token)
        if parsed:
            return parsed
    return None


def _set_numeric_bounds(intent: dict[str, object], stat: str, minimum: int | None, maximum: int | None) -> None:
    if minimum is not None:
        intent[f"{stat}_min"] = max(0, int(minimum))
    if maximum is not None:
        intent[f"{stat}_max"] = max(0, int(maximum))


def _parse_onepiece_numeric_bounds(query: str) -> dict[str, object]:
    """Parse exact/range/comparator numeric intent from the raw user query.

    Supported forms include:
    - ``life 5`` / ``5 life``
    - ``life 4-5``
    - ``power >= 10000`` / ``cost <= 3``
    - ``power 10000+`` / ``10000+ power``

    Stats are integer-valued, so strict comparisons become inclusive integer
    bounds (``> 5000`` -> minimum 5001).
    """
    source = str(query or "").lower()
    intent: dict[str, object] = {}

    for stat in _ONEPIECE_NUMERIC_STATS:
        range_match = re.search(rf"\b{stat}\s*(\d+)\s*-\s*(\d+)\b", source)
        if range_match:
            low, high = sorted((int(range_match.group(1)), int(range_match.group(2))))
            _set_numeric_bounds(intent, stat, low, high)
            continue

        comparator_match = re.search(rf"\b{stat}\s*(>=|<=|>|<|=)\s*(\d+)\b", source)
        if comparator_match:
            operator, raw_value = comparator_match.groups()
            value = int(raw_value)
            if operator == ">=":
                _set_numeric_bounds(intent, stat, value, None)
            elif operator == ">":
                _set_numeric_bounds(intent, stat, value + 1, None)
            elif operator == "<=":
                _set_numeric_bounds(intent, stat, None, value)
            elif operator == "<":
                _set_numeric_bounds(intent, stat, None, max(0, value - 1))
            else:
                _set_numeric_bounds(intent, stat, value, value)
            continue

        plus_after_match = re.search(rf"\b{stat}\s*(\d+)\s*\+", source)
        if plus_after_match:
            _set_numeric_bounds(intent, stat, int(plus_after_match.group(1)), None)
            continue

        plus_before_match = re.search(rf"\b(\d+)\s*\+\s*{stat}\b", source)
        if plus_before_match:
            _set_numeric_bounds(intent, stat, int(plus_before_match.group(1)), None)
            continue

        exact_after_match = re.search(rf"\b{stat}\s+(\d+)\b", source)
        if exact_after_match:
            value = int(exact_after_match.group(1))
            _set_numeric_bounds(intent, stat, value, value)
            continue

        exact_before_match = re.search(rf"\b(\d+)\s+{stat}\b", source)
        if exact_before_match:
            value = int(exact_before_match.group(1))
            _set_numeric_bounds(intent, stat, value, value)

    return intent


def _parse_onepiece_intent(
    tokens: list[str],
    set_code: str | None,
    numeric_bounds: dict[str, object] | None = None,
) -> tuple[dict[str, object], list[str]]:
    """Split natural One Piece properties from free-text identity terms."""
    intent: dict[str, object] = dict(numeric_bounds or {})
    residual: list[str] = []
    consumed: set[int] = set()

    # Raw-query parsing already understood numeric syntax. Remove the stat token
    # and its adjacent numeric tokens from the free-text identity terms.
    for index, token in enumerate(tokens):
        if token not in _ONEPIECE_NUMERIC_STATS:
            continue
        if not any(f"{token}_{suffix}" in intent for suffix in ("min", "max")):
            continue
        consumed.add(index)
        if index - 1 >= 0 and tokens[index - 1].isdigit():
            consumed.add(index - 1)
        if index + 1 < len(tokens) and tokens[index + 1].isdigit():
            consumed.add(index + 1)
            if index + 2 < len(tokens) and tokens[index + 2].isdigit():
                consumed.add(index + 2)

    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        token_set = normalize_onepiece_set_code(token)
        if set_code and token_set == set_code:
            continue
        if token in _ONEPIECE_COLORS and "color" not in intent:
            intent["color"] = _ONEPIECE_COLORS[token]
            continue
        if token in _ONEPIECE_CARD_TYPES and "card_type" not in intent:
            intent["card_type"] = _ONEPIECE_CARD_TYPES[token]
            continue
        if token in _ONEPIECE_LANGUAGES and "language" not in intent:
            intent["language"] = _ONEPIECE_LANGUAGES[token]
            continue
        if token in _ONEPIECE_RARITIES and "rarity" not in intent:
            intent["rarity"] = _ONEPIECE_RARITIES[token]
            continue
        residual.append(token)

    if set_code:
        intent["set"] = set_code
    return intent, residual


def _public_card_row(row: dict) -> dict:
    return {
        "type": "card",
        "card_id": row.get("card_id"),
        "card_key": row.get("card_key"),
        "name": row.get("name"),
        "game": row.get("game"),
        "matched_print": {
            "print_id": row.get("print_id"),
            "set_code": row.get("set_code"),
            "set_name": row.get("set_name"),
            "collector_number": row.get("collector_number"),
            "language": row.get("language"),
            "rarity": row.get("rarity"),
            "exact_variant": row.get("exact_variant"),
            "variant_family": row.get("variant_family"),
            "primary_image_url": row.get("primary_image_url"),
        },
        "variant_count": int(row.get("variant_count") or 0),
        "attributes": row.get("attributes_json") or {},
        "score": round(float(row.get("score") or 0), 4),
    }


def normal_search(session, *, query: str, game_slug: str | None = None, limit: int = 24) -> list[dict]:
    """Google-like logical-Card search with exact Print evidence."""
    q = str(query or "").strip()
    if not q:
        return []

    limit = _bounded_limit(limit)
    q_norm = normalize_search_text(q)
    q_compact = compact_search_text(q)
    all_tokens = _query_tokens(q)
    is_onepiece = not game_slug or game_slug == "onepiece"
    q_collector = normalize_onepiece_collector_number(q) if is_onepiece else None
    collector_only = bool(q_collector and q_compact == compact_search_text(q_collector))
    q_set = None if collector_only else (_find_onepiece_set(all_tokens, q) if is_onepiece else None)

    if is_onepiece and not collector_only:
        numeric_bounds = _parse_onepiece_numeric_bounds(q)
        intent, tokens = _parse_onepiece_intent(all_tokens, q_set, numeric_bounds)
    else:
        intent, tokens = ({}, all_tokens)

    name_query_norm = " ".join(tokens) if tokens else q_norm
    name_query_compact = compact_search_text(name_query_norm)
    single_token_query = len(tokens) == 1 and tokens[0] == name_query_norm
    has_residual = bool(tokens)
    has_structured = bool(intent)

    if session.bind.dialect.name != "postgresql":
        stmt = (
            select(PrintSearchProfile, Print, Card, Set, Game)
            .join(Print, Print.id == PrintSearchProfile.print_id)
            .join(Card, Card.id == PrintSearchProfile.card_id)
            .join(Set, Set.id == Print.set_id)
            .join(Game, Game.id == PrintSearchProfile.game_id)
        )
        if collector_only:
            stmt = stmt.where(PrintSearchProfile.normalized_collector_number == q_collector)
        elif tokens:
            stmt = stmt.where(and_(*[PrintSearchProfile.search_text.contains(token) for token in tokens]))
        elif q_set:
            stmt = stmt.where(PrintSearchProfile.normalized_set_code == q_set)
        else:
            stmt = stmt.where(PrintSearchProfile.search_text.contains(q_norm))
        if game_slug:
            stmt = stmt.where(Game.slug == game_slug)

        rows = session.execute(stmt.limit(limit * 20)).all()
        total_variants = dict(
            session.execute(
                select(PrintSearchProfile.card_id, func.count(PrintSearchProfile.id))
                .group_by(PrintSearchProfile.card_id)
            ).all()
        )
        seen: set[int] = set()
        result: list[dict] = []
        for profile, print_row, card, set_row, game in rows:
            if card.id in seen:
                continue
            seen.add(card.id)
            image_url = session.execute(
                select(PrintImage.url)
                .where(PrintImage.print_id == print_row.id)
                .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
                .limit(1)
            ).scalar_one_or_none()
            result.append(
                {
                    "type": "card",
                    "card_id": card.id,
                    "card_key": card.card_key,
                    "name": card.name,
                    "game": game.slug,
                    "matched_print": {
                        "print_id": print_row.id,
                        "set_code": set_row.code,
                        "set_name": set_row.name,
                        "collector_number": print_row.collector_number,
                        "language": print_row.language,
                        "rarity": print_row.rarity,
                        "exact_variant": profile.exact_variant,
                        "variant_family": profile.variant_family,
                        "primary_image_url": image_url,
                    },
                    "variant_count": int(total_variants.get(card.id, 1)),
                    "attributes": profile.attributes_json or {},
                    "score": 1.0,
                }
            )
            if len(result) >= limit:
                break
        return result

    token_bonus_sql = " + ".join(
        [f"CASE WHEN psp.search_text LIKE :token_{i} THEN 55.0 ELSE 0.0 END" for i in range(len(tokens))]
    ) or "0.0"
    token_params = {f"token_{i}": f"%{token}%" for i, token in enumerate(tokens)}
    all_tokens_sql = " AND ".join([f"psp.search_text LIKE :token_{i}" for i in range(len(tokens))]) or "FALSE"
    all_tokens_bonus = f"CASE WHEN {all_tokens_sql} THEN 900.0 ELSE 0.0 END" if tokens else "0.0"
    last_name_token_sql = "regexp_replace(psp.normalized_name, '^.* ', '')"

    color_match = "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(psp.attributes_json -> 'color', '[]'::jsonb)) color_value WHERE lower(color_value) = :q_color)"
    type_match = "lower(COALESCE(psp.attributes_json ->> 'card_type', '')) = :q_card_type"
    language_match = "lower(COALESCE(psp.language, '')) = :q_language"
    rarity_match = "upper(COALESCE(psp.rarity, '')) = :q_rarity"
    set_match = "psp.normalized_set_code = :q_set"

    def numeric_match(stat: str) -> str:
        value = f"(psp.attributes_json ->> '{stat}')::int"
        valid = f"COALESCE(psp.attributes_json ->> '{stat}', '') ~ '^[0-9]+$'"
        return (
            f"{valid} "
            f"AND (:q_{stat}_min < 0 OR {value} >= :q_{stat}_min) "
            f"AND (:q_{stat}_max < 0 OR {value} <= :q_{stat}_max)"
        )

    cost_match = numeric_match("cost")
    life_match = numeric_match("life")
    power_match = numeric_match("power")
    counter_match = numeric_match("counter")

    structured_conditions = [
        "(:q_color = '' OR " + color_match + ")",
        "(:q_card_type = '' OR " + type_match + ")",
        "(:q_language = '' OR " + language_match + ")",
        "(:q_rarity = '' OR " + rarity_match + ")",
        "(:q_set = '' OR " + set_match + ")",
        "((:q_cost_min < 0 AND :q_cost_max < 0) OR (" + cost_match + "))",
        "((:q_life_min < 0 AND :q_life_max < 0) OR (" + life_match + "))",
        "((:q_power_min < 0 AND :q_power_max < 0) OR (" + power_match + "))",
        "((:q_counter_min < 0 AND :q_counter_max < 0) OR (" + counter_match + "))",
    ]
    all_structured_sql = " AND ".join(structured_conditions)

    residual_candidate_sql = f"""
        ({all_tokens_sql})
        OR psp.normalized_name LIKE '%' || :q_name_norm || '%'
        OR psp.search_text LIKE '%' || :q_name_norm || '%'
        OR similarity(psp.normalized_name, :q_name_norm) >= 0.18
        OR similarity(psp.search_text, :q_name_norm) >= 0.12
        OR (:single_token AND similarity({last_name_token_sql}, :q_name_norm) >= 0.20)
    """

    sql = text(
        f"""
        WITH variant_counts AS (
          SELECT card_id, COUNT(*) AS variant_count
          FROM print_search_profiles
          GROUP BY card_id
        ), scored AS (
          SELECT
            psp.card_id,
            c.card_key,
            c.name,
            g.slug AS game,
            psp.print_id,
            s.code AS set_code,
            s.name AS set_name,
            p.collector_number,
            p.language,
            p.rarity,
            psp.exact_variant,
            psp.variant_family,
            psp.attributes_json,
            vc.variant_count,
            (
              CASE WHEN :q_collector <> '' AND psp.normalized_collector_number = :q_collector THEN 5000.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name = :q_name_norm THEN 3600.0 ELSE 0.0 END +
              CASE WHEN replace(psp.normalized_name, ' ', '') = :q_name_compact AND :q_name_compact <> '' THEN 3200.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE '% ' || :q_name_norm THEN 2600.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE :q_name_norm || '%' THEN 1800.0 ELSE 0.0 END +
              CASE WHEN (' ' || psp.normalized_name || ' ') LIKE '% ' || :q_name_norm || ' %' THEN 1500.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE '%' || :q_name_norm || '%' THEN 1100.0 ELSE 0.0 END +
              {all_tokens_bonus} +
              {token_bonus_sql} +
              similarity(psp.normalized_name, :q_name_norm) * 900.0 +
              similarity(psp.search_text, :q_name_norm) * 350.0 +
              CASE WHEN :single_token THEN similarity({last_name_token_sql}, :q_name_norm) * 2400.0 ELSE 0.0 END +
              CASE WHEN :q_color <> '' AND {color_match} THEN 1600.0 ELSE 0.0 END +
              CASE WHEN :q_card_type <> '' AND {type_match} THEN 1900.0 ELSE 0.0 END +
              CASE WHEN :q_language <> '' AND {language_match} THEN 1000.0 ELSE 0.0 END +
              CASE WHEN :q_rarity <> '' AND {rarity_match} THEN 1300.0 ELSE 0.0 END +
              CASE WHEN :q_set <> '' AND {set_match} THEN 1200.0 ELSE 0.0 END +
              CASE WHEN (:q_cost_min >= 0 OR :q_cost_max >= 0) AND ({cost_match}) THEN 1500.0 ELSE 0.0 END +
              CASE WHEN (:q_life_min >= 0 OR :q_life_max >= 0) AND ({life_match}) THEN 1700.0 ELSE 0.0 END +
              CASE WHEN (:q_power_min >= 0 OR :q_power_max >= 0) AND ({power_match}) THEN 1600.0 ELSE 0.0 END +
              CASE WHEN (:q_counter_min >= 0 OR :q_counter_max >= 0) AND ({counter_match}) THEN 1500.0 ELSE 0.0 END +
              CASE WHEN :has_structured AND ({all_structured_sql}) THEN 2500.0 ELSE 0.0 END +
              CASE WHEN psp.exact_variant = 'default' THEN 25.0 ELSE 0.0 END
            ) AS score
          FROM print_search_profiles psp
          JOIN prints p ON p.id = psp.print_id
          JOIN cards c ON c.id = psp.card_id
          JOIN sets s ON s.id = p.set_id
          JOIN games g ON g.id = psp.game_id
          JOIN variant_counts vc ON vc.card_id = psp.card_id
          WHERE (:game = '' OR g.slug = :game)
            AND (
              (:collector_only = TRUE AND psp.normalized_collector_number = :q_collector)
              OR
              (:collector_only = FALSE AND :has_residual = TRUE AND ({residual_candidate_sql}))
              OR
              (:collector_only = FALSE AND :has_residual = FALSE AND :has_structured = TRUE AND ({all_structured_sql}))
              OR
              (:collector_only = FALSE AND :has_residual = FALSE AND :has_structured = FALSE AND (
                psp.normalized_name LIKE '%' || :q_norm || '%'
                OR psp.search_text LIKE '%' || :q_norm || '%'
                OR similarity(psp.normalized_name, :q_norm) >= 0.18
                OR similarity(psp.search_text, :q_norm) >= 0.12
              ))
            )
        ), ranked AS (
          SELECT
            *,
            ROW_NUMBER() OVER (
              PARTITION BY card_id
              ORDER BY score DESC,
                       CASE WHEN exact_variant = 'default' THEN 0 ELSE 1 END,
                       print_id ASC
            ) AS card_rank
          FROM scored
          WHERE score > 0
        )
        SELECT
          r.*,
          (
            SELECT pi.url
            FROM print_images pi
            WHERE pi.print_id = r.print_id
            ORDER BY pi.is_primary DESC, pi.id ASC
            LIMIT 1
          ) AS primary_image_url
        FROM ranked r
        WHERE r.card_rank = 1
        ORDER BY r.score DESC, r.name ASC, r.card_id ASC
        LIMIT :limit
        """
    )

    def bound(stat: str, side: str) -> int:
        return int(intent.get(f"{stat}_{side}", -1))

    params = {
        "q_norm": q_norm,
        "q_name_norm": name_query_norm,
        "q_name_compact": name_query_compact,
        "q_collector": q_collector or "",
        "q_set": str(intent.get("set") or ""),
        "q_color": str(intent.get("color") or ""),
        "q_card_type": str(intent.get("card_type") or ""),
        "q_language": str(intent.get("language") or ""),
        "q_rarity": str(intent.get("rarity") or ""),
        "q_cost_min": bound("cost", "min"),
        "q_cost_max": bound("cost", "max"),
        "q_life_min": bound("life", "min"),
        "q_life_max": bound("life", "max"),
        "q_power_min": bound("power", "min"),
        "q_power_max": bound("power", "max"),
        "q_counter_min": bound("counter", "min"),
        "q_counter_max": bound("counter", "max"),
        "collector_only": collector_only,
        "single_token": single_token_query,
        "has_residual": has_residual,
        "has_structured": has_structured,
        "game": str(game_slug or "").strip().lower(),
        "limit": limit,
        **token_params,
    }
    return [_public_card_row(dict(row)) for row in session.execute(sql, params).mappings().all()]


def facet_definitions(session, *, game_slug: str) -> list[dict]:
    rows = session.execute(
        select(FacetDefinition)
        .join(Game, Game.id == FacetDefinition.game_id)
        .where(Game.slug == game_slug, FacetDefinition.active.is_(True))
        .order_by(FacetDefinition.group_name.asc(), FacetDefinition.display_order.asc(), FacetDefinition.id.asc())
    ).scalars().all()
    return [
        {
            "scope": row.scope,
            "key": row.key,
            "label": row.label,
            "value_type": row.value_type,
            "ui_type": row.ui_type,
            "group": row.group_name,
            "multi_value": row.multi_value,
            "filterable": row.filterable,
            "sortable": row.sortable,
            "searchable": row.searchable,
            "quick_filter": row.quick_filter,
            "display_order": row.display_order,
            "options": row.options_json,
        }
        for row in rows
    ]
