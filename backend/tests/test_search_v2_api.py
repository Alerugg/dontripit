import os

from app import db
from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2_models import FacetDefinition, PrintSearchProfile


def _seed_search_v2(session):
    game = Game(slug="onepiece", name="ONE PIECE Card Game")
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code="op-05", name="Awakening of the New Era")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="onepiece:op05-119")
    session.add(card)
    session.flush()
    prints = []
    for variant in ("default", "p1"):
        row = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="OP05-119",
            language="en",
            rarity="SEC",
            variant=variant,
            print_key=f"onepiece:op-05:op05-119:en:{variant}",
        )
        session.add(row)
        session.flush()
        session.add(
            PrintImage(
                print_id=row.id,
                url=f"https://example.test/{variant}.png",
                is_primary=True,
                source="test",
            )
        )
        session.add(
            PrintSearchProfile(
                print_id=row.id,
                card_id=card.id,
                game_id=game.id,
                normalized_name="monkey d luffy",
                normalized_set_code="op-05",
                normalized_collector_number="op05-119",
                language="en",
                rarity="SEC",
                exact_variant=variant,
                variant_family="default" if variant == "default" else "parallel",
                release_names_json=["Awakening of the New Era"],
                aliases_json=["op05119", "op05"],
                keywords_json=[],
                attributes_json={"color": ["Purple"], "power": 12000},
                search_text=f"monkey d luffy op05 119 op05 awakening of the new era sec {variant} purple",
            )
        )
        prints.append(row)

    session.add(
        FacetDefinition(
            game_id=game.id,
            scope="card",
            key="color",
            label="Color",
            value_type="enum",
            ui_type="chips",
            group_name="Card",
            source_path="attributes.color",
            quick_filter=True,
            display_order=10,
        )
    )
    session.commit()
    return card, prints


def _public_get(client, path: str):
    previous = os.environ.get("PUBLIC_API_ENABLED")
    os.environ["PUBLIC_API_ENABLED"] = "true"
    try:
        return client.get(path)
    finally:
        if previous is None:
            os.environ.pop("PUBLIC_API_ENABLED", None)
        else:
            os.environ["PUBLIC_API_ENABLED"] = previous


def test_normal_search_api_groups_physical_variants_by_card(client):
    with db.SessionLocal() as session:
        _seed_search_v2(session)

    response = _public_get(client, "/api/v2/search?q=Luffy&game=onepiece")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["items"][0]["name"] == "Monkey.D.Luffy"
    assert payload["items"][0]["matched_print"]["collector_number"] == "OP05-119"


def test_normal_search_name_results_expose_exhaustive_pagination(client):
    with db.SessionLocal() as session:
        _seed_search_v2(session)

    response = _public_get(client, "/api/v2/search?q=Luffy&game=onepiece&limit=1&offset=0")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination_mode"] == "canonical_name"
    assert payload["count"] == 1
    assert payload["total"] == 1
    assert payload["total_prints"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["has_more"] is False
    assert payload["next_offset"] is None
    assert payload["items"][0]["variant_count"] == 2


def test_normal_search_name_results_accept_offset(client):
    with db.SessionLocal() as session:
        _seed_search_v2(session)

    response = _public_get(client, "/api/v2/search?q=Luffy&game=onepiece&limit=1&offset=1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination_mode"] == "canonical_name"
    assert payload["items"] == []
    assert payload["count"] == 0
    assert payload["total"] == 1
    assert payload["total_prints"] == 2
    assert payload["offset"] == 1
    assert payload["has_more"] is False
    assert payload["next_offset"] is None


def test_search_suggest_contract_is_compact(client):
    with db.SessionLocal() as session:
        _seed_search_v2(session)

    response = _public_get(client, "/api/v2/search/suggest?q=Luffy&game=onepiece")
    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["name"] == "Monkey.D.Luffy"
    assert item["collector_number"] == "OP05-119"
    assert "score" not in item


def test_facets_api_is_dynamic_per_game(client):
    with db.SessionLocal() as session:
        _seed_search_v2(session)

    response = _public_get(client, "/api/v2/games/onepiece/facets")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["facets"][0]["key"] == "color"
    assert payload["groups"]["Card"][0]["quick_filter"] is True


def test_search_requires_query(client):
    response = _public_get(client, "/api/v2/search")
    assert response.status_code == 400
