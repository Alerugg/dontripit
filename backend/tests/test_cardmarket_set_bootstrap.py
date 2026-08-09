from __future__ import annotations

from app import db
from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.jobs.cardmarket_set_bootstrap import (
    bootstrap_expansion_set_families,
    market_identity_hints,
)
from app.models import Card, Game, Print, Set


def _game(session, slug: str):
    game = Game(slug=slug, name=slug.upper())
    session.add(game)
    session.flush()
    return game


def _set(session, game, code: str, names_and_numbers: list[tuple[str, str]]):
    row = Set(game_id=game.id, code=code, name=code)
    session.add(row)
    session.flush()
    for idx, (name, collector) in enumerate(names_and_numbers):
        card = Card(game_id=game.id, name=name, card_key=f"{game.slug}:{code}:{idx}:{name}")
        session.add(card)
        session.flush()
        session.add(Print(
            set_id=row.id,
            card_id=card.id,
            collector_number=collector,
            language="en",
            is_foil=False,
            variant="default",
            print_key=f"{game.slug}:{code}:{idx}:en:default",
        ))
    session.flush()
    return row


def _cm(product_id: int, name: str, expansion: str, category: str = "Magic Single"):
    return ProductListRow(str(product_id), name, "1", category, expansion)


def test_onepiece_name_parser_keeps_collector_hint():
    base, collector = market_identity_hints("onepiece", "Roronoa Zoro (OP01-001)")
    assert base == "Roronoa Zoro"
    assert collector == "OP01-001"


def test_pokemon_name_parser_removes_attack_disambiguator_only():
    base, collector = market_identity_hints("pokemon", "Pikachu [Thunder Wave | Spark]")
    assert base == "Pikachu"
    assert collector is None


def test_bootstrap_finds_unique_set_without_existing_cardmarket_ids(client):
    with db.SessionLocal() as session:
        game = _game(session, "mtg")
        _set(session, game, "AAA", [("Alpha", "1"), ("Beta", "2"), ("Gamma", "3"), ("Delta", "4")])
        _set(session, game, "BBB", [("Other", "1"), ("Else", "2"), ("Different", "3")])
        session.commit()

        rows = [
            _cm(1, "Alpha", "900"),
            _cm(2, "Beta", "900"),
            _cm(3, "Gamma", "900"),
            _cm(4, "Delta", "900"),
        ]
        summary, decisions, proposals = bootstrap_expansion_set_families(session, rows, game_slug="mtg")

    assert summary["reviewable_unique_set"] == 1
    assert decisions[0].status == "reviewable_unique_set"
    assert decisions[0].set_codes == ("AAA",)
    assert proposals["900"]["set_codes"] == ["AAA"]


def test_bootstrap_can_propose_split_set_family(client):
    with db.SessionLocal() as session:
        game = _game(session, "pokemon")
        _set(session, game, "MAIN", [("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")])
        _set(session, game, "GALLERY", [("E", "GG1"), ("F", "GG2"), ("G", "GG3"), ("H", "GG4")])
        _set(session, game, "NOISE", [("A", "x"), ("Z", "y"), ("Y", "z")])
        session.commit()

        rows = [
            _cm(10, "A [Move]", "901", "Pokemon Single"),
            _cm(11, "B [Move]", "901", "Pokemon Single"),
            _cm(12, "C [Move]", "901", "Pokemon Single"),
            _cm(13, "D [Move]", "901", "Pokemon Single"),
            _cm(14, "E [Move]", "901", "Pokemon Single"),
            _cm(15, "F [Move]", "901", "Pokemon Single"),
            _cm(16, "G [Move]", "901", "Pokemon Single"),
            _cm(17, "H [Move]", "901", "Pokemon Single"),
        ]
        summary, decisions, proposals = bootstrap_expansion_set_families(session, rows, game_slug="pokemon")

    assert summary["reviewable_set_family"] == 1
    assert decisions[0].status == "reviewable_set_family"
    assert set(decisions[0].set_codes) == {"MAIN", "GALLERY"}
    assert set(proposals["901"]["set_codes"]) == {"MAIN", "GALLERY"}


def test_bootstrap_onepiece_collector_pairs_disambiguate_same_names(client):
    with db.SessionLocal() as session:
        game = _game(session, "onepiece")
        _set(session, game, "OP01", [
            ("Roronoa Zoro", "OP01-001"),
            ("Trafalgar Law", "OP01-002"),
            ("Monkey.D.Luffy", "OP01-003"),
        ])
        _set(session, game, "OTHER", [
            ("Roronoa Zoro", "OP99-001"),
            ("Trafalgar Law", "OP99-002"),
            ("Monkey.D.Luffy", "OP99-003"),
        ])
        session.commit()

        rows = [
            _cm(20, "Roronoa Zoro (OP01-001)", "902", "One Piece Single"),
            _cm(21, "Trafalgar Law (OP01-002)", "902", "One Piece Single"),
            _cm(22, "Monkey.D.Luffy (OP01-003)", "902", "One Piece Single"),
        ]
        summary, decisions, proposals = bootstrap_expansion_set_families(session, rows, game_slug="onepiece")

    assert summary["reviewable_unique_set"] == 1
    assert decisions[0].set_codes == ("OP01",)
    assert proposals["902"]["set_codes"] == ["OP01"]


def test_bootstrap_does_not_promote_generic_weak_overlap(client):
    with db.SessionLocal() as session:
        game = _game(session, "pokemon")
        _set(session, game, "ENERGY", [
            ("Fire Energy", "1"),
            ("Water Energy", "2"),
            ("Grass Energy", "3"),
            ("Lightning Energy", "4"),
        ])
        session.commit()

        rows = [
            _cm(30, "Fire Energy [Basic]", "903", "Pokemon Single"),
            _cm(31, "Totally Missing A [Move]", "903", "Pokemon Single"),
            _cm(32, "Totally Missing B [Move]", "903", "Pokemon Single"),
            _cm(33, "Totally Missing C [Move]", "903", "Pokemon Single"),
        ]
        summary, decisions, proposals = bootstrap_expansion_set_families(session, rows, game_slug="pokemon")

    assert decisions[0].status in {"weak_overlap", "catalog_source_gap"}
    assert "903" not in proposals


def test_duplicate_onepiece_products_do_not_create_fake_physical_resolution(client):
    with db.SessionLocal() as session:
        game = _game(session, "onepiece")
        _set(session, game, "OP01", [
            ("Roronoa Zoro", "OP01-001"),
            ("Trafalgar Law", "OP01-002"),
            ("Monkey.D.Luffy", "OP01-003"),
        ])
        session.commit()

        rows = [
            _cm(40, "Roronoa Zoro (OP01-001)", "904", "One Piece Single"),
            _cm(41, "Roronoa Zoro (OP01-001)", "904", "One Piece Single"),
            _cm(42, "Trafalgar Law (OP01-002)", "904", "One Piece Single"),
            _cm(43, "Monkey.D.Luffy (OP01-003)", "904", "One Piece Single"),
        ]
        _, decisions, proposals = bootstrap_expansion_set_families(session, rows, game_slug="onepiece")

    assert decisions[0].products == 4
    assert decisions[0].collector_pairs == 3
    assert proposals["904"]["set_codes"] == ["OP01"]
    # This module proposes an expansion/set family only. It deliberately does
    # not pretend the duplicate Cardmarket product IDs are the same Print.
    assert "print_id" not in proposals["904"]
