from __future__ import annotations

import gzip
import json

from sqlalchemy import select

from app import db
from app.jobs.cardmarket_catalog_audit import (
    ProductListRow,
    audit_product_list,
    infer_game_from_category,
    load_product_list_bytes,
    normalize_collector,
    split_product_name_hints,
)
from app.models import Card, Game, Print, PrintIdentifier, Set


def _seed_set(session, *, game_slug="onepiece", set_code="OP05"):
    game = session.query(Game).filter(Game.slug == game_slug).one_or_none()
    if game is None:
        game = Game(slug=game_slug, name=game_slug.upper())
        session.add(game)
        session.flush()
    set_row = Set(game_id=game.id, code=set_code, name=f"Set {set_code}")
    session.add(set_row)
    session.flush()
    return game, set_row


def _seed_print(session, *, game, set_row, name, number, variant="default", language="en"):
    card = Card(game_id=game.id, name=name, card_key=f"{game.slug}:{set_row.code}:{number}:{variant}:{language}")
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


def test_current_product_list_json_shape_parses_metacard_and_category():
    payload = {
        "version": 1,
        "createdAt": "2026-07-22T11:06:58+0200",
        "products": [
            {
                "idProduct": 690368,
                "name": "Roronoa Zoro (OP01-001)",
                "idCategory": 1621,
                "categoryName": "One Piece Single",
                "idExpansion": 5229,
                "idMetacard": 415369,
                "dateAdded": "2022-12-28 22:11:43",
            }
        ],
    }
    rows = load_product_list_bytes(json.dumps(payload).encode())
    assert len(rows) == 1
    row = rows[0]
    assert row.product_id == "690368"
    assert row.name == "Roronoa Zoro (OP01-001)"
    assert row.category == "One Piece Single"
    assert row.expansion_id == "5229"
    assert row.metacard_id == "415369"
    assert row.date_added == "2022-12-28 22:11:43"
    assert infer_game_from_category(row.category) == "onepiece"


def test_legacy_product_list_csv_and_gzip_still_parse():
    csv_bytes = (
        '"idProduct","Name","Category ID","Category","Expansion ID","Date Added"\n'
        '"123","Monkey D. Luffy","99","One Piece Card Game Single","456","2026-08-01"\n'
    ).encode()
    rows = load_product_list_bytes(gzip.compress(csv_bytes))
    assert len(rows) == 1
    assert rows[0].product_id == "123"
    assert rows[0].name == "Monkey D. Luffy"
    assert rows[0].expansion_id == "456"
    assert rows[0].metacard_id is None
    assert infer_game_from_category(rows[0].category) == "onepiece"


def test_terminal_numeric_parenthetical_becomes_collector_hint_only():
    assert split_product_name_hints("Roronoa Zoro (OP01-001)") == ("Roronoa Zoro", "OP01-001")
    assert split_product_name_hints("Pikachu (SV1 025/198)") == ("Pikachu", "SV1 025/198")
    assert split_product_name_hints("Nissa (Borderless)") == ("Nissa (Borderless)", None)
    assert normalize_collector("OP01-001") == "op01001"


def test_name_and_collector_hint_narrow_to_review_candidate_but_never_write(client):
    product = ProductListRow(
        "123",
        "Monkey D. Luffy (OP05-060)",
        "99",
        "One Piece Single",
        "456",
        metacard_id="9999",
    )
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session)
        _seed_print(session, game=game, set_row=set_row, name="Monkey D. Luffy", number="OP05-059")
        card, expected = _seed_print(session, game=game, set_row=set_row, name="Monkey D. Luffy", number="OP05-060")
        session.commit()

        before = session.execute(select(PrintIdentifier)).scalars().all()
        summary, decisions = audit_product_list(session, [product], {"456": {"game": "onepiece", "set_code": "OP05"}})
        after = session.execute(select(PrintIdentifier)).scalars().all()

        assert before == [] and after == []
        assert summary["write_mode"] == "disabled"
        assert summary["exact_candidates_review_required"] == 1
        decision = decisions[0]
        assert decision.status == "exact_candidate_review_required"
        assert decision.print_id == expected.id
        assert decision.card_id == card.id
        assert decision.evidence["base_name"] == "Monkey D. Luffy"
        assert decision.evidence["collector_hint"] == "OP05-060"
        assert decision.evidence["metacard_id"] == "9999"
        assert decision.evidence["collector_match"] is True


def test_same_name_and_collector_with_multiple_physical_variants_stays_ambiguous(client):
    product = ProductListRow("999", "Nami (OP08-106)", "99", "One Piece Single", "777", metacard_id="12345")
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
        assert decisions[0].evidence["metacard_id"] == "12345"


def test_two_cardmarket_products_same_metacard_are_independent_product_rows(client):
    products = [
        ProductListRow("901", "Nami (OP08-106)", "99", "One Piece Single", "777", metacard_id="555"),
        ProductListRow("902", "Nami (OP08-106)", "99", "One Piece Single", "777", metacard_id="555"),
    ]
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, set_code="OP08")
        _seed_print(session, game=game, set_row=set_row, name="Nami", number="OP08-106", variant="standard")
        _seed_print(session, game=game, set_row=set_row, name="Nami", number="OP08-106", variant="alternate-art")
        session.commit()

        summary, decisions = audit_product_list(session, products, {"777": {"game": "onepiece", "set_code": "OP08"}})
        assert summary["status_counts"].get("duplicate_product_id", 0) == 0
        assert summary["physical_ambiguity"] == 2
        assert {item.product_id for item in decisions} == {"901", "902"}


def test_collector_hint_mismatch_is_explicit_blocker(client):
    product = ProductListRow("888", "Nami (OP08-999)", "99", "One Piece Single", "777")
    with db.SessionLocal() as session:
        game, set_row = _seed_set(session, set_code="OP08")
        _seed_print(session, game=game, set_row=set_row, name="Nami", number="OP08-106")
        session.commit()

        summary, decisions = audit_product_list(session, [product], {"777": {"game": "onepiece", "set_code": "OP08"}})
        assert summary["collector_no_match"] == 1
        assert decisions[0].status == "collector_no_match"
        assert decisions[0].evidence["name_candidate_count"] == 1


def test_existing_external_id_conflict_is_never_review_candidate(client):
    product = ProductListRow("321", "Luffy (OP05-001)", "99", "One Piece Single", "456")
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
