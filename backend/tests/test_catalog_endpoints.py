from sqlalchemy import select

from app import db
from app.auth.service import hash_api_key
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, Card, Print
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


def test_v1_games_responds_200_with_valid_api_key(client):
    run_seed()
    response = client.get("/api/v1/games", headers=_auth_headers())
    assert response.status_code == 200


def test_v1_sets_with_game_returns_200_and_list(client):
    _seed_fixture_catalog()
    response = client.get("/api/v1/sets?game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


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
