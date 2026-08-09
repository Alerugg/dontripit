from __future__ import annotations

from sqlalchemy import select

from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.normalization import compact_search_text, normalize_onepiece_collector_number
from app.search_v2_models import PrintSearchProfile


def _bounded_limit(value: int | None, *, default: int = 24, maximum: int = 100) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def exact_onepiece_collector_search(
    session,
    *,
    query: str,
    game: str | None,
    limit: int = 24,
) -> list[dict] | None:
    """Return every exact physical edition for a pure One Piece collector code.

    One Piece's certified canonical identity is collector-number based, so the
    logical gameplay identity for OP05-119 is ``onepiece:op05-119``. That logical
    Card can legitimately have many distinct official physical editions/artworks.
    Normal/name search may group them for usability, but a pure collector-code
    lookup must not collapse those editions into a single result.

    The canonical ``card_key`` is deliberately the lookup anchor here rather than
    a rebuildable Search V2 normalization field. Search projections can change;
    the certified Card identity is the durable source of truth.

    ``None`` means the query is not a pure One Piece collector-code lookup and
    regular logical-card search should continue. An empty list is a valid exact
    code lookup with no canonical match.
    """
    normalized_game = str(game or "").strip().lower() or None
    if normalized_game not in {None, "onepiece"}:
        return None

    raw_query = str(query or "").strip()
    collector = normalize_onepiece_collector_number(raw_query)
    if not collector:
        return None
    if compact_search_text(raw_query) != compact_search_text(collector):
        return None

    row_limit = _bounded_limit(limit)
    canonical_card_key = f"onepiece:{collector}"

    stmt = (
        select(PrintSearchProfile, Print, Card, Set, Game)
        .join(Print, Print.id == PrintSearchProfile.print_id)
        .join(Card, Card.id == PrintSearchProfile.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == PrintSearchProfile.game_id)
        .where(
            Game.slug == "onepiece",
            Card.card_key == canonical_card_key,
        )
        .order_by(
            Set.release_date.asc().nullslast(),
            Set.code.asc(),
            PrintSearchProfile.exact_variant.asc().nullslast(),
            Print.id.asc(),
        )
        .limit(row_limit)
    )

    results: list[dict] = []
    for profile, print_row, card, set_row, game_row in session.execute(stmt).all():
        image_url = session.execute(
            select(PrintImage.url)
            .where(PrintImage.print_id == print_row.id)
            .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        results.append(
            {
                "type": "print",
                "print_id": print_row.id,
                "print_key": print_row.print_key,
                "card_id": card.id,
                "card_key": card.card_key,
                "name": card.name,
                "game": game_row.slug,
                "set_code": set_row.code,
                "set_name": set_row.name,
                "collector_number": print_row.collector_number,
                "language": print_row.language,
                "rarity": print_row.rarity,
                "exact_variant": profile.exact_variant,
                "variant_family": profile.variant_family,
                "primary_image_url": image_url,
                "releases": profile.release_names_json or [],
                "attributes": profile.attributes_json or {},
                "score": 1.0,
            }
        )
    return results
