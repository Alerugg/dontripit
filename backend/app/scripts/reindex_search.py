from __future__ import annotations

import re

from sqlalchemy import delete, select

from app import db
from app.ingest.normalization import normalize_collector_number
from app.models import Card, Game, Print, PrintImage, SearchDocument, Set


_ONEPIECE_OFFICIAL_HOST = "en.onepiece-cardgame.com"
_ONEPIECE_LEGACY_FAKE_HOST = "example.cdn.onepiece"
_PLACEHOLDER_HOST = "placehold.co"


def _is_onepiece_official_image(url: str | None) -> bool:
    return _ONEPIECE_OFFICIAL_HOST in str(url or "").strip().lower()


def _is_placeholder_or_fake_image(url: str | None) -> bool:
    lowered = str(url or "").strip().lower()
    return _PLACEHOLDER_HOST in lowered or _ONEPIECE_LEGACY_FAKE_HOST in lowered


def _image_quality_token(url: str | None) -> str:
    if _is_onepiece_official_image(url):
        return "imgq_official"
    if _is_placeholder_or_fake_image(url):
        return "imgq_placeholder"
    return "imgq_generic"


def rebuild_search_documents(session, card_ids: set[int] | None = None, set_ids: set[int] | None = None, print_ids: set[int] | None = None) -> dict[str, int]:
    stats = {"cards": 0, "sets": 0, "prints": 0}

    if card_ids is None and set_ids is None and print_ids is None:
        session.execute(delete(SearchDocument))

    existing_cards = _existing_docs_by_object_id(session, "card", card_ids)
    existing_sets = _existing_docs_by_object_id(session, "set", set_ids)
    existing_prints = _existing_docs_by_object_id(session, "print", print_ids)

    card_query = (
        select(Card.id, Card.game_id, Card.name)
        if card_ids is None
        else select(Card.id, Card.game_id, Card.name).where(Card.id.in_(card_ids))
    )
    for card_id, game_id, name in session.execute(card_query).all():
        _upsert_doc(
            session,
            "card",
            card_id,
            game_id,
            name,
            None,
            existing=existing_cards.get(card_id),
        )
        stats["cards"] += 1

    set_query = (
        select(Set.id, Set.game_id, Set.name, Set.code)
        if set_ids is None
        else select(Set.id, Set.game_id, Set.name, Set.code).where(Set.id.in_(set_ids))
    )
    for set_id, game_id, name, code in session.execute(set_query).all():
        _upsert_doc(
            session,
            "set",
            set_id,
            game_id,
            name,
            code,
            existing=existing_sets.get(set_id),
        )
        stats["sets"] += 1

    print_query = (
        select(
            Print.id,
            Card.game_id,
            Game.slug,
            Card.id,
            Card.name,
            Set.code,
            Print.collector_number,
            (Set.code + " #" + Print.collector_number).label("subtitle"),
            Set.name,
            Print.variant,
            Print.language,
        )
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
        .join(Game, Game.id == Card.game_id)
    )
    if print_ids is not None:
        print_query = print_query.where(Print.id.in_(print_ids))

    print_rows = session.execute(print_query).all()

    primary_images = {}
    image_query = (
        select(
            Print.id,
            PrintImage.url,
            PrintImage.is_primary,
            PrintImage.id,
        )
        .join(PrintImage, PrintImage.print_id == Print.id)
    )
    if print_ids is not None:
        image_query = image_query.where(Print.id.in_(print_ids))
    image_rows = session.execute(image_query).all()
    for print_id, url, is_primary, image_id in image_rows:
        current = primary_images.get(print_id)
        candidate = (0 if _is_onepiece_official_image(url) else 2 if _is_placeholder_or_fake_image(url) else 1, 0 if is_primary else 1, image_id, url)
        if current is None or candidate < current:
            primary_images[print_id] = candidate

    onepiece_official_keys: dict[tuple[int, str, str], list[int]] = {}
    for row in print_rows:
        print_id, _game_id, game_slug, card_id, _title, set_code, collector_number, _subtitle, _set_name, _variant, _language = row
        url = (primary_images.get(print_id) or (None, None, None, None))[3]
        if game_slug != "onepiece" or not _is_onepiece_official_image(url):
            continue
        key = (card_id, str(set_code or "").strip().lower(), normalize_collector_number(collector_number))
        onepiece_official_keys.setdefault(key, []).append(print_id)

    for (
        print_id,
        game_id,
        game_slug,
        card_id,
        title,
        set_code,
        collector_number,
        subtitle,
        set_name,
        _variant,
        _language,
    ) in print_rows:
        primary_image_url = (primary_images.get(print_id) or (None, None, None, None))[3]
        image_quality = _image_quality_token(primary_image_url)
        should_exclude = False
        if game_slug == "onepiece" and _is_placeholder_or_fake_image(primary_image_url):
            key = (card_id, str(set_code or "").strip().lower(), normalize_collector_number(collector_number))
            alternatives = [candidate_id for candidate_id in onepiece_official_keys.get(key, []) if candidate_id != print_id]
            should_exclude = bool(alternatives)

        if should_exclude:
            existing_doc = existing_prints.get(print_id)
            if existing_doc is not None:
                session.delete(existing_doc)
            continue

        doc_text = f"{title} {set_name} {set_code} {collector_number} {image_quality}".strip()
        _upsert_doc(
            session,
            "print",
            print_id,
            game_id,
            title,
            subtitle,
            doc_text,
            existing=existing_prints.get(print_id),
        )
        stats["prints"] += 1

    return stats


def _existing_docs_by_object_id(
    session,
    doc_type: str,
    object_ids: set[int] | None,
) -> dict[int, SearchDocument]:
    query = select(SearchDocument).where(SearchDocument.doc_type == doc_type)
    if object_ids is not None:
        if not object_ids:
            return {}
        query = query.where(SearchDocument.object_id.in_(object_ids))
    return {row.object_id: row for row in session.execute(query).scalars().all()}


def _upsert_doc(
    session,
    doc_type: str,
    object_id: int,
    game_id: int,
    title: str,
    subtitle: str | None,
    doc_text: str | None = None,
    *,
    existing: SearchDocument | None = None,
) -> None:
    value = doc_text or " ".join(part for part in [title, subtitle] if part)
    value = re.sub(r"\s+", " ", value).strip()
    if existing is None:
        session.add(SearchDocument(doc_type=doc_type, object_id=object_id, game_id=game_id, title=title, subtitle=subtitle, tsv=value))
    else:
        existing.game_id = game_id
        existing.title = title
        existing.subtitle = subtitle
        existing.tsv = value


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        stats = rebuild_search_documents(session)
        session.commit()
    print(f"reindex complete cards={stats['cards']} sets={stats['sets']} prints={stats['prints']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
