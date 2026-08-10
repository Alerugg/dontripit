from app import db
from app.catalog_release_models import CatalogRelease, PrintRelease
from app.models import Card, Game, Print, Set
from app.routes.set_ui import _normalized_release_code, _release_codes


def test_release_code_normalization_accepts_bandai_bracket_alias():
    row = {"code": None, "name": "BOOSTER PACK -THE TIME OF BATTLE- [OP-16]"}
    assert _normalized_release_code("op16") == "op16"
    assert _normalized_release_code("OP-16") == "op16"
    assert _release_codes(row) == {"op16"}


def test_set_ui_prefers_catalog_release_membership_over_set_family(client):
    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="OP-16", name="OP-16")
        session.add(set_row)
        session.flush()

        card_a = Card(game_id=game.id, name="Card A", card_key="op:a")
        card_b = Card(game_id=game.id, name="Card B", card_key="op:b")
        session.add_all([card_a, card_b])
        session.flush()
        print_a = Print(
            set_id=set_row.id,
            card_id=card_a.id,
            collector_number="OP16-001",
            language="en",
            rarity="C",
            is_foil=False,
            variant="default",
            print_key="op:a:default",
        )
        print_b = Print(
            set_id=set_row.id,
            card_id=card_b.id,
            collector_number="OP16-002",
            language="en",
            rarity="C",
            is_foil=False,
            variant="default",
            print_key="op:b:default",
        )
        session.add_all([print_a, print_b])
        session.flush()

        release = CatalogRelease(
            game_id=game.id,
            source="onepiece_official",
            external_id="569116",
            name="BOOSTER PACK -THE TIME OF BATTLE- [OP-16]",
            code=None,
            language="en",
            region="global-en",
        )
        session.add(release)
        session.flush()
        # Deliberately include only one print in the release. The endpoint must
        # return the release membership, not both prints from the Set family.
        session.add(PrintRelease(print_id=print_b.id, release_id=release.id))
        session.commit()

    response = client.get("/api/v1/set-ui/prints?game=onepiece&set_code=OP16&limit=50&offset=0")
    assert response.status_code == 200
    body = response.get_json()
    assert body["scope"]["type"] == "release"
    assert body["scope"]["release_external_id"] == "569116"
    assert body["total"] == 1
    assert [row["collector_number"] for row in body["items"]] == ["OP16-002"]


def test_set_ui_falls_back_to_set_when_no_release_alias_exists(client):
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="sv01", name="Scarlet & Violet", tcgdex_id="sv01")
        session.add(set_row)
        session.flush()
        card = Card(game_id=game.id, name="Poké Ball", card_key="pokemon:sv01:185")
        session.add(card)
        session.flush()
        session.add(
            Print(
                set_id=set_row.id,
                card_id=card.id,
                collector_number="185",
                language="en",
                rarity="Common",
                is_foil=False,
                variant="default",
                print_key="pokemon:sv01:185:default",
            )
        )
        session.commit()

    response = client.get("/api/v1/set-ui/prints?game=pokemon&set_code=sv01")
    assert response.status_code == 200
    body = response.get_json()
    assert body["scope"]["type"] == "set"
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Poké Ball"
