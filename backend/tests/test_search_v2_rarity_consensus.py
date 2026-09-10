import os

from app import db
from app.models import Card, Game, Print, Set


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


def _seed_card_with_prints(*, representative_rarity, siblings):
    """Seed one canonical Pikachu; the first print is always representative."""
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

        representative = Print(
            set_id=set_row.id,
            card_id=card.id,
            collector_number="58",
            language="en",
            rarity=representative_rarity,
            variant="default",
            print_key="pokemon:bs:58:en:legacy-representative",
        )
        session.add(representative)
        session.flush()
        representative_id = representative.id

        for index, sibling in enumerate(siblings, start=1):
            session.add(
                Print(
                    set_id=set_row.id,
                    card_id=card.id,
                    collector_number=sibling.get("collector_number", "58"),
                    language=sibling.get("language", "en"),
                    rarity=sibling.get("rarity"),
                    variant=sibling.get("variant", f"sibling-{index}"),
                    print_key=f"pokemon:bs:{sibling.get('collector_number', '58')}:{sibling.get('language', 'en')}:sibling-{index}",
                )
            )
        session.commit()
        return representative_id


def _search_pikachu(client, monkeypatch):
    # The exact identifier implementation is PostgreSQL-only; this test targets
    # the canonical-name path and its read-only representative enrichment.
    monkeypatch.setattr("app.routes.search_v2._exact_identifier_for_game", lambda *args, **kwargs: None)
    response = _public_get(client, "/api/v2/search?q=Pikachu&game=pokemon")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination_mode"] == "canonical_name"
    assert len(payload["items"]) == 1
    return payload["items"][0]


def test_missing_rarity_is_enriched_from_strict_sibling_consensus(client, monkeypatch):
    representative_id = _seed_card_with_prints(
        representative_rarity="unknown",
        siblings=[
            {"rarity": "Common", "variant": "normal"},
            {"rarity": " common ", "variant": "reverse"},
            {"rarity": "UNKNOWN", "variant": "legacy-copy"},
        ],
    )

    item = _search_pikachu(client, monkeypatch)
    matched = item["matched_print"]
    assert matched["print_id"] == representative_id
    assert matched["set_code"] == "bs"
    assert matched["collector_number"] == "58"
    assert matched["language"] == "en"
    assert matched["rarity"] == "Common"
    assert matched["rarity_source"] == "sibling_consensus_v1"
    assert matched["rarity_evidence_count"] == 2


def test_conflicting_sibling_rarities_remain_unresolved(client, monkeypatch):
    representative_id = _seed_card_with_prints(
        representative_rarity="unknown",
        siblings=[{"rarity": "Common"}, {"rarity": "Rare"}],
    )

    item = _search_pikachu(client, monkeypatch)
    matched = item["matched_print"]
    assert matched["print_id"] == representative_id
    assert matched["rarity"] is None
    assert "rarity_source" not in matched
    assert "rarity_evidence_count" not in matched


def test_different_language_or_collector_number_is_not_used_as_evidence(client, monkeypatch):
    representative_id = _seed_card_with_prints(
        representative_rarity="unknown",
        siblings=[
            {"rarity": "Common", "language": "ja"},
            {"rarity": "Common", "collector_number": "59"},
        ],
    )

    item = _search_pikachu(client, monkeypatch)
    matched = item["matched_print"]
    assert matched["print_id"] == representative_id
    assert matched["rarity"] is None
    assert "rarity_source" not in matched


def test_known_representative_rarity_wins_without_sibling_override(client, monkeypatch):
    representative_id = _seed_card_with_prints(
        representative_rarity="Common",
        siblings=[{"rarity": "Rare"}],
    )

    item = _search_pikachu(client, monkeypatch)
    matched = item["matched_print"]
    assert matched["print_id"] == representative_id
    assert matched["rarity"] == "Common"
    assert "rarity_source" not in matched
    assert "rarity_evidence_count" not in matched
