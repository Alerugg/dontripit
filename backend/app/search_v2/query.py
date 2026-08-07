from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, func, or_, select, text

from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.normalization import (
    compact_search_text,
    normalize_language,
    normalize_onepiece_collector_number,
    normalize_onepiece_set_code,
    normalize_search_text,
)
from app.search_v2_models import FacetDefinition, PrintSearchProfile


def _bounded_limit(value: int | None, *, default: int = 24, maximum: int = 100) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _query_tokens(query: str) -> list[str]:
    tokens = [token for token in normalize_search_text(query).split() if len(token) >= 2]
    return tokens[:8]


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
    """Google-like card search, grouped by logical Card.

    The best matching physical Print is retained as evidence, while duplicate
    physical variants are collapsed into one logical Card result.
    """
    q = str(query or "").strip()
    if not q:
        return []
    limit = _bounded_limit(limit)
    q_norm = normalize_search_text(q)
    q_compact = compact_search_text(q)
    q_collector = normalize_onepiece_collector_number(q) if (not game_slug or game_slug == "onepiece") else None
    q_set = normalize_onepiece_set_code(q) if (not game_slug or game_slug == "onepiece") else None
    tokens = _query_tokens(q)

    if session.bind.dialect.name != "postgresql":
        # Deterministic test/dev fallback. Production uses pg_trgm below.
        stmt = (
            select(
                PrintSearchProfile,
                Print,
                Card,
                Set,
                Game,
            )
            .join(Print, Print.id == PrintSearchProfile.print_id)
            .join(Card, Card.id == PrintSearchProfile.card_id)
            .join(Set, Set.id == Print.set_id)
            .join(Game, Game.id == PrintSearchProfile.game_id)
            .where(PrintSearchProfile.search_text.contains(q_norm))
        )
        if game_slug:
            stmt = stmt.where(Game.slug == game_slug)
        rows = session.execute(stmt.limit(limit * 12)).all()
        seen = set()
        result = []
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
                    "variant_count": len(rows),
                    "attributes": profile.attributes_json or {},
                    "score": 1.0,
                }
            )
            if len(result) >= limit:
                break
        return result

    token_bonus_sql = " + ".join(
        [f"CASE WHEN psp.search_text LIKE :token_{i} THEN 35.0 ELSE 0.0 END" for i in range(len(tokens))]
    ) or "0.0"
    token_params = {f"token_{i}": f"%{token}%" for i, token in enumerate(tokens)}

    sql = text(
        f"""
        WITH scored AS (
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
            (
              CASE WHEN :q_collector <> '' AND psp.normalized_collector_number = :q_collector THEN 5000.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name = :q_norm THEN 3600.0 ELSE 0.0 END +
              CASE WHEN replace(psp.normalized_name, ' ', '') = :q_compact AND :q_compact <> '' THEN 3200.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE :q_norm || '%' THEN 1800.0 ELSE 0.0 END +
              CASE WHEN (' ' || psp.normalized_name || ' ') LIKE '% ' || :q_norm || ' %' THEN 1500.0 ELSE 0.0 END +
              CASE WHEN psp.normalized_name LIKE '%' || :q_norm || '%' THEN 1100.0 ELSE 0.0 END +
              CASE WHEN :q_set <> '' AND psp.normalized_set_code = :q_set THEN 900.0 ELSE 0.0 END +
              CASE WHEN psp.search_text LIKE '%' || :q_norm || '%' THEN 700.0 ELSE 0.0 END +
              {token_bonus_sql} +
              similarity(psp.normalized_name, :q_norm) * 900.0 +
              similarity(psp.search_text, :q_norm) * 350.0 +
              CASE WHEN psp.exact_variant = 'default' THEN 25.0 ELSE 0.0 END
            ) AS score,
            COUNT(*) OVER (PARTITION BY psp.card_id) AS variant_count,
            ROW_NUMBER() OVER (
              PARTITION BY psp.card_id
              ORDER BY
                CASE WHEN :q_collector <> '' AND psp.normalized_collector_number = :q_collector THEN 0 ELSE 1 END,
                CASE WHEN psp.exact_variant = 'default' THEN 0 ELSE 1 END,
                psp.print_id ASC
            ) AS preliminary_rank
          FROM print_search_profiles psp
          JOIN prints p ON p.id = psp.print_id
          JOIN cards c ON c.id = psp.card_id
          JOIN sets s ON s.id = p.set_id
          JOIN games g ON g.id = psp.game_id
          WHERE (:game = '' OR g.slug = :game)
            AND (
              psp.normalized_name LIKE '%' || :q_norm || '%'
              OR psp.search_text LIKE '%' || :q_norm || '%'
              OR (:q_collector <> '' AND psp.normalized_collector_number = :q_collector)
              OR (:q_set <> '' AND psp.normalized_set_code = :q_set)
              OR similarity(psp.normalized_name, :q_norm) >= 0.18
              OR similarity(psp.search_text, :q_norm) >= 0.12
            )
        ), ranked AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY score DESC, preliminary_rank ASC, print_id ASC) AS card_rank
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
    params = {
        "q_norm": q_norm,
        "q_compact": q_compact,
        "q_collector": q_collector or "",
        "q_set": q_set or "",
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


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text_value = str(value).strip()
    return [text_value] if text_value else []


def _numeric_bounds(value) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        low, high = value.get("min"), value.get("max")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        low, high = value
    else:
        low = high = value
    def conv(item):
        if item in (None, ""):
            return None
        return int(item)
    return conv(low), conv(high)


def advanced_print_search(
    session,
    *,
    game_slug: str,
    filters: dict | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Exact Print-level advanced filtering using typed columns + JSONB attributes."""
    filters = dict(filters or {})
    limit = _bounded_limit(limit, default=50, maximum=200)
    offset = max(int(offset or 0), 0)

    stmt = (
        select(PrintSearchProfile, Print, Card, Set, Game)
        .join(Print, Print.id == PrintSearchProfile.print_id)
        .join(Card, Card.id == PrintSearchProfile.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == PrintSearchProfile.game_id)
        .where(Game.slug == game_slug)
    )

    q_norm = normalize_search_text(query or "")
    if q_norm:
        stmt = stmt.where(PrintSearchProfile.search_text.contains(q_norm))

    set_values = _as_list(filters.pop("set", None))
    if set_values:
        normalized_sets = [normalize_onepiece_set_code(value) or normalize_search_text(value).replace(" ", "-") for value in set_values]
        stmt = stmt.where(PrintSearchProfile.normalized_set_code.in_(normalized_sets))

    collector_values = _as_list(filters.pop("collector_number", None))
    if collector_values:
        normalized_collectors = [normalize_onepiece_collector_number(value) or normalize_search_text(value).replace(" ", "-") for value in collector_values]
        stmt = stmt.where(PrintSearchProfile.normalized_collector_number.in_(normalized_collectors))

    language_values = _as_list(filters.pop("language", None))
    if language_values:
        stmt = stmt.where(PrintSearchProfile.language.in_([normalize_language(value) for value in language_values]))

    rarity_values = _as_list(filters.pop("rarity", None))
    if rarity_values:
        stmt = stmt.where(func.lower(PrintSearchProfile.rarity).in_([value.lower() for value in rarity_values]))

    exact_variants = _as_list(filters.pop("exact_variant", None))
    if exact_variants:
        stmt = stmt.where(PrintSearchProfile.exact_variant.in_([value.lower() for value in exact_variants]))

    families = _as_list(filters.pop("variant_family", None))
    if families:
        stmt = stmt.where(PrintSearchProfile.variant_family.in_([value.lower() for value in families]))

    # JSONB-backed game-specific facets. These are only executed on PostgreSQL;
    # the public contract remains the same if another search engine is added later.
    if session.bind.dialect.name == "postgresql":
        for key in ("color", "attribute", "traits"):
            values = _as_list(filters.pop(key, None))
            if values:
                stmt = stmt.where(or_(*[PrintSearchProfile.attributes_json[key].contains([value]) for value in values]))

        scalar_map = {
            "card_type": "card_type",
            "block": "block",
        }
        for input_key, json_key in scalar_map.items():
            values = _as_list(filters.pop(input_key, None))
            if values:
                stmt = stmt.where(PrintSearchProfile.attributes_json[json_key].astext.in_(values))

        for key in ("cost", "life", "power", "counter"):
            value = filters.pop(key, None)
            if value is None:
                continue
            low, high = _numeric_bounds(value)
            expr = PrintSearchProfile.attributes_json[key].astext.cast(text("integer"))
            if low is not None:
                stmt = stmt.where(expr >= low)
            if high is not None:
                stmt = stmt.where(expr <= high)

        boolean_map = {
            "promo": "is_promo",
            "sp": "is_sp",
            "treasure_rare": "is_treasure_rare",
        }
        for input_key, json_key in boolean_map.items():
            value = filters.pop(input_key, None)
            if value is not None:
                truthy = value is True or str(value).lower() in {"1", "true", "yes", "on"}
                stmt = stmt.where(PrintSearchProfile.attributes_json[json_key].astext == ("true" if truthy else "false"))

    # Intentionally reject unsupported filters instead of silently ignoring them.
    if filters:
        raise ValueError(f"Unsupported advanced filters: {sorted(filters)}")

    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(stmt.order_by(Card.name.asc(), Set.code.asc(), Print.collector_number.asc(), Print.id.asc()).offset(offset).limit(limit)).all()

    items = []
    for profile, print_row, card, set_row, game in rows:
        image_url = session.execute(
            select(PrintImage.url)
            .where(PrintImage.print_id == print_row.id)
            .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        items.append(
            {
                "type": "print",
                "print_id": print_row.id,
                "card_id": card.id,
                "card_key": card.card_key,
                "name": card.name,
                "game": game.slug,
                "set_code": set_row.code,
                "set_name": set_row.name,
                "collector_number": print_row.collector_number,
                "language": profile.language,
                "rarity": profile.rarity,
                "exact_variant": profile.exact_variant,
                "variant_family": profile.variant_family,
                "releases": profile.release_names_json or [],
                "attributes": profile.attributes_json or {},
                "primary_image_url": image_url,
            }
        )

    return {"items": items, "total": int(total or 0), "limit": limit, "offset": offset}
