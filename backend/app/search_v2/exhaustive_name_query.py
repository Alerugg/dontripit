from __future__ import annotations

from sqlalchemy import func, select, text

from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.normalization import normalize_search_text
from app.search_v2_models import PrintSearchProfile


def _empty(limit: int, offset: int) -> dict:
    return {
        "items": [],
        "total": 0,
        "total_prints": 0,
        "limit": limit,
        "offset": offset,
        "has_more": False,
        "next_offset": None,
    }


def _sqlite_page(session, *, query: str, game: str | None, limit: int, offset: int) -> dict:
    q_norm = normalize_search_text(query)
    if not q_norm:
        return _empty(limit, offset)

    cards = session.execute(
        select(Card, Game)
        .join(Game, Game.id == Card.game_id)
        .order_by(Card.name.asc(), Card.id.asc())
    ).all()
    matched = [
        (card, game_row)
        for card, game_row in cards
        if (not game or game_row.slug == game)
        and q_norm in normalize_search_text(card.name)
    ]
    total = len(matched)
    total_prints = 0
    for card, _ in matched:
        total_prints += int(
            session.execute(select(func.count(Print.id)).where(Print.card_id == card.id)).scalar_one()
        )

    def score(card: Card) -> float:
        name = normalize_search_text(card.name)
        if name == q_norm:
            return 5000.0
        if name.startswith(q_norm):
            return 3000.0
        if f" {q_norm} " in f" {name} ":
            return 2200.0
        return 1500.0

    matched.sort(key=lambda pair: (-score(pair[0]), pair[0].name.lower(), pair[0].id))
    page_rows = matched[offset : offset + limit]
    items: list[dict] = []
    for card, game_row in page_rows:
        representative = session.execute(
            select(Print, Set)
            .join(Set, Set.id == Print.set_id)
            .where(Print.card_id == card.id)
            .order_by(Print.id.asc())
            .limit(1)
        ).first()
        if representative is None:
            continue
        print_row, set_row = representative
        image_url = session.execute(
            select(PrintImage.url)
            .where(PrintImage.print_id == print_row.id)
            .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        variant_count = int(
            session.execute(
                select(func.count(Print.id)).where(Print.card_id == card.id)
            ).scalar_one()
        )
        profile = session.execute(
            select(PrintSearchProfile)
            .where(PrintSearchProfile.print_id == print_row.id)
            .limit(1)
        ).scalar_one_or_none()
        items.append(
            {
                "type": "card",
                "card_id": card.id,
                "card_key": card.card_key,
                "name": card.name,
                "game": game_row.slug,
                "matched_print": {
                    "print_id": print_row.id,
                    "set_code": set_row.code,
                    "set_name": set_row.name,
                    "collector_number": print_row.collector_number,
                    "language": print_row.language,
                    "rarity": print_row.rarity,
                    "exact_variant": getattr(profile, "exact_variant", None),
                    "variant_family": getattr(profile, "variant_family", None),
                    "primary_image_url": image_url,
                },
                "variant_count": variant_count,
                "attributes": getattr(profile, "attributes_json", None) or {},
                "score": score(card),
            }
        )

    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": items,
        "total": total,
        "total_prints": total_prints,
        "limit": limit,
        "offset": offset,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }


def exhaustive_name_page(
    session,
    *,
    query: str,
    game: str | None,
    limit: int,
    offset: int,
) -> dict:
    """Page every logical Card whose canonical normalized name contains query.

    Canonical ``cards`` are the source of truth. Search profiles may enrich and
    rank a Card, but a missing/stale profile is never allowed to hide a real
    canonical Card. This is what makes common-name searches exhaustive.
    """
    q_norm = normalize_search_text(query)
    if not q_norm:
        return _empty(limit, offset)
    if session.bind.dialect.name != "postgresql":
        return _sqlite_page(session, query=query, game=game, limit=limit, offset=offset)

    # The fallback pattern only exists for canonical Cards that are absent from
    # card_search_profiles. Inter-token '%' also tolerates punctuation such as
    # Monkey.D.Luffy. Indexed Cards retain the fully normalized profile match.
    canonical_fallback = "%" + "%".join(q_norm.split()) + "%"
    params = {
        "game": str(game or "").strip().lower(),
        "contains": f"%{q_norm}%",
        "canonical_fallback": canonical_fallback,
        "q_norm": q_norm,
        "prefix": f"{q_norm}%",
        "word": f"% {q_norm} %",
        "limit": int(limit),
        "offset": int(offset),
    }

    matched_cards_cte = """
        SELECT
          c.id AS card_id,
          profile.normalized_name,
          COALESCE(profile.attributes_json, '{}'::jsonb) AS attributes_json,
          CASE
            WHEN profile.normalized_name = :q_norm THEN 5000.0
            WHEN profile.normalized_name LIKE :prefix THEN 3000.0
            WHEN (' ' || COALESCE(profile.normalized_name, '') || ' ') LIKE :word THEN 2200.0
            WHEN profile.normalized_name LIKE :contains THEN 1500.0
            ELSE 1400.0
          END AS score
        FROM cards c
        JOIN games g ON g.id = c.game_id
        LEFT JOIN LATERAL (
          SELECT csp.normalized_name, csp.attributes_json
          FROM card_search_profiles csp
          WHERE csp.card_id = c.id
            AND csp.normalized_name LIKE :contains
          ORDER BY
            CASE WHEN csp.normalized_name = :q_norm THEN 0 ELSE 1 END,
            csp.id ASC
          LIMIT 1
        ) profile ON TRUE
        WHERE (:game = '' OR g.slug = :game)
          AND (
            profile.normalized_name IS NOT NULL
            OR lower(c.name) LIKE :canonical_fallback
          )
    """

    count_sql = text(
        f"""
        WITH matched_cards AS MATERIALIZED (
          {matched_cards_cte}
        )
        SELECT
          COUNT(*)::bigint AS total_cards,
          COALESCE((
            SELECT COUNT(*)::bigint
            FROM prints p
            JOIN matched_cards mc ON mc.card_id = p.card_id
          ), 0)::bigint AS total_prints
        FROM matched_cards
        """
    )
    counts = session.execute(count_sql, params).mappings().one()
    total = int(counts["total_cards"] or 0)
    total_prints = int(counts["total_prints"] or 0)
    if total == 0:
        return _empty(limit, offset)

    page_sql = text(
        f"""
        WITH matched_cards AS MATERIALIZED (
          {matched_cards_cte}
        )
        SELECT
          c.id AS card_id,
          c.card_key,
          c.name,
          g.slug AS game,
          mc.attributes_json,
          chosen.print_id,
          chosen.set_code,
          chosen.set_name,
          chosen.collector_number,
          chosen.language,
          chosen.rarity,
          chosen.exact_variant,
          chosen.variant_family,
          chosen.primary_image_url,
          chosen.variant_count,
          mc.score
        FROM matched_cards mc
        JOIN cards c ON c.id = mc.card_id
        JOIN games g ON g.id = c.game_id
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
            (
              SELECT COUNT(*)::bigint
              FROM prints pv
              WHERE pv.card_id = mc.card_id
            ) AS variant_count
          FROM prints p
          JOIN sets s ON s.id = p.set_id
          LEFT JOIN print_search_profiles psp ON psp.print_id = p.id
          WHERE p.card_id = mc.card_id
          ORDER BY
            CASE WHEN lower(COALESCE(p.variant, '')) IN ('default', 'base', '') THEN 0 ELSE 1 END,
            (psp.print_id IS NOT NULL) DESC,
            p.id ASC
          LIMIT 1
        ) chosen ON TRUE
        ORDER BY mc.score DESC, lower(c.name) ASC, c.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    rows = session.execute(page_sql, params).mappings().all()
    items = [
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
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": items,
        "total": total,
        "total_prints": total_prints,
        "limit": limit,
        "offset": offset,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }
