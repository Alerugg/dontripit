from app.scripts.seed import run_seed
from app.scripts.seed_catalog import run_seed_catalog


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
