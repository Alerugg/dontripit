from __future__ import annotations

import json

from app import db
from app.jobs.cardmarket_master_inventory import (
    CatalogFeed,
    build_master_inventory,
    load_catalog_feed_bytes,
)
from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.models import (
    Card,
    Game,
    Print,
    PrintIdentifier,
    Product,
    ProductIdentifier,
    ProductVariant,
    Set,
)


def _game(session, slug: str):
    row = session.query(Game).filter(Game.slug == slug).one_or_none()
    if row is None:
        row = Game(slug=slug, name=slug.upper())
        session.add(row)
        session.flush()
    return row


def _single(session, *, game_slug="pokemon", set_code="TEST", name="Pikachu", number="001"):
    game = _game(session, game_slug)
    set_row = Set(game_id=game.id, code=set_code, name=f"Set {set_code}")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name=name, card_key=f"{game_slug}:{set_code}:{number}:{name}")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number=number,
        language="en",
        is_foil=False,
        variant="default",
        print_key=f"{game_slug}:{set_code}:{number}:en:default",
    )
    session.add(print_row)
    session.flush()
    return game, set_row, card, print_row


def _sealed(session, *, game_slug="pokemon", name="Booster Box"):
    game = _game(session, game_slug)
    product = Product(game_id=game.id, set_id=None, product_type="booster_box", name=name)
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        language="en",
        region="eu",
        packaging="sealed",
    )
    session.add(variant)
    session.flush()
    return game, product, variant


def _feed(game: str, group: str, *rows: ProductListRow) -> CatalogFeed:
    return CatalogFeed(
        game_slug=game,
        product_group=group,
        rows=tuple(rows),
        created_at=None,
        raw_records=len(rows),
        rejected_records=0,
    )


def test_master_loader_keeps_nonsingle_without_expansion_and_accounts_for_rejections():
    payload = {
        "createdAt": "2026-08-09T20:00:00+0200",
        "products": [
            {"idProduct": 100, "name": "Elite Trainer Box", "categoryName": "Pokemon Elite Trainer Boxes"},
            {"idProduct": 101, "categoryName": "Pokemon Booster"},
            "not-an-object",
        ],
    }
    feed = load_catalog_feed_bytes(
        json.dumps(payload).encode(),
        game_slug="pokemon",
        product_group="non_single",
    )
    assert feed.raw_records == 3
    assert len(feed.rows) == 1
    assert feed.rejected_records == 2
    assert feed.rows[0].product_id == "100"
    assert feed.rows[0].expansion_id == ""
    assert feed.created_at is not None


def test_mapped_single_and_sealed_make_game_ready(client):
    single_row = ProductListRow("1001", "Pikachu", "51", "Pokemon Single", "11")
    sealed_row = ProductListRow("2001", "Test Booster Box", "53", "Pokemon Display", "11")
    with db.SessionLocal() as session:
        _, _, _, print_row = _single(session)
        _, _, variant = _sealed(session, name="Test Booster Box")
        session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id="1001"))
        session.add(ProductIdentifier(product_variant_id=variant.id, source="cardmarket", external_id="2001"))
        session.commit()

        summary, decisions = build_master_inventory(
            session,
            [_feed("pokemon", "single", single_row), _feed("pokemon", "non_single", sealed_row)],
        )
        session.rollback()

    assert summary["raw_records"] == 2
    assert summary["accepted_records"] == 2
    assert summary["classified_records"] == 2
    assert summary["rejected_records"] == 0
    assert summary["unclassified_records"] == 0
    assert summary["lost_records"] == 0
    assert summary["mapped"] == 2
    assert summary["unresolved"] == 0
    assert summary["ready"] is True
    assert summary["games"]["pokemon"]["ready"] is True
    assert {item.status for item in decisions} == {"mapped_print", "mapped_product_variant"}


def test_single_mapped_to_product_side_is_explicit_wrong_entity(client):
    row = ProductListRow("3001", "Pikachu", "51", "Pokemon Single", "11")
    with db.SessionLocal() as session:
        _, _, variant = _sealed(session)
        session.add(ProductIdentifier(product_variant_id=variant.id, source="cardmarket", external_id="3001"))
        session.commit()

        summary, decisions = build_master_inventory(session, [_feed("pokemon", "single", row)])

    assert decisions[0].status == "wrong_entity_mapping"
    assert decisions[0].entity_type == "product_variant"
    assert summary["conflicts"] == 1
    assert summary["ready"] is False


def test_nonsingle_mapped_to_print_side_is_explicit_wrong_entity(client):
    row = ProductListRow("3002", "Booster Box", "53", "Pokemon Display", "11")
    with db.SessionLocal() as session:
        _, _, _, print_row = _single(session)
        session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id="3002"))
        session.commit()

        summary, decisions = build_master_inventory(session, [_feed("pokemon", "non_single", row)])

    assert decisions[0].status == "wrong_entity_mapping"
    assert decisions[0].entity_type == "print"
    assert summary["conflicts"] == 1


def test_cross_game_identifier_is_never_counted_as_mapped(client):
    row = ProductListRow("4001", "Pikachu", "51", "Pokemon Single", "11")
    with db.SessionLocal() as session:
        _, _, _, print_row = _single(session, game_slug="onepiece", set_code="OPX", name="Wrong", number="001")
        session.add(PrintIdentifier(print_id=print_row.id, source="cardmarket", external_id="4001"))
        session.commit()

        summary, decisions = build_master_inventory(session, [_feed("pokemon", "single", row)])

    assert decisions[0].status == "cross_game_identifier"
    assert summary["mapped"] == 0
    assert summary["conflicts"] == 1
    assert summary["ready"] is False


def test_duplicate_cardmarket_id_across_feeds_is_a_hard_conflict(client):
    single = ProductListRow("5001", "Pikachu", "51", "Pokemon Single", "11")
    sealed = ProductListRow("5001", "Pikachu Collection", "53", "Pokemon Display", "11")
    with db.SessionLocal() as session:
        summary, decisions = build_master_inventory(
            session,
            [_feed("pokemon", "single", single), _feed("pokemon", "non_single", sealed)],
        )

    assert len(decisions) == 2
    assert {item.status for item in decisions} == {"duplicate_catalog_product"}
    assert summary["conflicts"] == 2
    assert summary["lost_records"] == 0
    assert summary["ready"] is False


def test_unmapped_single_reuses_exact_mapping_auditor_evidence(client):
    row = ProductListRow("6001", "Monkey D. Luffy (OP05-060)", "1621", "One Piece Single", "900")
    with db.SessionLocal() as session:
        _, _, card, print_row = _single(
            session,
            game_slug="onepiece",
            set_code="OP05",
            name="Monkey D. Luffy",
            number="OP05-060",
        )
        session.commit()

        summary, decisions = build_master_inventory(
            session,
            [_feed("onepiece", "single", row)],
            crosswalks={"onepiece": {"900": {"game": "onepiece", "set_code": "OP05"}}},
        )

    decision = decisions[0]
    assert decision.status == "exact_candidate_review_required"
    assert decision.entity_type == "print"
    assert decision.entity_id == print_row.id
    assert decision.evidence["candidate_card_id"] == card.id
    assert summary["mapped"] == 0
    assert summary["unresolved"] == 1
    assert summary["ready"] is False


def test_every_accepted_row_gets_a_decision_and_no_row_is_lost(client):
    payload = {
        "products": [
            {"idProduct": 7001, "name": "A", "categoryName": "Pokemon Single", "idExpansion": 1},
            {"idProduct": 7002, "name": "B", "categoryName": "Pokemon Single", "idExpansion": 1},
        ]
    }
    feed = load_catalog_feed_bytes(json.dumps(payload).encode(), game_slug="pokemon", product_group="single")
    with db.SessionLocal() as session:
        summary, decisions = build_master_inventory(session, [feed])

    assert len(decisions) == 2
    assert summary["accepted_records"] == 2
    assert summary["classified_records"] == 2
    assert summary["unclassified_records"] == 0
    assert summary["lost_records"] == 0
    assert summary["ready"] is False
