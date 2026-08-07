from app import db
from app.catalog_release_models import CatalogRelease, PrintRelease
from app.models import Card, Game, Print, Set
from app.search_v2.indexer import rebuild_onepiece_search_v2
from app.search_v2_models import CardSearchProfile, FacetDefinition, PrintSearchProfile


def test_indexer_builds_searchable_prints_and_dynamic_facets(client):
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
        base = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP05-119",
            language="en",
            rarity="SEC",
            variant="default",
            print_key="onepiece:op-05:op05-119:en:default",
        )
        parallel = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP05-119",
            language="en",
            rarity="SEC",
            variant="p1",
            print_key="onepiece:op-05:op05-119:en:p1",
        )
        session.add_all([base, parallel])
        session.flush()
        release = CatalogRelease(
            game_id=game.id,
            source="onepiece_official",
            external_id="569105",
            name="BOOSTER PACK -AWAKENING OF THE NEW ERA- [OP-05]",
            language="en",
            region="global-en",
        )
        session.add(release)
        session.flush()
        session.add_all(
            [
                PrintRelease(print_id=base.id, release_id=release.id, source_print_id="OP05-119"),
                PrintRelease(print_id=parallel.id, release_id=release.id, source_print_id="OP05-119_p1"),
            ]
        )
        session.commit()

        attrs = {
            "OP05-119": {
                "card_type": "Character",
                "colors": ["Purple"],
                "cost": 10,
                "life": None,
                "power": 12000,
                "counter": None,
                "attributes": ["Strike"],
                "block": "2",
                "traits": ["Straw Hat Crew"],
                "effect": "[On Play] Example.",
                "trigger": None,
            },
            "OP05-119_P1": {
                "card_type": "Character",
                "colors": ["Purple"],
                "cost": 10,
                "life": None,
                "power": 12000,
                "counter": None,
                "attributes": ["Strike"],
                "block": "2",
                "traits": ["Straw Hat Crew"],
                "effect": "[On Play] Example.",
                "trigger": None,
            },
        }
        stats = rebuild_onepiece_search_v2(session, source_attributes=attrs)
        session.commit()

        assert stats["cards"] == 1
        assert stats["prints"] == 2
        assert stats["facets"] >= 20

        card_profile = session.query(CardSearchProfile).one()
        assert card_profile.normalized_name == "monkey d luffy"
        assert card_profile.attributes_json["color"] == ["Purple"]
        assert "straw hat crew" in card_profile.search_text

        profiles = session.query(PrintSearchProfile).order_by(PrintSearchProfile.exact_variant).all()
        assert {row.exact_variant for row in profiles} == {"default", "p1"}
        parallel_profile = next(row for row in profiles if row.exact_variant == "p1")
        assert parallel_profile.variant_family == "parallel"
        assert "monkey d luffy" in parallel_profile.search_text
        assert "op05 119" in parallel_profile.search_text
        assert "awakening of the new era" in parallel_profile.search_text
        assert parallel_profile.attributes_json["block"] == "2"

        facet_keys = {row.key for row in session.query(FacetDefinition).all()}
        assert {"color", "power", "traits", "rarity", "variant_family"}.issubset(facet_keys)
