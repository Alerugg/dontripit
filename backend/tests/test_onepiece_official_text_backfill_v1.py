from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, PrintIdentifier, Set
from app.multilingual_models import PrintLocalization
from app.scripts.apply_onepiece_official_text_v1 import build_plan


def _payload(*, external_id: str = "OP05-119_P1") -> dict:
    return {
        "language": "en",
        "cards": [
            {
                "id": "onepiece:op05-119",
                "name": "Monkey.D.Luffy",
                "prints": [
                    {
                        "id": external_id,
                        "collector_number": "OP05-119",
                        "variant": "p1",
                        "details": {
                            "cost": "10",
                            "power": "12000",
                            "effect": "[On Play] DON!! -10: Take an extra turn after this one.",
                            "trigger": "None",
                            "official": True,
                            "source": "onepiece_official",
                        },
                    }
                ],
            }
        ],
        "diagnostics": {"source_text_conflicts": []},
    }


def _seed_exact_target(*, identifier: str = "OP05-119_P1") -> int:
    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="op-05", name="Awakening of the New Era")
        session.add(set_row)
        session.flush()
        card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="onepiece:op05-119")
        session.add(card)
        session.flush()
        print_row = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP05-119",
            language="en",
            rarity="SEC",
            variant="p1",
            print_key="onepiece:op-05:op05-119:en:p1",
        )
        session.add(print_row)
        session.flush()
        session.add(
            PrintIdentifier(
                print_id=print_row.id,
                source="onepiece_official",
                external_id=identifier,
            )
        )
        session.commit()
        return print_row.id


def test_backfill_plan_uses_exact_official_identifier_only(client):
    print_id = _seed_exact_target()
    with db.SessionLocal() as session:
        plan = build_plan(_payload(), session)

    assert plan["safe_to_apply"] is True
    assert plan["matching"]["unresolved_source_count"] == 0
    assert plan["proposals"] == {
        "count": 1,
        "insert_count": 1,
        "update_count": 0,
        "already_current_count": 0,
    }
    proposal = plan["_proposals"][0]
    assert proposal["print_id"] == print_id
    assert proposal["external_id"] == "OP05-119_P1"
    assert proposal["details_json"]["effect"].startswith("[On Play] DON!! -10")
    assert proposal["details_json"]["official"] is True


def test_backfill_plan_does_not_guess_when_identifier_does_not_match(client):
    _seed_exact_target(identifier="OP05-119_P2")
    with db.SessionLocal() as session:
        plan = build_plan(_payload(external_id="OP05-119_P1"), session)

    assert plan["proposals"]["count"] == 0
    assert plan["matching"]["unresolved_source_count"] == 1
    assert plan["matching"]["database_without_source_count"] == 1


def test_backfill_plan_blocks_existing_localization_from_other_source(client):
    print_id = _seed_exact_target()
    with db.SessionLocal() as session:
        session.add(
            PrintLocalization(
                print_id=print_id,
                language="en",
                source="manual_review",
                external_id="manual:1",
                card_name="Monkey.D.Luffy",
                set_name="Awakening of the New Era",
                details_json={"effect": "Reviewed text"},
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        plan = build_plan(_payload(), session)

    assert plan["safe_to_apply"] is False
    assert plan["proposals"]["count"] == 0
    assert plan["blockers"]["conflicting_existing_localizations"] == 1
    assert plan["samples"]["conflicting_localizations"][0]["print_id"] == print_id


def test_backfill_plan_is_idempotent_for_identical_existing_payload(client):
    print_id = _seed_exact_target()
    with db.SessionLocal() as session:
        plan = build_plan(_payload(), session)
        proposal = plan["_proposals"][0]
        session.add(
            PrintLocalization(
                print_id=proposal["print_id"],
                language=proposal["language"],
                source=proposal["source"],
                external_id=proposal["external_id"],
                card_name=proposal["card_name"],
                set_name=proposal["set_name"],
                details_json=proposal["details_json"],
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        plan = build_plan(_payload(), session)
        row = session.execute(
            select(PrintLocalization).where(PrintLocalization.print_id == print_id)
        ).scalar_one()

    assert row.source == "onepiece_official"
    assert plan["safe_to_apply"] is True
    assert plan["proposals"]["count"] == 0
    assert plan["proposals"]["already_current_count"] == 1