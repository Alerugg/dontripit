from __future__ import annotations

from sqlalchemy import select

from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.normalization import (
    compact_search_text,
    normalize_onepiece_collector_number,
)
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
    """Return every exact physical One Piece print for a pure collector-code query.

    Normal/name search intentionally groups variants under a logical Card. A pure
    collector-code search is different: collectors are asking for the physical
    editions that carry that code, so collapsing by ``card_id`` hides legitimate
    official artworks/reprints (for example OP05-119).

    ``None`` means the query is not a pure One Piece collector-code lookup and
    the regular logical-card search should continue. An empty list is a valid
    exact-code result with no matching physical print.
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
    stmt = (
        select(PrintSearchProfile, Print, Card, Set, Game)
        .join(Print, Print.id == PrintSearchProfile.print_id)
        .join(Card, Card.id == PrintSearchProfile.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == PrintSearchProfile.game_id)
        .where(
            Game.slug == "onepiece",
            PrintSearchProfile.normalized_collector_number == collector,
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
