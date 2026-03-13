from __future__ import annotations

import re

from sqlalchemy import delete, select

from app import db
from app.models import Card, Print, SearchDocument, Set


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
            Card.name,
            (Set.code + " #" + Print.collector_number).label("subtitle"),
            (Card.name + " " + Set.name + " " + Set.code + " " + Print.collector_number).label("doc_text"),
        )
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
    )
    if print_ids is not None:
        print_query = print_query.where(Print.id.in_(print_ids))

    for print_id, game_id, title, subtitle, doc_text in session.execute(print_query).all():
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
