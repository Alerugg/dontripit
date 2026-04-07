from sqlalchemy import select

from app import db
from app.auth.service import hash_api_key
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, Card, Game, Print, PrintImage, Set
from app.scripts.seed import run_seed


def _auth_headers(key: str = "catalog-test-key") -> dict[str, str]:
    with db.SessionLocal() as session:
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == "free")).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()

        api_key = session.execute(select(ApiKey).where(ApiKey.prefix == key[:8])).scalar_one_or_none()
        if api_key is None:
            session.add(
                ApiKey(
                    key_hash=hash_api_key(key),
                    prefix=key[:8],
                    plan_id=plan.id,
                    is_active=True,
                    scopes=["read:catalog"],
                )
            )
            session.commit()

    return {"X-API-Key": key}


def _seed_fixture_catalog():
    run_seed()
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()


def _seed_fixture_catalog_with_yugioh():
    _seed_fixture_catalog()
    yugioh_connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        yugioh_connector.run(session, "backend/data/fixtures", fixture=True)
        session.commit()


def test_v1_games_responds_200_with_valid_api_key(client):
    run_seed()
    response = client.get("/api/v1/games", headers=_auth_headers())
    assert response.status_code == 200


def test_v1_sets_with_game_returns_200_and_list(client):
    _seed_fixture_catalog()
    response = client.get("/api/v1/sets?game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_v1_sets_includes_non_zero_card_count_when_prints_exist(client):
    _seed_fixture_catalog()
    response = client.get("/api/v1/sets?game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)
    assert any(int(item.get("card_count", 0)) > 0 for item in payload)


def test_v1_sets_onepiece_keeps_unique_numeric_codes_and_uses_neutral_name_fallback(client):
    run_seed()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            game = Game(slug="onepiece", name="ONE PIECE Card Game")
            session.add(game)
            session.flush()

        legacy_set = Set(game_id=game.id, code="569010", name="569010")
        card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="legacy-luffy-st10")
        session.add_all([legacy_set, card])
        session.flush()

        session.add(
            Print(
                set_id=legacy_set.id,
                card_id=card.id,
                collector_number="ST10-001",
                language="en",
                variant="default",
                print_key="onepiece:569010:st10-001:en:default",
            )
        )
        session.commit()

    response = client.get("/api/v1/sets?game=onepiece&q=569010", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    top = payload[0]
    assert top["code"] == "569010"
    assert top["name"] == f"Set #{top['id']}"
    assert int(top["card_count"]) > 0


def test_v1_cards_with_query_returns_200(client):
    _seed_fixture_catalog()
    response = client.get("/api/v1/cards?game=pokemon&q=pika", headers=_auth_headers())
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_v1_prints_with_limit_returns_200(client):
    _seed_fixture_catalog()
    response = client.get("/api/v1/prints?game=pokemon&limit=5", headers=_auth_headers())
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_v1_card_detail_returns_200_with_prints_array(client):
    _seed_fixture_catalog()
    with db.SessionLocal() as session:
        card_id = session.execute(
            select(Card.id)
            .join(Print, Print.card_id == Card.id)
            .order_by(Card.id.asc())
        ).scalars().first()

    response = client.get(f"/api/v1/cards/{card_id}", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload["prints"], list)


def test_v1_sets_without_api_key_returns_401(client):
    run_seed()
    response = client.get("/api/v1/sets?game=pokemon")
    assert response.status_code == 401


def test_v1_search_card_result_includes_primary_image_url_for_yugioh_card(client):
    _seed_fixture_catalog_with_yugioh()

    with db.SessionLocal() as session:
        card_row = session.execute(
            select(Card.id)
            .join(Print, Print.card_id == Card.id)
            .join(PrintImage, PrintImage.print_id == Print.id)
            .where(Card.game_id == select(Game.id).where(Game.slug == "yugioh").scalar_subquery())
            .order_by(Card.id.asc())
        ).first()

    assert card_row is not None
    card_id = card_row[0]

    response = client.get(f"/api/v1/cards/{card_id}", headers=_auth_headers())
    assert response.status_code == 200
    card_payload = response.get_json()
    assert card_payload["primary_image_url"] is not None

    card_name = card_payload["name"]
    search_response = client.get(
        f"/api/v1/search?q={card_name}&game=yugioh&type=card",
        headers=_auth_headers(),
    )
    assert search_response.status_code == 200
    search_payload = search_response.get_json()
    assert search_payload

    card_result = next((item for item in search_payload if item["type"] == "card" and item["id"] == card_id), None)
    assert card_result is not None
    assert card_result["primary_image_url"] is not None



def test_v1_search_card_primary_image_matches_card_detail(client):
    _seed_fixture_catalog_with_yugioh()

    with db.SessionLocal() as session:
        card_id = session.execute(
            select(Card.id)
            .join(Print, Print.card_id == Card.id)
            .join(PrintImage, PrintImage.print_id == Print.id)
            .where(Card.game_id == select(Game.id).where(Game.slug == "yugioh").scalar_subquery())
            .order_by(Card.id.asc())
        ).scalars().first()

    assert card_id is not None

    detail_response = client.get(f"/api/v1/cards/{card_id}", headers=_auth_headers())
    assert detail_response.status_code == 200
    detail_payload = detail_response.get_json()

    search_response = client.get(
        f"/api/v1/search?q={detail_payload['name']}&game=yugioh&type=card",
        headers=_auth_headers(),
    )
    assert search_response.status_code == 200
    search_payload = search_response.get_json()

    card_result = next((item for item in search_payload if item["type"] == "card" and item["id"] == card_id), None)
    assert card_result is not None
    assert card_result["primary_image_url"] == detail_payload["primary_image_url"]
