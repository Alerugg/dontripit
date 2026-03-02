from sqlalchemy import func, select
from app import db
from app.ingest.registry import get_connector
from app.models import Print, SourceRecord
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
