from pathlib import Path

from sqlalchemy import func, select

from app import db
from app.ingest.run import run_ingest
from app.models import Print, PrintIdentifier
from app.scripts.seed import run_seed
from app.scripts.seed_catalog import run_seed_catalog


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_v1_endpoints_exist(client):
    run_seed()
    run_seed_catalog()

    health_response = client.get("/api/v1/health")
    assert health_response.status_code == 200

    search_response = client.get("/api/v1/search?q=pika&game=pokemon")
    assert search_response.status_code == 200
    assert isinstance(search_response.get_json(), list)


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


def test_sets_list(client):
    run_seed()
    run_seed_catalog()

    response = client.get("/api/sets?game=pokemon")
    assert response.status_code == 200
    data = response.get_json()

    assert len(data) >= 1
    assert any(item["code"] == "SV1" for item in data)


def test_prints_filter_by_set_code(client):
    run_seed()
    run_seed_catalog()

    response = client.get("/api/prints?game=pokemon&set_code=SV1&language=EN")
    assert response.status_code == 200
    data = response.get_json()

    assert len(data) >= 1
    assert all(item["set"]["code"] == "SV1" for item in data)
    assert all(item["language"] == "EN" for item in data)


def test_print_detail(client):
    run_seed()
    run_seed_catalog()

    list_response = client.get("/api/prints?game=pokemon&set_code=SV1")
    first_print_id = list_response.get_json()[0]["id"]

    detail_response = client.get(f"/api/prints/{first_print_id}")
    assert detail_response.status_code == 200
    payload = detail_response.get_json()

    assert payload["print"]["id"] == first_print_id
    assert payload["set"]["code"] == "SV1"
    assert isinstance(payload["images"], list)


def test_search_returns_results_after_seed_or_ingest(client):
    run_seed()
    run_seed_catalog()

    response = client.get("/api/search?q=pika&game=pokemon")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) >= 1
    assert any("Pikachu" in item["title"] for item in data)


def test_rate_limit_basic(client):
    run_seed()
    run_seed_catalog()

    for _ in range(5):
        ok_response = client.get("/api/v1/search?q=pika&game=pokemon")
        assert ok_response.status_code == 200

    blocked_response = client.get("/api/v1/search?q=pika&game=pokemon")
    assert blocked_response.status_code == 429


def test_cache_basic(client):
    run_seed()
    run_seed_catalog()

    first = client.get("/api/v1/prints?game=pokemon&set_code=SV1")
    second = client.get("/api/v1/prints?game=pokemon&set_code=SV1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers.get("X-Cache") == "MISS"
    assert second.headers.get("X-Cache") == "HIT"


def test_ingest_fixture_local_idempotent(client):
    run_seed()
    fixtures_path = Path(__file__).resolve().parents[1] / "data" / "fixtures"

    run_ingest("fixture_local", str(fixtures_path))
    with db.SessionLocal() as session:
        first_count = session.execute(select(func.count(Print.id))).scalar_one()

    run_ingest("fixture_local", str(fixtures_path))
    with db.SessionLocal() as session:
        second_count = session.execute(select(func.count(Print.id))).scalar_one()

    assert first_count == second_count


def test_scryfall_fixture_ingest_dedupes_by_identifier(client):
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "scryfall"

    run_ingest("scryfall_mtg", str(fixture_path), set_code="woe", limit=10, fixture=True)
    with db.SessionLocal() as session:
        first_prints = session.execute(select(func.count(Print.id))).scalar_one()
        first_ids = session.execute(select(func.count(PrintIdentifier.id)).where(PrintIdentifier.source == "scryfall")).scalar_one()

    run_ingest("scryfall_mtg", str(fixture_path), set_code="woe", limit=10, fixture=True)
    with db.SessionLocal() as session:
        second_prints = session.execute(select(func.count(Print.id))).scalar_one()
        second_ids = session.execute(select(func.count(PrintIdentifier.id)).where(PrintIdentifier.source == "scryfall")).scalar_one()

    assert first_prints == second_prints
    assert first_ids == second_ids
