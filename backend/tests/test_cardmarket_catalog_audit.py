from __future__ import annotations

import gzip

from sqlalchemy import select

from app import db
from app.jobs.cardmarket_catalog_audit import (
    ProductListRow,
    audit_product_list,
    infer_game_from_category,
    load_product_list_bytes,
)
from app.models import Card, Game, Print, PrintIdentifier, Set


def _seed_set(session, *, game_slug="onepiece", set_code="OP05"):
    game = Game(slug=game_slug, name=game_slug.upper())
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code=set_code, name=f"Set {set_code}")
    session.add(set_row)
    session.flush()
    return game, set_row


def _seed_print(session, *, game, set_row, name, number, variant="default", language="en"):
    card = Card(game_id=game.id, name=name, card_key=f"{game.slug}:{number}:{variant}:{language}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language=language,
        is_foil=False,
        variant=variant,
        print_key=f"{game.slug}:{set_row.code}:{number}:{variant}:{language}",
    )
    session.add(print_row)
    session.flush()
    return card, print_row


def test_documented_product_list_csv_and_gzip_parse():
    csv_bytes = (
        '"idProduct","Name","Category ID","Category","Expansion ID","Date Added"\n'
        '"123","Monkey D. Luffy","99","One Piece Card Game Single","456","2026-08-01"\n'
    ).encode()
    rows = load_product_list_bytes(gzip.compress(csv_bytes))
    assert len(rows) == 1
    assert rows[0].product_id == "123"
    assert rows[0].name == "Monkey D. Luffy"
    assert rows[0].expansion_id == "456"
    assert infer_game_from_category(rows[0].category) == "onepiece"


def test_unique_name_and_crosswalk_produces_review_candidate_but_no_write(client):
    product = ProductListRow("123", "Monkey D. Luffy", "99", "One Piece Card Game Single", "456")
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session)
        card, print_row = _seed_print(session, game=game, set_row=set_row, name="Monkey D. Luffy", number="OP05-060")
        session.commit()

        before = session.execute(select(PrintIdentifier)).scalars().all()
        summary, decisions = audit_product_list(session, [product], {"456": {"game": "onepiece", "set_code": "OP05"}})
        after = session.execute(select(PrintIdentifier)).scalars().all()

        assert before == [] and after == []
        assert summary["write_mode"] == "disabled"
        assert summary["exact_candidates_review_required"] == 1
        decision = decisions[0]
        assert decision.status == "exact_candidate_review_required"
        assert decision.print_id == print_row.id
        assert decision.card_id == card.id
        assert decision.evidence["name_match"] == "normalized_exact"


def test_multiple_physical_prints_are_ambiguity_not_candidate(client):
    product = ProductListRow("999", "Nami", "99", "One Piece Card Game Single", "777")
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, set_code="OP08")
        _seed_print(session, game=game, set_row=set_row, name="Nami", number="OP08-106", variant="standard")
        _seed_print(session, game=game, set_row=set_row, name="Nami", number="OP08-106", variant="alternate-art")
        session.commit()

        summary, decisions = audit_product_list(session, [product], {"777": {"game": "onepiece", "set_code": "OP08"}})
        assert summary["physical_ambiguity"] == 1
        assert summary["exact_candidates_review_required"] == 0
        assert decisions[0].status == "physical_ambiguity"
        assert decisions[0].evidence["candidate_count"] == 2


def test_existing_external_id_conflict_is_never_review_candidate(client):
    product = ProductListRow("321", "Luffy", "99", "One Piece Card Game Single", "456")
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session)
        _, expected = _seed_print(session, game=game, set_row=set_row, name="Luffy", number="OP05-001")
        _, other = _seed_print(session, game=game, set_row=set_row, name="Other", number="OP05-002")
        session.add(PrintIdentifier(print_id=other.id, source="cardmarket", external_id="321"))
        session.commit()

        summary, decisions = audit_product_list(session, [product], {"456": {"game": "onepiece", "set_code": "OP05"}})
        assert summary["exact_candidates_review_required"] == 0
        assert decisions[0].status == "external_id_conflict"
        assert decisions[0].print_id == expected.id


def test_missing_expansion_crosswalk_is_explicit_blocker(client):
    product = ProductListRow("555", "Pikachu", "1", "Pokemon Single", "888")
    with db.SessionLocal() as session:
        summary, decisions = audit_product_list(session, [product], {})
        assert summary["missing_expansion_crosswalk"] == 1
        assert decisions[0].status == "missing_expansion_crosswalk"
