from app import db
from app.models import Card, Game, Print, PrintIdentifier, Set
from app.onepiece_legacy_policy import (
    is_legacy_onepiece_print,
    is_onepiece_canonical_external_id,
    is_onepiece_legacy_external_id,
)


def test_onepiece_legacy_external_id_pattern_detects_legacy_suffixes():
    assert is_onepiece_legacy_external_id("op01-001-default-en")
    assert is_onepiece_legacy_external_id("eb01-012-parallel-en")
    assert not is_onepiece_legacy_external_id("OP01-016_p1")


def test_onepiece_canonical_external_id_pattern_accepts_new_family():
    assert is_onepiece_canonical_external_id("OP01-016_p1")
    assert is_onepiece_canonical_external_id("ST01-012")
    assert is_onepiece_canonical_external_id("P-001")
    assert not is_onepiece_canonical_external_id("st10-001-default-en")


def test_is_legacy_onepiece_print_is_scoped_to_onepiece_only():
    assert is_legacy_onepiece_print(
        game_slug="onepiece",
        primary_image_url="https://placehold.co/367x512?text=ONE+PIECE",
        external_id="op01-001-default-en",
    )
    assert not is_legacy_onepiece_print(
        game_slug="yugioh",
        primary_image_url="https://placehold.co/367x512?text=YGO",
        external_id="dark-magician-default-en",
    )


def test_set_ui_onepiece_exposes_only_canonical_physical_prints_with_correct_pagination(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")

    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="One Piece")
        session.add(game)
        session.flush()

        set_row = Set(game_id=game.id, code="OP-15", name="OP-15")
        card = Card(game_id=game.id, name="Test Card")
        session.add_all([set_row, card])
        session.flush()

        legacy = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP15-001",
            language="en",
            variant="parallel",
        )
        canonical_p1 = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP15-001",
            language="en",
            variant="p1",
        )
        canonical_p2 = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP15-001",
            language="en",
            variant="p2",
        )
        session.add_all([legacy, canonical_p1, canonical_p2])
        session.flush()

        session.add_all(
            [
                PrintIdentifier(
                    print_id=legacy.id,
                    source="punk_records",
                    external_id="op15-001-parallel-en",
                ),
                PrintIdentifier(
                    print_id=canonical_p1.id,
                    source="punk_records",
                    external_id="OP15-001_p1",
                ),
                PrintIdentifier(
                    print_id=canonical_p2.id,
                    source="punk_records",
                    external_id="OP15-001_p2",
                ),
            ]
        )
        session.commit()
        canonical_ids = [canonical_p1.id, canonical_p2.id]
        legacy_id = legacy.id

    first_page = client.get("/api/v1/set-ui/prints?game=onepiece&set_code=OP-15&limit=1&offset=0")
    assert first_page.status_code == 200, first_page.get_json()
    first_payload = first_page.get_json()
    assert first_payload["total"] == 2
    assert first_payload["limit"] == 1
    assert first_payload["offset"] == 0
    assert [item["print_id"] for item in first_payload["items"]] == [canonical_ids[0]]
    assert legacy_id not in [item["print_id"] for item in first_payload["items"]]

    second_page = client.get("/api/v1/set-ui/prints?game=onepiece&set_code=OP-15&limit=1&offset=1")
    assert second_page.status_code == 200, second_page.get_json()
    second_payload = second_page.get_json()
    assert second_payload["total"] == 2
    assert [item["print_id"] for item in second_payload["items"]] == [canonical_ids[1]]
