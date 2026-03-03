import os

from sqlalchemy import func, select

from app import db
from app.auth import middleware
from app.auth.create_key import main as create_key_main
from app.auth.service import hash_api_key
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, Print, SourceRecord
from app.scripts.seed import run_seed


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_db_check(client):
    response = client.get("/api/db-check")
    assert response.status_code == 200
    assert response.get_json() == {"db": "ok"}


def test_games(client):
    run_seed()
    response = client.get("/api/games")
    assert response.status_code == 200
    data = response.get_json()
    slugs = {item["slug"] for item in data}
    assert {"pokemon", "mtg"}.issubset(slugs)


def test_search_works(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_search_returns_results_after_seed_or_ingest(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] in {"card", "print"} for item in payload)


def test_search_with_empty_type_and_no_results_returns_200(client):
    response = client.get("/api/search?q=zzzz-no-results&game=pokemon&type=")
    assert response.status_code == 200
    assert response.get_json() == []


def test_cards_returns_200_and_json_list(client):
    run_seed()
    response = client.get("/api/cards")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_prints_returns_200_and_json_list(client):
    run_seed()
    response = client.get("/api/prints")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_ingest_fixture_local_idempotent(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_count_first = session.execute(select(func.count(Print.id))).scalar_one()
        source_records_first = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_count_second = session.execute(select(func.count(Print.id))).scalar_one()
        source_records_second = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    assert print_count_first == print_count_second
    assert source_records_first == source_records_second


def test_sets_list_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/sets?game=pokemon")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)
    assert any(item["code"] == "SV1" for item in payload)


def test_print_detail_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_id = session.execute(select(Print.id).order_by(Print.id)).scalars().first()

    response = client.get(f"/api/prints/{print_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["print"]["id"] == print_id
    assert payload["card"]["name"]
    assert payload["set"]["code"]
    assert isinstance(payload["images"], list)


def test_search_returns_pikachu(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon&type=card")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] == "card" for item in payload)


def test_search_returns_print_by_collector_number(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=001&game=pokemon&type=print")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] == "print" and item.get("collector_number") == "001" for item in payload)


def test_public_enabled_allows_no_key(client):
    os.environ["PUBLIC_API_ENABLED"] = "true"
    response = client.get("/api/games")
    assert response.status_code == 200


def test_public_disabled_requires_key(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/games")
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}


def test_create_key_cli_creates_record(client, monkeypatch, capsys):
    with db.SessionLocal() as session:
        session.add(ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60))
        session.commit()

    monkeypatch.setattr("sys.argv", ["create_key", "--plan", "free", "--label", "dev"])
    create_key_main()
    created_key = capsys.readouterr().out.strip()

    with db.SessionLocal() as session:
        stored = session.execute(select(ApiKey).where(ApiKey.prefix == created_key[:8])).scalar_one()

    assert stored.key_hash == hash_api_key(created_key)
    assert stored.prefix == created_key[:8]


def test_rate_limit_blocks(client):
    os.environ["PUBLIC_API_ENABLED"] = "true"
    os.environ["PUBLIC_IP_RATE_LIMIT_RPM"] = "1"
    middleware._RATE_WINDOWS.clear()

    first = client.get("/api/games")
    second = client.get("/api/games")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {"error": "rate_limited"}


def test_quota_exceeded_blocks(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    middleware._RATE_WINDOWS.clear()

    with db.SessionLocal() as session:
        plan = ApiPlan(name="free", monthly_quota_requests=1, burst_rpm=10)
        session.add(plan)
        session.flush()
        api_key = ApiKey(key_hash=hash_api_key("test-key"), prefix="test-key", plan_id=plan.id, is_active=True)
        session.add(api_key)
        session.commit()

    first = client.get("/api/games", headers={"X-API-Key": "test-key"})
    second = client.get("/api/games", headers={"X-API-Key": "test-key"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {"error": "quota_exceeded"}
