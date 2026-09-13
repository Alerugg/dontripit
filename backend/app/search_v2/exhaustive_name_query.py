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


def _page_sql(matched_cards_cte: str):
    """Build the PostgreSQL page query around an already-scoped card source."""
    return text(
        f"""
        WITH matched_cards AS MATERIALIZED (
          {matched_cards_cte}
        ),
        card_stats AS MATERIALIZED (
          SELECT
            mc.*,
            (
              SELECT COUNT(*)::bigint
              FROM prints pv
              WHERE pv.card_id = mc.card_id
            ) AS variant_count
          FROM matched_cards mc
        ),
        paged_cards AS MATERIALIZED (
          SELECT
            c.id AS card_id,
            c.card_key,
            c.name,
            g.slug AS game,
            cs.attributes_json,
            cs.score,
            cs.variant_count,
            COUNT(*) OVER ()::bigint AS total_cards,
            COALESCE(SUM(cs.variant_count) OVER (), 0)::bigint AS total_prints
          FROM card_stats cs
          JOIN cards c ON c.id = cs.card_id
          JOIN games g ON g.id = c.game_id
          ORDER BY cs.score DESC, lower(c.name) ASC, c.id ASC
          LIMIT :limit OFFSET :offset
        )
        SELECT
          pc.card_id,
          pc.card_key,
          pc.name,
          pc.game,
          pc.attributes_json,
          chosen.print_id,
          chosen.set_code,
          chosen.set_name,
          chosen.collector_number,
          chosen.language,
          chosen.rarity,
          chosen.exact_variant,
          chosen.variant_family,
          chosen.primary_image_url,
          pc.variant_count,
          pc.score,
          pc.total_cards,
          pc.total_prints
        FROM paged_cards pc
        JOIN LATERAL (
          SELECT
            p.id AS print_id,
            s.code AS set_code,
            s.name AS set_name,
            p.collector_number,
            p.language,
            CASE
              WHEN NULLIF(trim(p.rarity), '') IS NOT NULL
               AND lower(trim(p.rarity)) NOT IN ('unknown','n/a','na','none','null','undefined','-','?')
              THEN trim(p.rarity)
              ELSE (
                SELECT MIN(trim(p2.rarity))
                FROM prints p2
                WHERE p2.card_id = p.card_id
                  AND p2.set_id = p.set_id
                  AND COALESCE(trim(p2.collector_number), '') = COALESCE(trim(p.collector_number), '')
                  AND lower(COALESCE(trim(p2.language), '')) = lower(COALESCE(trim(p.language), ''))
                  AND NULLIF(trim(p2.rarity), '') IS NOT NULL
                  AND lower(trim(p2.rarity)) NOT IN ('unknown','n/a','na','none','null','undefined','-','?')
                HAVING COUNT(DISTINCT lower(trim(p2.rarity))) = 1
              )
            END AS rarity,
            psp.exact_variant,
            psp.variant_family,
            (
              SELECT pi.url
              FROM print_images pi
              WHERE pi.print_id = p.id
              ORDER BY pi.is_primary DESC, pi.id ASC
              LIMIT 1
            ) AS primary_image_url
          FROM prints p
          JOIN sets s ON s.id = p.set_id
          LEFT JOIN print_search_profiles psp ON psp.print_id = p.id
          WHERE p.card_id = pc.card_id
          ORDER BY
            CASE WHEN lower(COALESCE(p.variant, '')) IN ('default', 'base', '') THEN 0 ELSE 1 END,
            (psp.print_id IS NOT NULL) DESC,
            p.id ASC
          LIMIT 1
        ) chosen ON TRUE
        ORDER BY pc.score DESC, lower(pc.name) ASC, pc.card_id ASC
        """
    )


def _fallback_matched_cards_cte() -> str:
    # Runtime fallback is deliberately cold. Search profiles are the certified
    # production index; only when that index returns no card at all do we scan
    # canonical Cards to rescue a missing/stale projection. Keeping this UNION
    # out of the hot query removes a full canonical-card anti-scan from every
    # successful name search while preserving a fail-safe for projection drift.
    return """
        SELECT
          c.id AS card_id,
          COALESCE(csp.normalized_name, lower(c.name)) AS normalized_name,
          COALESCE(csp.attributes_json, '{}'::jsonb) AS attributes_json,
          CASE
            WHEN COALESCE(csp.normalized_name, lower(c.name)) = :q_norm THEN 5000.0
            WHEN COALESCE(csp.normalized_name, lower(c.name)) LIKE :prefix THEN 3000.0
            WHEN (' ' || COALESCE(csp.normalized_name, lower(c.name)) || ' ') LIKE :word THEN 2200.0
            ELSE 1400.0
          END AS score
        FROM cards c
        JOIN games g ON g.id = c.game_id
        LEFT JOIN card_search_profiles csp ON csp.card_id = c.id
        WHERE (:game = '' OR g.slug = :game)
          AND lower(c.name) LIKE :canonical_fallback
          AND NOT EXISTS (
            SELECT 1
            FROM card_search_profiles hit
            WHERE hit.card_id = c.id
              AND hit.normalized_name LIKE :contains
          )
    """


def exhaustive_name_page(
    session,
    *,
    query: str,
    game: str | None,
    limit: int,
    offset: int,
) -> dict:
    """Page canonical-name matches from the certified Card search projection.

    The hot path reads only CardSearchProfile and hydrates representative Prints
    after LIMIT/OFFSET. A canonical-card rescue scan runs only when the indexed
    projection yields no result, rather than being UNIONed into every successful
    request. Missing representative rarity is resolved inline from strict sibling
    consensus so the API does not pay a second database round trip for Pikachu-
    style legacy rows.
    """
    q_norm = normalize_search_text(query)
    if not q_norm:
        return _empty(limit, offset)
    if session.bind.dialect.name != "postgresql":
        return _sqlite_page(session, query=query, game=game, limit=limit, offset=offset)

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

    profile_matched_cards_cte = """
        SELECT
          csp.card_id,
          csp.normalized_name,
          COALESCE(csp.attributes_json, '{}'::jsonb) AS attributes_json,
          CASE
            WHEN csp.normalized_name = :q_norm THEN 5000.0
            WHEN csp.normalized_name LIKE :prefix THEN 3000.0
            WHEN (' ' || csp.normalized_name || ' ') LIKE :word THEN 2200.0
            ELSE 1500.0
          END AS score
        FROM card_search_profiles csp
        JOIN games g ON g.id = csp.game_id
        WHERE (:game = '' OR g.slug = :game)
          AND csp.normalized_name LIKE :contains
    """

    rows = session.execute(_page_sql(profile_matched_cards_cte), params).mappings().all()

    if not rows and offset == 0:
        rows = session.execute(_page_sql(_fallback_matched_cards_cte()), params).mappings().all()

    if not rows:
        if offset == 0:
            return _empty(limit, offset)

        count_sql = text(
            f"""
            WITH matched_cards AS MATERIALIZED (
              {profile_matched_cards_cte}
            )
            SELECT
              COUNT(*)::bigint AS total_cards,
              COALESCE(SUM((
                SELECT COUNT(*)::bigint
                FROM prints pv
                WHERE pv.card_id = mc.card_id
              )), 0)::bigint AS total_prints
            FROM matched_cards mc
            """
        )
        counts = session.execute(count_sql, params).mappings().one()
        total = int(counts["total_cards"] or 0)
        total_prints = int(counts["total_prints"] or 0)
        if total == 0:
            return _empty(limit, offset)
        return {
            "items": [],
            "total": total,
            "total_prints": total_prints,
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "next_offset": None,
        }

    total = int(rows[0]["total_cards"] or 0)
    total_prints = int(rows[0]["total_prints"] or 0)
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
