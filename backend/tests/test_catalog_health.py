from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.scripts.catalog_health import get_catalog_health


def test_catalog_health_reports_missing_data_and_empty_entities(client):
    with db.SessionLocal() as session:
        game = Game(slug="health-test", name="Health Test TCG")
        session.add(game)
        session.flush()

        populated_set = Set(game_id=game.id, code="HT01", name="First Set")
        empty_set = Set(game_id=game.id, code="HT02", name="Empty Set")
        session.add_all([populated_set, empty_set])
        session.flush()

        complete_card = Card(game_id=game.id, name="Complete Card", card_key="complete-card")
        incomplete_card = Card(game_id=game.id, name="Incomplete Card")
        orphan_card = Card(game_id=game.id, name="Orphan Card", card_key="orphan-card")
        session.add_all([complete_card, incomplete_card, orphan_card])
        session.flush()

        complete_print = Print(
            set_id=populated_set.id,
            card_id=complete_card.id,
            collector_number="001",
            language="EN",
            rarity="rare",
            is_foil=False,
            variant="default",
            print_key="health-test:ht01:001:en:default",
            tcgdex_id="external-001",
        )
        incomplete_print = Print(
            set_id=populated_set.id,
            card_id=incomplete_card.id,
            collector_number="002",
            language=None,
            rarity=None,
            is_foil=False,
            variant="default",
            print_key=None,
        )
        session.add_all([complete_print, incomplete_print])
        session.flush()

        session.add(
            PrintImage(
                print_id=complete_print.id,
                url="https://example.com/complete.png",
                is_primary=True,
                source="fixture",
            )
        )
        session.add(
            PrintIdentifier(
                print_id=complete_print.id,
                source="fixture",
                external_id="fixture-001",
            )
        )
        session.commit()

        payload = get_catalog_health(session)

    game_health = next(item for item in payload["games"] if item["slug"] == "health-test")

    assert game_health["counts"]["sets"] == 2
    assert game_health["counts"]["cards"] == 3
    assert game_health["counts"]["prints"] == 2
    assert game_health["counts"]["images"] == 1
    assert game_health["counts"]["prints_with_any_image"] == 1
    assert game_health["counts"]["prints_with_primary_image"] == 1
    assert game_health["counts"]["prints_with_any_external_identifier"] == 1

    assert game_health["issues"]["sets_without_prints"] == 1
    assert game_health["issues"]["cards_without_prints"] == 1
    assert game_health["issues"]["sets_missing_release_date"] == 2
    assert game_health["issues"]["cards_missing_card_key"] == 1
    assert game_health["issues"]["prints_missing_language"] == 1
    assert game_health["issues"]["prints_missing_rarity"] == 1
    assert game_health["issues"]["prints_missing_print_key"] == 1
    assert game_health["issues"]["prints_without_any_image"] == 1
    assert game_health["issues"]["prints_without_primary_image"] == 1
    assert game_health["issues"]["prints_without_external_identifier"] == 1
    assert game_health["issues"]["potential_duplicate_print_identity_groups"] == 0
    assert game_health["status"] == "warning"

    assert game_health["samples"]["sets_without_prints"][0]["code"] == "HT02"
    assert payload["database_dialect"] == "sqlite"


def test_catalog_health_flags_null_language_duplicate_identity_groups(client):
    with db.SessionLocal() as session:
        game = Game(slug="duplicate-test", name="Duplicate Test TCG")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="DUP", name="Duplicate Set")
        session.add(set_row)
        session.flush()
        card_a = Card(game_id=game.id, name="Card A")
        card_b = Card(game_id=game.id, name="Card B")
        session.add_all([card_a, card_b])
        session.flush()

        session.add_all(
            [
                Print(
                    set_id=set_row.id,
                    card_id=card_a.id,
                    collector_number="001",
                    language=None,
                    rarity="rare",
                    is_foil=False,
                    variant="default",
                ),
                Print(
                    set_id=set_row.id,
                    card_id=card_b.id,
                    collector_number="001",
                    language=None,
                    rarity="rare",
                    is_foil=False,
                    variant="default",
                ),
            ]
        )
        session.commit()

        payload = get_catalog_health(session)

    game_health = next(item for item in payload["games"] if item["slug"] == "duplicate-test")
    assert game_health["issues"]["potential_duplicate_print_identity_groups"] == 1
    assert game_health["status"] == "critical"


def test_catalog_health_is_read_only(client):
    with db.SessionLocal() as session:
        game = Game(slug="readonly-test", name="Read Only Test")
        session.add(game)
        session.commit()
        before = session.execute(select(Game.id).where(Game.slug == "readonly-test")).scalar_one()

        get_catalog_health(session)

        after = session.execute(select(Game.id).where(Game.slug == "readonly-test")).scalar_one()

    assert before == after
