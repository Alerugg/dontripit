import os

import pytest

from app import db
from app.models import Card, Game, Print, PrintImage, Set
from app.search_v2.output_contract import clean_optional_metadata, sanitize_search_item


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


def _seed_pikachu(*, rarity: str):
    with db.SessionLocal() as session:
        game = Game(slug="pokemon", name="Pokémon")
        session.add(game)
        session.flush()
        set_row = Set(game_id=game.id, code="bs", name="Base Set")
        session.add(set_row)
        session.flush()
        card = Card(game_id=game.id, name="Pikachu", card_key="pokemon:bs:58")
        session.add(card)
        session.flush()
        print_row = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="58",
            language="en",
            rarity=rarity,
            variant="default",
            print_key="pokemon:bs:58:en:default",
        )
        session.add(print_row)
        session.flush()
        session.add(
            PrintImage(
                print_id=print_row.id,
                url="https://example.test/pikachu.png",
                is_primary=True,
                source="test",
            )
        )
        session.commit()
        return card.id, print_row.id


@pytest.mark.parametrize(
    "value",
    [None, "", " ", "unknown", "UNKNOWN", "n/a", "NA", "none", "null", "-", "?", "undefined"],
)
def test_clean_optional_metadata_rejects_placeholders(value):
    assert clean_optional_metadata(value) is None


def test_clean_optional_metadata_preserves_real_values():
    assert clean_optional_metadata(" Common ") == "Common"
    assert clean_optional_metadata("OP05") == "OP05"
    assert clean_optional_metadata(58) == 58


def test_sanitize_search_item_does_not_change_physical_identity():
    source = {
        "type": "card",
        "card_id": 1,
        "matched_print": {
            "print_id": 571,
            "set_code": "bs",
            "collector_number": "58",
            "rarity": "unknown",
            "primary_image_url": "https://example.test/pikachu.png",
        },
        "score": 5000.0,
    }
    result = sanitize_search_item(source)
    assert result["card_id"] == 1
    assert result["matched_print"]["print_id"] == 571
    assert result["matched_print"]["set_code"] == "bs"
    assert result["matched_print"]["collector_number"] == "58"
    assert result["matched_print"]["rarity"] is None
    assert result["matched_print"]["primary_image_url"] == "https://example.test/pikachu.png"
    assert result["score"] == 5000.0


def test_search_v2_returns_null_not_unknown_for_missing_rarity(client):
    _, print_id = _seed_pikachu(rarity="unknown")

    response = _public_get(client, "/api/v2/search?q=Pikachu&game=pokemon")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination_mode"] == "canonical_name"
    item = payload["items"][0]
    assert item["matched_print"]["print_id"] == print_id
    assert item["matched_print"]["set_code"] == "bs"
    assert item["matched_print"]["collector_number"] == "58"
    assert item["matched_print"]["rarity"] is None


def test_search_v2_preserves_known_rarity(client):
    _, print_id = _seed_pikachu(rarity="Common")

    response = _public_get(client, "/api/v2/search?q=Pikachu&game=pokemon")
    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["matched_print"]["print_id"] == print_id
    assert item["matched_print"]["rarity"] == "Common"
