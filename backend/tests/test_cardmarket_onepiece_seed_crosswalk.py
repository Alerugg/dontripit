from __future__ import annotations

from app import db
from app.jobs.cardmarket_catalog_audit import ProductListRow
from app.jobs.cardmarket_onepiece_seed_crosswalk import derive_onepiece_seed_crosswalk
from app.models import Game, Set


def _seed_sets(session, codes):
    game = Game(slug="onepiece", name="One Piece")
    session.add(game)
    session.flush()
    for code in codes:
        session.add(Set(game_id=game.id, code=code, name=code))
    session.commit()


def _product(pid, expansion, name):
    return ProductListRow(str(pid), name, "18", "One Piece Card Game Single", str(expansion))


def test_consistent_collector_prefix_is_reviewable_only(client):
    with db.SessionLocal() as session:
        _seed_sets(session, ["OP01", "OP02"])
        rows = [
            _product(1, 100, "Luffy (OP01-001)"),
            _product(2, 100, "Zoro (OP01-002)"),
            _product(3, 100, "Nami (OP01-003)"),
            _product(4, 100, "Sanji (OP01-004)"),
            _product(5, 100, "Usopp (OP01-005)"),
        ]
        summary, decisions, proposals = derive_onepiece_seed_crosswalk(session, rows)
        assert summary["write_mode"] == "disabled"
        assert summary["reviewable_proposals"] == 1
        assert decisions[0].status == "reviewable_collector_consensus"
        assert decisions[0].proposed_set_code == "OP01"
        assert decisions[0].consensus == 1.0
        assert proposals["100"]["evidence"]["review_required"] is True


def test_mixed_reprint_prefixes_never_become_reviewable_consensus(client):
    with db.SessionLocal() as session:
        _seed_sets(session, ["OP01", "OP02", "PRB01"])
        rows = [
            _product(1, 200, "A (OP01-001)"),
            _product(2, 200, "B (OP01-002)"),
            _product(3, 200, "C (OP02-001)"),
            _product(4, 200, "D (OP02-002)"),
            _product(5, 200, "E (PRB01-001)"),
        ]
        summary, decisions, proposals = derive_onepiece_seed_crosswalk(session, rows, min_consensus=0.9)
        assert decisions[0].status == "mixed_prefixes"
        assert proposals == {}
        assert summary["reviewable_proposals"] == 0


def test_strong_dominant_but_mixed_prefix_is_flagged_not_proposed(client):
    with db.SessionLocal() as session:
        _seed_sets(session, ["OP01", "OP02"])
        rows = [
            *[_product(i, 300, f"A{i} (OP01-{i:03d})") for i in range(1, 10)],
            _product(10, 300, "Other (OP02-001)"),
        ]
        summary, decisions, proposals = derive_onepiece_seed_crosswalk(session, rows, min_consensus=0.9)
        assert decisions[0].status == "mixed_prefixes_dominant"
        assert decisions[0].consensus == 0.9
        assert proposals == {}


def test_parentheses_without_collector_digits_are_ignored(client):
    with db.SessionLocal() as session:
        _seed_sets(session, ["OP01"])
        rows = [
            _product(1, 400, "Character (Parallel)"),
            _product(2, 400, "Character Two (Alt Art)"),
            _product(3, 400, "Character Three"),
        ]
        summary, decisions, proposals = derive_onepiece_seed_crosswalk(session, rows)
        assert decisions[0].status == "insufficient_collector_hints"
        assert decisions[0].hinted_rows == 0
        assert proposals == {}


def test_unknown_collector_prefix_does_not_match_internal_set(client):
    with db.SessionLocal() as session:
        _seed_sets(session, ["OP01"])
        rows = [
            _product(1, 500, "A (XYZ01-001)"),
            _product(2, 500, "B (XYZ01-002)"),
            _product(3, 500, "C (XYZ01-003)"),
            _product(4, 500, "D (XYZ01-004)"),
            _product(5, 500, "E (XYZ01-005)"),
        ]
        summary, decisions, proposals = derive_onepiece_seed_crosswalk(session, rows)
        assert decisions[0].status == "insufficient_internal_set_matches"
        assert decisions[0].unmatched_hints == 5
        assert proposals == {}
