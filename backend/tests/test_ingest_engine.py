import json
from copy import deepcopy
from pathlib import Path
from sqlalchemy import func, select

from app import db
from app.auth.service import hash_api_key
from app.ingest.base import IngestStats
from app.ingest.registry import get_connector
from app.models import (
    ApiKey,
    ApiPlan,
    Card,
    Game,
    IngestRun,
    Print,
    PrintImage,
    SearchDocument,
    Set,
    Source,
    SourceRecord,
)


def _ygo_fixture_source_path() -> Path:
    fixture_name = "ygoprodeck_yugioh_sample.json"
    candidates = [
        Path("data/fixtures") / fixture_name,
        Path("backend/data/fixtures") / fixture_name,
        Path(__file__).resolve().parents[1] / "data" / "fixtures" / fixture_name,
        Path(__file__).resolve().parents[2] / "data" / "fixtures" / fixture_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unable to resolve fixture path for {fixture_name}")


def _auth_headers(
    key: str = "admin-key", scopes: list[str] | None = None
) -> dict[str, str]:
    with db.SessionLocal() as session:
        plan = session.execute(
            select(ApiPlan).where(ApiPlan.name == "free")
        ).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()

        api_key = session.execute(
            select(ApiKey).where(ApiKey.prefix == key[:8])
        ).scalar_one_or_none()
        if api_key is None:
            session.add(
                ApiKey(
                    key_hash=hash_api_key(key),
                    prefix=key[:8],
                    plan_id=plan.id,
                    is_active=True,
                    scopes=scopes or ["read:catalog"],
                )
            )
            session.commit()
    return {"X-API-Key": key}


def test_ingest_run_created(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_demo.json")
        session.commit()

    with db.SessionLocal() as session:
        run = (
            session.execute(select(IngestRun).order_by(IngestRun.id.desc()))
            .scalars()
            .first()
        )

    assert run is not None
    assert run.status == "success"
    assert run.counts_json["files_seen"] >= 1


def test_reindex_search_populates_and_search_finds_pikachu(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_demo.json")
        session.commit()

    with db.SessionLocal() as session:
        total_docs = session.execute(select(func.count(SearchDocument.id))).scalar_one()
    assert total_docs > 0

    response = client.get(
        "/api/search?q=pika&game=pokemon",
        headers=_auth_headers("catalog-key", ["read:catalog"]),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Pikachu" in item["title"] for item in payload)


def test_scryfall_bootstrap_when_incremental_true_on_empty_db(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
            limit=5,
        )
        session.commit()

    with db.SessionLocal() as session:
        source = session.execute(
            select(Source).where(Source.name == "scryfall_mtg")
        ).scalar_one()
        mtg_cards = session.execute(select(func.count(Card.id))).scalar_one()
        source_records = session.execute(
            select(func.count(SourceRecord.id)).where(
                SourceRecord.source_id == source.id
            )
        ).scalar_one()

    assert stats.records_inserted > 0
    assert mtg_cards > 0
    assert source_records > 0


def test_scryfall_fixture_incremental_idempotent_has_zero_second_run_changes(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        run = (
            session.execute(select(IngestRun).order_by(IngestRun.id.desc()))
            .scalars()
            .first()
        )

    assert run.counts_json["inserted"] == 0
    assert run.counts_json["updated"] == 0


def test_scryfall_search_finds_forest_after_ingest(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    response = client.get(
        "/api/v1/search?q=Forest&game=mtg",
        headers=_auth_headers("mtg-search", ["read:catalog"]),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Forest" in item["title"] for item in payload)


def test_admin_requires_scope(client):
    response = client.get(
        "/api/v1/admin/ingest/runs",
        headers=_auth_headers("catalog-only", ["read:catalog"]),
    )
    assert response.status_code == 403


def test_tcgdex_bootstrap_incremental_inserts_pokemon(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=True,
            limit=5,
        )
        session.commit()

    with db.SessionLocal() as session:
        source = session.execute(
            select(Source).where(Source.name == "tcgdex_pokemon")
        ).scalar_one()
        pokemon_cards = session.execute(select(func.count(Card.id))).scalar_one()
        source_records = session.execute(
            select(func.count(SourceRecord.id)).where(
                SourceRecord.source_id == source.id
            )
        ).scalar_one()

    assert stats.records_inserted > 0
    assert pokemon_cards > 0
    assert source_records > 0


def test_tcgdex_fixture_incremental_idempotent_has_zero_second_run_changes(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        run = (
            session.execute(select(IngestRun).order_by(IngestRun.id.desc()))
            .scalars()
            .first()
        )

    assert run.counts_json["inserted"] == 0
    assert run.counts_json["updated"] == 0


def test_tcgdex_search_finds_pikachu_after_ingest(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    response = client.get(
        "/api/v1/search?q=Pikachu&game=pokemon",
        headers=_auth_headers("pokemon-search", ["read:catalog"]),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Pikachu" in item["title"] for item in payload)


def test_tcgdex_fixture_bootstrap_after_demo_data_backfills_tcgdex_ids(client):
    fixture_connector = get_connector("fixture_local")
    tcgdex_connector = get_connector("tcgdex_pokemon")

    with db.SessionLocal() as session:
        fixture_connector.run(session, "data/fixtures/pokemon_demo.json")
        session.commit()

    with db.SessionLocal() as session:
        legacy_set = session.execute(
            select(Set).where(Set.name == "Scarlet & Violet")
        ).scalar_one()
        assert legacy_set.tcgdex_id is None

    with db.SessionLocal() as session:
        stats = tcgdex_connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=False,
            limit=2,
        )
        session.commit()

    with db.SessionLocal() as session:
        set_backfills = session.execute(
            select(func.count(Set.id)).where(
                Set.name == "Scarlet & Violet", Set.tcgdex_id.is_not(None)
            )
        ).scalar_one()
        pokemon_game_id = session.execute(
            select(Game.id).where(Game.slug == "pokemon")
        ).scalar_one()
        card_backfills = session.execute(
            select(func.count(Card.id)).where(
                Card.game_id == pokemon_game_id, Card.tcgdex_id.is_not(None)
            )
        ).scalar_one()
        print_backfills = session.execute(
            select(func.count(Print.id)).where(Print.tcgdex_id.is_not(None))
        ).scalar_one()

    assert stats.records_updated > 0
    assert set_backfills >= 1
    assert card_backfills >= 1 or print_backfills >= 1


def test_tcgdex_fixture_ingest_populates_set_tcgdex_ids(client):
    connector = get_connector("tcgdex_pokemon")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=False,
            limit=2,
        )
        session.commit()

    with db.SessionLocal() as session:
        pokemon_game_id = session.execute(
            select(Game.id).where(Game.slug == "pokemon")
        ).scalar_one()
        pokemon_set_ids = session.execute(
            select(func.count(Set.id)).where(
                Set.game_id == pokemon_game_id, Set.tcgdex_id.is_not(None)
            )
        ).scalar_one()

    assert pokemon_set_ids > 0


def test_tcgdex_fixture_ingest_contains_base1_set_when_present(client, tmp_path):
    connector = get_connector("tcgdex_pokemon")

    fixture_payload = {
        "set": {"id": "base1", "abbreviation": {"official": "BS"}, "name": "Base"},
        "cards": [
            {
                "id": "base1-1",
                "localId": "1",
                "name": "Alakazam",
                "image": "https://example.invalid/base1-1",
                "set": {
                    "id": "base1",
                    "abbreviation": {"official": "BS"},
                    "name": "Base",
                    "releaseDate": "1999-01-09",
                },
            }
        ],
    }
    fixture_path = tmp_path / "tcgdex_base1_fixture.json"
    fixture_path.write_text(json.dumps(fixture_payload), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(
            session, str(fixture_path), fixture=True, incremental=False, limit=5
        )
        session.commit()

    with db.SessionLocal() as session:
        pokemon_game_id = session.execute(
            select(Game.id).where(Game.slug == "pokemon")
        ).scalar_one()
        base_set = session.execute(
            select(Set).where(Set.game_id == pokemon_game_id, Set.tcgdex_id == "base1")
        ).scalar_one_or_none()

    assert base_set is not None
    assert base_set.code == "bs"
    assert base_set.name == "Base"


def test_tcgdex_fixture_path_resolution_prefers_data_fixtures_layout(
    client, monkeypatch, tmp_path
):
    connector = get_connector("tcgdex_pokemon")

    backend_root = tmp_path / "backend"
    connectors_dir = backend_root / "app" / "ingest" / "connectors"
    connectors_dir.mkdir(parents=True)

    payload = '{"cards": [{"id": "pikachu"}]}'
    (backend_root / "data" / "fixtures").mkdir(parents=True)
    (backend_root / "data" / "fixtures" / "tcgdex_pokemon_sample.json").write_text(
        payload, encoding="utf-8"
    )

    monkeypatch.setattr(
        "app.ingest.connectors.tcgdex_pokemon.__file__",
        str(connectors_dir / "tcgdex_pokemon.py"),
    )

    default_payloads = connector.load(None, fixture=True, limit=1)
    assert default_payloads


def test_tcgdex_fixture_path_resolution_supports_repo_backend_data_fixtures_layout(
    client, monkeypatch, tmp_path
):
    connector = get_connector("tcgdex_pokemon")

    backend_root = tmp_path / "backend"
    connectors_dir = backend_root / "app" / "ingest" / "connectors"
    connectors_dir.mkdir(parents=True)

    payload = '{"cards": [{"id": "charizard"}]}'
    (tmp_path / "backend" / "data" / "fixtures").mkdir(parents=True)
    (
        tmp_path / "backend" / "data" / "fixtures" / "tcgdex_pokemon_sample.json"
    ).write_text(payload, encoding="utf-8")

    monkeypatch.setattr(
        "app.ingest.connectors.tcgdex_pokemon.__file__",
        str(connectors_dir / "tcgdex_pokemon.py"),
    )

    default_payloads = connector.load(None, fixture=True, limit=1)
    assert default_payloads


def test_tcgdex_remote_load_with_limit_stops_early_and_logs_progress(client, monkeypatch, caplog):
    connector = get_connector("tcgdex_pokemon")
    calls: list[str] = []

    def _fake_request_json(url, params=None):
        calls.append(url)
        if url.endswith("/sets"):
            return [{"id": "sv1"}, {"id": "sv2"}]
        if url.endswith("/sets/sv1"):
            return {
                "id": "sv1",
                "abbreviation": "SV1",
                "name": "Scarlet & Violet",
                "releaseDate": "2023-03-31",
                "cards": [
                    {"id": "sv1-1", "localId": "1", "name": "A", "image": "https://img/1"},
                    {"id": "sv1-2", "localId": "2", "name": "B", "image": "https://img/2"},
                    {"id": "sv1-3", "localId": "3", "name": "C", "image": "https://img/3"},
                ],
            }
        if url.endswith("/sets/sv2"):
            return {
                "id": "sv2",
                "abbreviation": "SV2",
                "name": "Paldea Evolved",
                "releaseDate": "2023-06-09",
                "cards": [{"id": "sv2-1", "localId": "1", "name": "D", "image": "https://img/4"}],
            }
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(connector, "_request_json", _fake_request_json)

    caplog.set_level("INFO")
    payloads = connector.load(None, fixture=False, limit=2, lang="en")

    assert len(payloads) == 2
    assert calls == [
        "https://api.tcgdex.net/v2/en/sets",
        "https://api.tcgdex.net/v2/en/sets/sv1",
    ]
    assert "ingest tcgdex load_start" in caplog.text
    assert "ingest tcgdex load_progress" in caplog.text
    assert "ingest tcgdex load_done" in caplog.text


def test_tcgdex_remote_set_filter_does_not_fetch_per_card_endpoint(client, monkeypatch):
    connector = get_connector("tcgdex_pokemon")
    calls: list[str] = []

    def _fake_request_json(url, params=None):
        calls.append(url)
        if url.endswith("/sets/sv1"):
            return {
                "id": "sv1",
                "abbreviation": "SV1",
                "name": "Scarlet & Violet",
                "releaseDate": "2023-03-31",
                "cards": [
                    {"id": "sv1-1", "localId": "1", "name": "A", "image": "https://img/1"},
                    {"id": "sv1-2", "localId": "2", "name": "B", "image": "https://img/2"},
                ],
            }
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(connector, "_request_json", _fake_request_json)

    payloads = connector.load(None, fixture=False, set="sv1", limit=1, lang="en")

    assert len(payloads) == 1
    assert calls == ["https://api.tcgdex.net/v2/en/sets/sv1"]


def test_tcgdex_remote_load_limit_run_persists_rows(client, monkeypatch):
    connector = get_connector("tcgdex_pokemon")

    def _fake_request_json(url, params=None):
        if url.endswith("/sets"):
            return [{"id": "sv1"}]
        if url.endswith("/sets/sv1"):
            return {
                "id": "sv1",
                "abbreviation": "SV1",
                "name": "Scarlet & Violet",
                "releaseDate": "2023-03-31",
                "cards": [
                    {"id": "sv1-1", "localId": "1", "name": "Sprigatito", "image": "https://img/1"},
                    {"id": "sv1-2", "localId": "2", "name": "Fuecoco", "image": "https://img/2"},
                ],
            }
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(connector, "_request_json", _fake_request_json)

    with db.SessionLocal() as session:
        stats = connector.run(session, None, fixture=False, incremental=False, limit=1)
        session.commit()

    with db.SessionLocal() as session:
        pokemon_game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
        card_count = session.execute(select(func.count(Card.id)).where(Card.game_id == pokemon_game.id)).scalar_one()

    assert stats.files_seen == 1
    assert stats.errors == 0
    assert card_count == 1


def test_tcgdex_fixture_path_resolution_default_none(client):
    connector = get_connector("tcgdex_pokemon")

    default_payloads = connector.load(None, fixture=True, limit=1)
    assert default_payloads


def test_tcgdex_fixture_path_resolution_from_backend_data_directory(client):
    connector = get_connector("tcgdex_pokemon")

    from_directory_payloads = connector.load("backend/data", fixture=True, limit=1)
    assert from_directory_payloads


def test_tcgdex_fixture_path_resolution_from_backend_data_file(client):
    connector = get_connector("tcgdex_pokemon")

    from_file_payloads = connector.load(
        "backend/data/tcgdex_pokemon_sample.json",
        fixture=True,
        limit=1,
    )
    assert from_file_payloads


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http error: {self.status_code}")


def test_tcgdex_remote_set_ingest_fetches_set_only_with_limit(
    client, monkeypatch
):
    connector = get_connector("tcgdex_pokemon")
    requested_urls: list[str] = []

    payloads = {
        "https://api.tcgdex.net/v2/en/sets/base1": {
            "id": "base1",
            "abbreviation": {"official": "BS"},
            "name": "Base",
            "releaseDate": "1999-01-09",
            "cards": [{"id": "base1-1"}, {"id": "base1-2"}],
        },
    }

    def _fake_get(url, params=None, timeout=30):
        requested_urls.append(url)
        return _FakeResponse(payloads[url])

    monkeypatch.setattr("app.ingest.connectors.tcgdex_pokemon.requests.get", _fake_get)

    payloads = connector.load(None, fixture=False, set="base1", lang="en", limit=1)

    assert len(payloads) == 1
    assert requested_urls == ["https://api.tcgdex.net/v2/en/sets/base1"]


def test_tcgdex_remote_without_set_preserves_general_list_behavior(client, monkeypatch):
    connector = get_connector("tcgdex_pokemon")
    requested_urls: list[str] = []

    payloads = {
        "https://api.tcgdex.net/v2/en/sets": [{"id": "base1"}],
        "https://api.tcgdex.net/v2/en/sets/base1": {
            "id": "base1",
            "abbreviation": {"official": "BS"},
            "name": "Base",
            "releaseDate": "1999-01-09",
            "cards": [
                {
                    "id": "base1-1",
                    "localId": "1",
                    "name": "Alakazam",
                    "image": "https://img/base1-1",
                }
            ],
        },
    }

    def _fake_get(url, params=None, timeout=30):
        requested_urls.append(url)
        return _FakeResponse(payloads[url])

    monkeypatch.setattr("app.ingest.connectors.tcgdex_pokemon.requests.get", _fake_get)

    out = connector.load(None, fixture=False, lang="en", limit=10)

    assert len(out) == 1
    assert requested_urls == [
        "https://api.tcgdex.net/v2/en/sets",
        "https://api.tcgdex.net/v2/en/sets/base1",
    ]


def test_tcgdex_fixture_path_resolution_from_app_data_fixtures_directory(
    client, monkeypatch, tmp_path
):
    connector = get_connector("tcgdex_pokemon")

    backend_root = tmp_path / "backend"
    connectors_dir = backend_root / "app" / "ingest" / "connectors"
    connectors_dir.mkdir(parents=True)

    payload = '{"cards": [{"id": "mew"}]}'
    app_fixtures_dir = tmp_path / "app" / "data" / "fixtures"
    app_fixtures_dir.mkdir(parents=True)
    (app_fixtures_dir / "tcgdex_pokemon_sample.json").write_text(
        payload, encoding="utf-8"
    )

    monkeypatch.setattr(
        "app.ingest.connectors.tcgdex_pokemon.__file__",
        str(connectors_dir / "tcgdex_pokemon.py"),
    )

    from_directory_payloads = connector.load(
        str(app_fixtures_dir), fixture=True, limit=1
    )
    assert from_directory_payloads


def test_yugioh_remote_load_paginates_until_limit(monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")

    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    calls: list[dict] = []

    def _fake_get(url, params=None, headers=None, timeout=45):
        calls.append({"url": url, "params": dict(params or {})})
        offset = int((params or {}).get("offset", 0))
        num = int((params or {}).get("num", 0))
        cards = [{"id": offset + i + 1, "name": f"Card {offset + i + 1}"} for i in range(num)]
        return _FakeResponse({"data": cards})

    monkeypatch.setattr("app.ingest.connectors.ygoprodeck_yugioh.requests.get", _fake_get)

    payloads = connector.load(None, fixture=False, limit=2000, page_size=500)

    assert len(payloads) == 2000
    assert len(calls) == 4
    assert [call["params"]["offset"] for call in calls] == [0, 500, 1000, 1500]
    assert all(call["params"]["num"] == 500 for call in calls)


def test_yugioh_remote_load_small_limit_uses_single_request(monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")

    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    calls: list[dict] = []

    def _fake_get(url, params=None, headers=None, timeout=45):
        calls.append({"url": url, "params": dict(params or {})})
        return _FakeResponse(
            {
                "data": [
                    {"id": 101, "name": "Card 101"},
                    {"id": 102, "name": "Card 102"},
                    {"id": 103, "name": "Card 103"},
                ]
            }
        )

    monkeypatch.setattr("app.ingest.connectors.ygoprodeck_yugioh.requests.get", _fake_get)

    payloads = connector.load(None, fixture=False, limit=3, page_size=500)

    assert len(payloads) == 3
    assert len(calls) == 1
    assert calls[0]["params"] == {"num": 3, "offset": 0}


def test_yugioh_remote_load_dedupes_overlapping_pages(monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")

    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    pages = {
        0: [{"id": 1, "name": "Card 1"}, {"id": 2, "name": "Card 2"}],
        2: [{"id": 2, "name": "Card 2"}, {"id": 3, "name": "Card 3"}],
        4: [],
    }

    def _fake_get(url, params=None, headers=None, timeout=45):
        offset = int((params or {}).get("offset", 0))
        return _FakeResponse({"data": pages.get(offset, [])})

    monkeypatch.setattr("app.ingest.connectors.ygoprodeck_yugioh.requests.get", _fake_get)

    payloads = connector.load(None, fixture=False, limit=4, page_size=2)

    ids = [payload[1]["id"] for payload in payloads]
    assert ids == [1, 2, 3]

def test_yugioh_fixture_ingest_inserts_sets_cards_prints(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(
            select(Game).where(Game.slug == "yugioh")
        ).scalar_one_or_none()
        set_count = session.execute(
            select(func.count(Set.id)).where(Set.game_id == game.id)
        ).scalar_one()
        card_count = session.execute(
            select(func.count(Card.id)).where(Card.game_id == game.id)
        ).scalar_one()
        print_count = session.execute(
            select(func.count(Print.id))
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id)
        ).scalar_one()
        null_language_count = session.execute(
            select(func.count(Print.id))
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id, Print.language.is_(None))
        ).scalar_one()

    assert stats.records_inserted > 0
    assert set_count > 0
    assert card_count > 0
    assert print_count > 0
    assert null_language_count == 0


def test_yugioh_fixture_ingest_persists_primary_images(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        image_rows = session.execute(
            select(PrintImage.url, PrintImage.source)
            .join(Print, Print.id == PrintImage.print_id)
            .where(Print.yugioh_id == "46986414::LOB-005::1", PrintImage.is_primary.is_(True))
        ).all()

    assert image_rows
    assert image_rows[0].url == "https://images.ygoprodeck.com/images/cards/46986414.jpg"
    assert image_rows[0].source == "ygoprodeck"


def test_yugioh_incremental_backfills_missing_images_for_existing_card(client):
    connector = get_connector("ygoprodeck_yugioh")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        dark_magician_card_id = session.execute(
            select(Card.id).where(Card.yugoprodeck_id == "46986414")
        ).scalar_one()
        session.query(PrintImage).filter(
            PrintImage.print_id.in_(
                select(Print.id).where(Print.card_id == dark_magician_card_id)
            )
        ).delete(synchronize_session=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        image_urls = session.execute(
            select(PrintImage.url)
            .join(Print, Print.id == PrintImage.print_id)
            .where(Print.yugioh_id == "46986414::LOB-005::1", PrintImage.is_primary.is_(True))
        ).scalars().all()

    assert image_urls == ["https://images.ygoprodeck.com/images/cards/46986414.jpg"]


def test_yugioh_incremental_rehydrates_legacy_row_with_ygo_id_and_missing_key_and_image(client):
    connector = get_connector("ygoprodeck_yugioh")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        print_row = session.execute(
            select(Print).where(Print.yugioh_id == "46986414::LOB-005::1")
        ).scalar_one()
        legacy_print_id = print_row.id

        print_row.print_key = None
        session.query(PrintImage).filter(PrintImage.print_id == legacy_print_id).delete(synchronize_session=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        refreshed = session.get(Print, legacy_print_id)
        ygo_count = session.execute(
            select(func.count(Print.id)).where(Print.yugioh_id == "46986414::LOB-005::1")
        ).scalar_one()
        image_urls = session.execute(
            select(PrintImage.url)
            .where(PrintImage.print_id == legacy_print_id, PrintImage.is_primary.is_(True))
            .order_by(PrintImage.id.asc())
        ).scalars().all()

    assert refreshed is not None
    assert refreshed.print_key is not None
    assert image_urls == ["https://images.ygoprodeck.com/images/cards/46986414.jpg"]
    assert ygo_count == 1


def test_yugioh_incremental_limit_repairs_legacy_print_not_in_processed_subset(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")
    fixture_source = _ygo_fixture_source_path()
    sample_payload = json.loads(fixture_source.read_text(encoding="utf-8"))
    blue_eyes = next(card for card in sample_payload["data"] if card.get("id") == 89631139)
    dark_magician = next(card for card in sample_payload["data"] if card.get("id") == 46986414)
    limited_payload = {"data": [deepcopy(blue_eyes), deepcopy(dark_magician)]}

    fixture_path = tmp_path / "ygo_limited_payload.json"
    fixture_path.write_text(json.dumps(limited_payload), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(fixture_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        print_row = session.execute(
            select(Print).where(Print.yugioh_id == "46986414::LOB-005::1")
        ).scalar_one()
        legacy_print_id = print_row.id
        print_row.print_key = None
        session.query(PrintImage).filter(PrintImage.print_id == legacy_print_id).delete(synchronize_session=False)
        session.commit()

    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            str(fixture_path),
            fixture=True,
            incremental=True,
            limit=1,
        )
        session.commit()

    assert stats.files_seen == 1

    with db.SessionLocal() as session:
        refreshed = session.get(Print, legacy_print_id)
        image_urls = session.execute(
            select(PrintImage.url)
            .where(PrintImage.print_id == legacy_print_id, PrintImage.is_primary.is_(True))
            .order_by(PrintImage.id.asc())
        ).scalars().all()

    assert refreshed is not None
    assert refreshed.print_key is not None
    assert image_urls == ["https://images.ygoprodeck.com/images/cards/46986414.jpg"]


def test_yugioh_incremental_backfills_partial_missing_images_for_blue_eyes(client):
    connector = get_connector("ygoprodeck_yugioh")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        blue_eyes_card_id = session.execute(
            select(Card.id).where(Card.yugoprodeck_id == "89631139")
        ).scalar_one()

        # Leave one image in place and remove another to emulate historical partial backfill.
        target_print_id = session.execute(
            select(Print.id).where(Print.yugioh_id == "89631139::LOB-001::1")
        ).scalar_one()
        session.query(PrintImage).filter(PrintImage.print_id == target_print_id).delete(synchronize_session=False)

        # Ensure card still has at least one primary image in another print so skip logic must be granular.
        other_print_id = session.execute(
            select(Print.id).where(Print.card_id == blue_eyes_card_id, Print.id != target_print_id).limit(1)
        ).scalar_one_or_none()
        if other_print_id is None:
            session.add(
                Print(
                    set_id=session.execute(select(Print.set_id).where(Print.id == target_print_id)).scalar_one(),
                    card_id=blue_eyes_card_id,
                    collector_number="LOB-001-ALT",
                    language="en",
                    rarity="Ultra Rare",
                    variant="ultra-rare",
                    yugioh_id="89631139::LOB-001-ALT::99",
                )
            )
            session.flush()
            other_print_id = session.execute(
                select(Print.id).where(Print.yugioh_id == "89631139::LOB-001-ALT::99")
            ).scalar_one()
        has_primary = session.execute(
            select(PrintImage.id).where(PrintImage.print_id == other_print_id, PrintImage.is_primary.is_(True))
        ).scalar_one_or_none()
        if has_primary is None:
            session.add(
                PrintImage(
                    print_id=other_print_id,
                    url="https://images.ygoprodeck.com/images/cards/89631139.jpg",
                    is_primary=True,
                    source="ygoprodeck",
                )
            )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        restored = session.execute(
            select(PrintImage.url)
            .join(Print, Print.id == PrintImage.print_id)
            .where(Print.yugioh_id == "89631139::LOB-001::1", PrintImage.is_primary.is_(True))
        ).scalars().all()

    assert restored == ["https://images.ygoprodeck.com/images/cards/89631139.jpg"]


def test_yugioh_missing_rarity_defaults_to_unknown_without_integrity_error(
    client, tmp_path
):
    connector = get_connector("ygoprodeck_yugioh")
    fixture = {
        "data": [
            {
                "id": 999001,
                "name": "Missing Rarity Card",
                "card_sets": [
                    {
                        "set_name": "Null Rarity Set",
                        "set_code": "NRS-001",
                        "set_rarity": None,
                    },
                    {
                        "set_name": "Empty Rarity Set",
                        "set_code": "ERS-002",
                        "set_rarity": "   ",
                    },
                ],
            }
        ]
    }
    fixture_path = tmp_path / "ygo_missing_rarity.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        stats = connector.run(
            session, str(fixture_path), fixture=True, incremental=False
        )
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "yugioh")).scalar_one()
        rarities = (
            session.execute(
                select(Print.rarity)
                .join(Set, Set.id == Print.set_id)
                .where(
                    Set.game_id == game.id,
                    Print.collector_number.in_(["NRS-001", "ERS-002"]),
                )
                .order_by(Print.collector_number.asc())
            )
            .scalars()
            .all()
        )

    assert stats.records_inserted > 0
    assert rarities == ["unknown", "unknown"]


def test_yugioh_variant_is_derived_from_rarity_and_slugged(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")
    fixture = {
        "data": [
            {
                "id": 999003,
                "name": "Variant Source Card",
                "card_sets": [
                    {
                        "set_name": "Variant Set",
                        "set_code": "VRS-001",
                        "set_rarity": "Ultra Rare",
                    },
                    {
                        "set_name": "Variant Set",
                        "set_code": "VRS-001",
                        "set_rarity": "Collector/Rare",
                    },
                ],
            }
        ]
    }
    fixture_path = tmp_path / "ygo_variant_slug.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(fixture_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        variants = (
            session.execute(
                select(Print.variant)
                .where(Print.collector_number == "VRS-001")
                .order_by(Print.variant.asc())
            )
            .scalars()
            .all()
        )

    assert variants == ["collector-rare", "ultra-rare"]


def test_yugioh_incremental_does_not_overwrite_existing_variant(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")

    initial_fixture = {
        "data": [
            {
                "id": 999004,
                "name": "Existing Variant Card",
                "card_sets": [
                    {
                        "set_name": "Existing Variant Set",
                        "set_code": "EVS-001",
                        "set_rarity": "Starlight Rare",
                    }
                ],
            }
        ]
    }
    incremental_fixture = {
        "data": [
            {
                "id": 999004,
                "name": "Existing Variant Card",
                "card_sets": [
                    {
                        "set_name": "Existing Variant Set",
                        "set_code": "EVS-001",
                        "set_rarity": None,
                        "rarity": None,
                        "set_rarity_code": None,
                        "set_rarity_short": None,
                    }
                ],
            }
        ]
    }

    initial_path = tmp_path / "ygo_variant_initial.json"
    initial_path.write_text(json.dumps(initial_fixture), encoding="utf-8")
    incremental_path = tmp_path / "ygo_variant_incremental.json"
    incremental_path.write_text(json.dumps(incremental_fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(initial_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, str(incremental_path), fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        variant = session.execute(
            select(Print.variant).where(Print.collector_number == "EVS-001")
        ).scalar_one()

    assert variant == "starlight-rare"


def test_yugioh_incremental_sparse_payload_prefers_specific_existing_variant_row(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")

    initial_fixture = {
        "data": [
            {
                "id": 999024,
                "name": "Competing Variant Card",
                "card_sets": [
                    {
                        "set_name": "Competing Variant Set",
                        "set_code": "CVS-005",
                        "set_rarity": "Starlight Rare",
                    }
                ],
            }
        ]
    }
    incremental_fixture = {
        "data": [
            {
                "id": 999024,
                "name": "Competing Variant Card",
                "card_sets": [
                    {
                        "set_name": "Competing Variant Set",
                        "set_code": "CVS-005",
                        "set_rarity": None,
                        "rarity": None,
                        "set_rarity_code": None,
                        "set_rarity_short": None,
                    }
                ],
            }
        ]
    }

    initial_path = tmp_path / "ygo_variant_compete_initial.json"
    initial_path.write_text(json.dumps(initial_fixture), encoding="utf-8")
    incremental_path = tmp_path / "ygo_variant_compete_incremental.json"
    incremental_path.write_text(json.dumps(incremental_fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(initial_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        specific_row = session.execute(
            select(Print).where(Print.collector_number == "CVS-005", Print.variant == "starlight-rare")
        ).scalar_one()
        session.add(
            Print(
                set_id=specific_row.set_id,
                card_id=specific_row.card_id,
                collector_number="CVS-005",
                language="en",
                rarity="unknown",
                variant="default",
                is_foil=False,
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, str(incremental_path), fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        rows = session.execute(
            select(Print.variant, Print.yugioh_id)
            .where(Print.collector_number == "CVS-005")
            .order_by(Print.id.asc())
        ).all()

    assert rows
    specific_rows = [row for row in rows if row.variant == "starlight-rare"]
    default_rows = [row for row in rows if row.variant == "default"]
    assert len(specific_rows) == 1
    assert len(default_rows) == 1
    assert specific_rows[0].yugioh_id == "999024::CVS-005::1"
    assert default_rows[0].yugioh_id is None


def test_yugioh_incremental_updates_variant_when_new_payload_is_more_specific(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")

    initial_fixture = {
        "data": [
            {
                "id": 999014,
                "name": "Upgrade Variant Card",
                "card_sets": [
                    {
                        "set_name": "Upgrade Variant Set",
                        "set_code": "UVS-001",
                        "set_rarity": None,
                    }
                ],
            }
        ]
    }
    incremental_fixture = {
        "data": [
            {
                "id": 999014,
                "name": "Upgrade Variant Card",
                "card_sets": [
                    {
                        "set_name": "Upgrade Variant Set",
                        "set_code": "UVS-001",
                        "set_rarity": "Secret Rare",
                    }
                ],
            }
        ]
    }

    initial_path = tmp_path / "ygo_variant_upgrade_initial.json"
    initial_path.write_text(json.dumps(initial_fixture), encoding="utf-8")
    incremental_path = tmp_path / "ygo_variant_upgrade_incremental.json"
    incremental_path.write_text(json.dumps(incremental_fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(initial_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, str(incremental_path), fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        row = session.execute(
            select(Print.variant, Print.rarity).where(Print.collector_number == "UVS-001")
        ).one()

    assert row.variant == "secret-rare"
    assert row.rarity == "Secret Rare"


def test_yugioh_missing_language_and_rarity_default_without_none(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")
    fixture = {
        "data": [
            {
                "id": 999002,
                "name": "Missing Fields Card",
                "card_sets": [
                    {
                        "set_name": "Missing Fields Set",
                        "set_code": "MFS-001",
                        "set_rarity": None,
                        "set_language": None,
                    }
                ],
            }
        ]
    }
    fixture_path = tmp_path / "ygo_missing_fields.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(fixture_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        row = session.execute(
            select(Print.language, Print.rarity, Print.variant).where(
                Print.collector_number == "MFS-001"
            )
        ).first()

    assert row is not None
    assert row.language == "en"
    assert row.rarity == "unknown"
    assert row.variant == "default"


def test_yugioh_full_refresh_reuses_print_identity_across_cards(client, tmp_path):
    connector = get_connector("ygoprodeck_yugioh")

    first_fixture = {
        "data": [
            {
                "id": 910001,
                "name": "First Identity Card",
                "card_sets": [
                    {
                        "set_name": "Battle Pack",
                        "set_code": "BLCR-EN015",
                        "set_rarity": "Secret Rare",
                        "set_language": "en",
                    }
                ],
            }
        ]
    }
    second_fixture = {
        "data": [
            {
                "id": 910002,
                "name": "Second Identity Card",
                "card_sets": [
                    {
                        "set_name": "Battle Pack",
                        "set_code": "BLCR-EN015",
                        "set_rarity": "Secret Rare",
                        "set_language": "en",
                    }
                ],
            }
        ]
    }

    first_path = tmp_path / "ygo_identity_first.json"
    first_path.write_text(json.dumps(first_fixture), encoding="utf-8")
    second_path = tmp_path / "ygo_identity_second.json"
    second_path.write_text(json.dumps(second_fixture), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, str(first_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, str(second_path), fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        rows = session.execute(
            select(Print.card_id, Print.yugioh_id, Print.rarity, Print.language, Print.variant)
            .where(
                Print.collector_number == "BLCR-EN015",
                Print.language == "en",
                Print.variant == "secret-rare",
                Print.is_foil.is_(False),
            )
        ).all()

        second_card_id = session.execute(
            select(Card.id).where(Card.yugoprodeck_id == "910002")
        ).scalar_one()

    assert len(rows) == 1
    row = rows[0]
    assert row.card_id == second_card_id
    assert row.yugioh_id == "910002::BLCR-EN015::1"
    assert row.rarity == "Secret Rare"
    assert row.language == "en"
    assert row.variant == "secret-rare"


def test_yugioh_normalize_deduplicates_duplicate_prints_in_payload(client):
    connector = get_connector("ygoprodeck_yugioh")
    payload = {
        "id": 910003,
        "name": "Duplicated Print Card",
        "card_sets": [
            {
                "set_name": "Battle Pack",
                "set_code": "BLCR-EN015",
                "set_rarity": "Secret Rare",
                "set_language": "en",
            },
            {
                "set_name": "Battle Pack",
                "set_code": "BLCR-EN015",
                "set_rarity": "Secret Rare",
                "set_language": "en",
            },
        ],
    }

    normalized = connector.normalize(payload)

    assert len(normalized["prints"]) == 1
    assert normalized["prints"][0]["set_code"] == "blcr-en015"
    assert normalized["prints"][0]["collector_number"] == "BLCR-EN015"
    assert normalized["prints"][0]["language"] == "en"
    assert normalized["prints"][0]["variant"] == "secret-rare"

def test_prints_unique_constraint_allows_same_identity_with_different_variant(client):
    with db.SessionLocal() as session:
        game = Game(slug="variant-game", name="Variant Game")
        session.add(game)
        session.flush()

        set_row = Set(game_id=game.id, code="v1", name="Variant Set")
        card_row = Card(game_id=game.id, name="Variant Card")
        session.add_all([set_row, card_row])
        session.flush()

        first = Print(
            set_id=set_row.id,
            card_id=card_row.id,
            collector_number="001",
            language="en",
            rarity="unknown",
            is_foil=False,
            variant="default",
        )
        second = Print(
            set_id=set_row.id,
            card_id=card_row.id,
            collector_number="001",
            language="en",
            rarity="unknown",
            is_foil=False,
            variant="alt-art",
        )
        session.add_all([first, second])
        session.commit()

    with db.SessionLocal() as session:
        variants = (
            session.execute(
                select(Print.variant)
                .where(Print.collector_number == "001")
                .order_by(Print.variant.asc())
            )
            .scalars()
            .all()
        )

    assert variants == ["alt-art", "default"]


def test_pokemon_prints_default_variant(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one()
        variants = (
            session.execute(
                select(Print.variant)
                .join(Set, Set.id == Print.set_id)
                .where(Set.game_id == game.id)
                .distinct()
            )
            .scalars()
            .all()
        )

    assert variants == ["default"]


def test_riftbound_fixture_ingest_inserts_sets_cards_prints(client):
    connector = get_connector("riftbound")
    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/riftbound_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(
            select(Game).where(Game.slug == "riftbound")
        ).scalar_one_or_none()
        set_count = session.execute(
            select(func.count(Set.id)).where(Set.game_id == game.id)
        ).scalar_one()
        card_count = session.execute(
            select(func.count(Card.id)).where(Card.game_id == game.id)
        ).scalar_one()
        print_count = session.execute(
            select(func.count(Print.id))
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id)
        ).scalar_one()
        null_language_count = session.execute(
            select(func.count(Print.id))
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id, Print.language.is_(None))
        ).scalar_one()
        null_rarity_count = session.execute(
            select(func.count(Print.id))
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id, Print.rarity.is_(None))
        ).scalar_one()

    assert stats.records_inserted > 0
    assert set_count > 0
    assert card_count > 0
    assert print_count > 0
    assert null_language_count == 0
    assert null_rarity_count == 0


def test_scryfall_ingest_sets_single_primary_print_image(client):
    connector = get_connector("scryfall_mtg")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=False,
            limit=1,
        )
        connector.run(
            session,
            "data/fixtures/scryfall_mtg_sample.json",
            fixture=True,
            incremental=False,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        print_row = session.execute(select(Print).where(Print.scryfall_id.is_not(None))).scalars().first()
        assert print_row is not None
        images = session.execute(select(PrintImage).where(PrintImage.print_id == print_row.id)).scalars().all()

    assert len(images) == 1
    assert images[0].is_primary is True
    assert images[0].source == "scryfall"


def test_tcgdex_ingest_sets_primary_print_image(client):
    connector = get_connector("tcgdex_pokemon")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/tcgdex_pokemon_sample.json",
            fixture=True,
            incremental=False,
            limit=1,
        )
        session.commit()

    with db.SessionLocal() as session:
        print_row = session.execute(select(Print).where(Print.tcgdex_id.is_not(None))).scalars().first()
        assert print_row is not None
        image = session.execute(
            select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
        ).scalar_one_or_none()

    assert image is not None
    assert image.url.endswith('/high.webp')
    assert image.source == "tcgdex"


def test_riftbound_ingest_persists_primary_image_from_fixture(client):
    connector = get_connector("riftbound")

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/riftbound_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    with db.SessionLocal() as session:
        print_row = session.execute(select(Print).where(Print.riftbound_id == "rb-print-1")).scalar_one_or_none()
        assert print_row is not None
        image = session.execute(
            select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
        ).scalar_one_or_none()

    assert image is not None
    assert image.url == "https://example.com/riftbound/rb1/001.png"
    assert image.source == "riftbound"


def test_ygoprodeck_incremental_reindex_is_scoped_to_touched_entities(client, monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")
    reindex_calls = []

    def _fake_rebuild(session, card_ids=None, set_ids=None, print_ids=None):
        reindex_calls.append(
            {
                "card_ids": set(card_ids or set()),
                "set_ids": set(set_ids or set()),
                "print_ids": set(print_ids or set()),
            }
        )
        return {"cards": 0, "sets": 0, "prints": 0}

    monkeypatch.setattr("app.ingest.base.rebuild_search_documents", _fake_rebuild)

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
            limit=2,
        )
        session.commit()

    assert len(reindex_calls) == 1
    assert reindex_calls[0]["card_ids"]
    assert reindex_calls[0]["set_ids"]
    assert reindex_calls[0]["print_ids"]


def test_ygoprodeck_incremental_skips_reindex_when_no_payload_changes(client, monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")
    reindex_calls = []

    def _fake_rebuild(session, card_ids=None, set_ids=None, print_ids=None):
        reindex_calls.append((card_ids, set_ids, print_ids))
        return {"cards": 0, "sets": 0, "prints": 0}

    monkeypatch.setattr("app.ingest.base.rebuild_search_documents", _fake_rebuild)

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
            limit=2,
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
            limit=2,
        )
        session.commit()

    assert len(reindex_calls) == 1


def test_yugioh_incremental_rehydrates_legacy_prints_missing_print_key_and_primary_images(client):
    connector = get_connector("ygoprodeck_yugioh")

    with db.SessionLocal() as session:
        game = Game(slug="yugioh", name="Yu-Gi-Oh!")
        session.add(game)
        session.flush()

        dark_magician = Card(game_id=game.id, name="Dark Magician", yugoprodeck_id="46986414")
        blue_eyes = Card(game_id=game.id, name="Blue-Eyes White Dragon", yugoprodeck_id="89631139")
        session.add_all([dark_magician, blue_eyes])
        session.flush()

        lob_set = Set(
            game_id=game.id,
            code="lob",
            name="Legend of Blue Eyes White Dragon",
            yugioh_id="LOB",
        )
        session.add(lob_set)
        session.flush()

        dark_magician_print = Print(
            set_id=lob_set.id,
            card_id=dark_magician.id,
            collector_number="LOB-005",
            language="en",
            rarity="Ultra Rare",
            variant="default",
            yugioh_id="46986414::LOB-005::1",
            print_key=None,
        )
        blue_eyes_print = Print(
            set_id=lob_set.id,
            card_id=blue_eyes.id,
            collector_number="LOB-001",
            language="en",
            rarity="Ultra Rare",
            variant="glossy",
            yugioh_id="89631139::LOB-001::1",
            print_key=None,
        )
        session.add_all([dark_magician_print, blue_eyes_print])
        session.commit()

    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/ygoprodeck_yugioh_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    assert stats.records_updated >= 2

    with db.SessionLocal() as session:
        recovered_rows = session.execute(
            select(
                Print.collector_number,
                Print.variant,
                Print.print_key,
                Print.yugioh_id,
                func.count(PrintImage.id).label("primary_count"),
            )
            .outerjoin(
                PrintImage,
                (PrintImage.print_id == Print.id) & (PrintImage.is_primary.is_(True)),
            )
            .where(Print.yugioh_id.in_(["46986414::LOB-005::1", "89631139::LOB-001::1"]))
            .group_by(Print.id)
            .order_by(Print.collector_number.asc())
        ).all()

    assert len(recovered_rows) == 2
    by_collector = {row.collector_number: row for row in recovered_rows}
    assert by_collector["LOB-005"].print_key is not None
    assert by_collector["LOB-001"].print_key is not None
    assert by_collector["LOB-005"].primary_count == 1
    assert by_collector["LOB-001"].primary_count == 1
    # Existing non-default variant must be preserved for sparse payload compatibility.
    assert by_collector["LOB-001"].variant == "glossy"

    with db.SessionLocal() as session:
        duplicates = session.execute(
            select(func.count(Print.id)).where(
                Print.yugioh_id.in_(["46986414::LOB-005::1", "89631139::LOB-001::1"])
            )
        ).scalar_one()
    assert duplicates == 2


def test_yugioh_incremental_remote_mode_limit_terminates_and_persists_rows(client, monkeypatch):
    connector = get_connector("ygoprodeck_yugioh")

    fixture_path = (
        Path(__file__).resolve().parents[1] / "data" / "fixtures" / "ygoprodeck_yugioh_sample.json"
    )
    fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    seed_cards = fixture_payload.get("data") or []
    fixture_cards = []
    for idx in range(10):
        template = deepcopy(seed_cards[idx % len(seed_cards)])
        card_id = int(template["id"]) + (idx * 1000000)
        template["id"] = card_id
        template["name"] = f"{template['name']} Batch {idx+1}"
        for set_idx, set_payload in enumerate(template.get("card_sets") or []):
            original_code = set_payload.get("set_code") or "SET"
            set_payload["set_code"] = f"{original_code}-{idx+1}-{set_idx+1}"
        for image in template.get("card_images") or []:
            image["id"] = card_id
            if image.get("image_url"):
                image["image_url"] = image["image_url"].replace(str(seed_cards[idx % len(seed_cards)]["id"]), str(card_id))
            if image.get("image_url_small"):
                image["image_url_small"] = image["image_url_small"].replace(str(seed_cards[idx % len(seed_cards)]["id"]), str(card_id))
            if image.get("image_url_cropped"):
                image["image_url_cropped"] = image["image_url_cropped"].replace(str(seed_cards[idx % len(seed_cards)]["id"]), str(card_id))
        fixture_cards.append(template)

    def _fake_load_remote(limit=None, base_url=None, page_size=None):
        if limit is None:
            return fixture_cards
        return fixture_cards[:limit]

    monkeypatch.setattr(connector, "_load_remote", _fake_load_remote)

    with db.SessionLocal() as session:
        stats = connector.run(session, incremental=True, limit=10)
        session.commit()

    assert stats.errors == 0
    assert stats.files_seen == 10

    with db.SessionLocal() as session:
        games = session.execute(select(func.count(Game.id))).scalar_one()
        cards = session.execute(select(func.count(Card.id))).scalar_one()
        prints = session.execute(select(func.count(Print.id))).scalar_one()
        primary_images = session.execute(
            select(func.count(PrintImage.id)).where(PrintImage.is_primary.is_(True))
        ).scalar_one()

    assert games >= 1
    assert cards > 0
    assert prints > 0
    assert primary_images > 0

def test_riftbound_remote_load_supports_limit_and_dedupe(monkeypatch):
    connector = get_connector("riftbound")

    payload = {
        "sets": [{"id": "rb1", "code": "RB1", "name": "Rift One"}],
        "cards": [{"id": "card-1", "name": "Alpha"}],
        "prints": [
            {"id": "rb-print-1", "set_id": "rb1", "card_id": "card-1", "collector_number": "001"},
            {"id": "rb-print-1", "set_id": "rb1", "card_id": "card-1", "collector_number": "001"},
            {"id": "rb-print-2", "set_id": "rb1", "card_id": "card-1", "collector_number": "002"},
        ],
    }

    monkeypatch.setattr(connector, "_request_json", lambda url, params=None: payload)

    loaded = connector.load(fixture=False, limit=1)

    assert len(loaded) == 1
    assert loaded[0][1]["print"]["id"] == "rb-print-1"


def test_scryfall_upsert_returns_touched_entity_ids(client):
    connector = get_connector("scryfall_mtg")
    payload = {
        "set": {"code": "lea", "name": "Limited Edition Alpha", "released_at": "1993-08-05"},
        "card": {
            "id": "00000000-0000-0000-0000-000000000001",
            "oracle_id": "00000000-0000-0000-0000-000000000002",
            "set": "lea",
            "set_name": "Limited Edition Alpha",
            "released_at": "1993-08-05",
            "name": "Black Lotus",
            "collector_number": "233",
            "lang": "en",
            "rarity": "rare",
            "foil": False,
            "image_uris": {"normal": "https://example.com/black-lotus.png"},
        },
    }

    with db.SessionLocal() as session:
        stats = IngestStats()
        touched = connector.upsert(session, payload, stats)
        session.commit()

    assert touched.get("card_id")
    assert touched.get("set_id")
    assert touched.get("print_id")
