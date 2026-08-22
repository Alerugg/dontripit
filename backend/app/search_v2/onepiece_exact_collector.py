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

    Physical ``Print`` rows anchored by the durable canonical ``Card.card_key``
    are the source of truth. ``PrintSearchProfile`` is joined only as optional
    enrichment, so a newly ingested physical print is immediately discoverable
    even if the rebuildable Search V2 projection has not caught up yet.

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
        select(Print, Card, Set, Game, PrintSearchProfile)
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Card.game_id)
        .outerjoin(PrintSearchProfile, PrintSearchProfile.print_id == Print.id)
        .where(
            Game.slug == "onepiece",
            Card.card_key == canonical_card_key,
        )
        .order_by(
            Set.release_date.asc().nullslast(),
            Set.code.asc(),
            Print.variant.asc().nullslast(),
            Print.id.asc(),
        )
        .limit(row_limit)
    )

    results: list[dict] = []
    for print_row, card, set_row, game_row, profile in session.execute(stmt).all():
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
                "exact_variant": (
                    profile.exact_variant
                    if profile is not None and profile.exact_variant
                    else print_row.variant
                ),
                "variant_family": (
                    profile.variant_family
                    if profile is not None and profile.variant_family
                    else ("default" if str(print_row.variant or "default") == "default" else print_row.variant)
                ),
                "primary_image_url": image_url,
                "releases": profile.release_names_json or [] if profile is not None else [],
                "attributes": profile.attributes_json or {} if profile is not None else {},
                "score": 1.0,
            }
        )
    return results
