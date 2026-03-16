import json
from copy import deepcopy
from pathlib import Path

import pytest
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
    PrintIdentifier,
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


def test_fixture_local_accepts_game_as_string_slug(client):
    connector = get_connector("fixture_local")
    payload = {
        "game": "riftbound",
        "sets": [{"code": "rbx", "name": "Riftbound X"}],
        "cards": [{"name": "Compat Card"}],
        "prints": [
            {
                "set_code": "rbx",
                "card_name": "Compat Card",
                "collector_number": "001",
                "language": "en",
                "rarity": "rare",
                "is_foil": False,
            }
        ],
    }

    with db.SessionLocal() as session:
        stats = IngestStats()
        connector.upsert(session, payload, stats)
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "riftbound")).scalar_one_or_none()
    assert game is not None
    assert game.name == "Riftbound"


def test_fixture_local_accepts_game_as_dict_slug(client):
    connector = get_connector("fixture_local")
    payload = {
        "game": {"slug": "pokemon", "name": "Pokémon TCG"},
        "sets": [{"code": "svp", "name": "Scarlet & Violet Promos"}],
        "cards": [{"name": "Compat Pikachu"}],
        "prints": [
            {
                "set_code": "svp",
                "card_name": "Compat Pikachu",
                "collector_number": "123",
                "language": "en",
                "rarity": "common",
                "is_foil": False,
            }
        ],
    }

    with db.SessionLocal() as session:
        stats = IngestStats()
        connector.upsert(session, payload, stats)
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
    assert game is not None
    assert game.name == "Pokémon TCG"


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
    assert image.url == "/images/riftbound/rb1-placeholder.svg"
    assert image.source == "riftbound"


def test_riftbound_ingest_replaces_disallowed_domains_and_missing_images(client, tmp_path):
    connector = get_connector("riftbound")
    fixture_path = Path("data/fixtures/riftbound_sample.json")
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    payload["prints"][0]["primary_image_url"] = "https://images.riftbound.cards/sets/rb1/001-borderless-en.webp"
    payload["prints"][14].pop("primary_image_url", None)

    custom_fixture = tmp_path / "riftbound_custom.json"
    custom_fixture.write_text(json.dumps(payload), encoding="utf-8")

    with db.SessionLocal() as session:
        connector.run(session, custom_fixture, fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        riftbound_game_id = session.execute(select(Game.id).where(Game.slug == "riftbound")).scalar_one()
        rows = session.execute(
            select(Print.riftbound_id, PrintImage.url)
            .join(PrintImage, PrintImage.print_id == Print.id)
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == riftbound_game_id, PrintImage.is_primary.is_(True))
        ).all()

    assert rows
    assert all("images.riftbound.cards" not in url for _, url in rows)
    row_map = {print_id: url for print_id, url in rows}
    assert row_map["rb-print-1"] == "/images/riftbound/rb1-placeholder.svg"
    assert row_map["rb-print-15"] == "/images/riftbound/rb2-placeholder.svg"


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

def test_riftbound_selects_fallback_backend_when_requested(client, monkeypatch):
    connector = get_connector("riftbound")
    monkeypatch.setenv("RIFTBOUND_SOURCE", "fallback")

    payloads = connector.load("data/fixtures/riftbound_sample.json", fixture=True, limit=1)

    assert payloads
    _, payload, _ = payloads[0]
    assert payload["print"]["source_system"] == "riftcodex"


def test_riftbound_selects_official_backend_in_auto_with_credentials(client, monkeypatch):
    connector = get_connector("riftbound")
    monkeypatch.setenv("RIFTBOUND_SOURCE", "auto")
    monkeypatch.setenv("RIFTBOUND_API_BASE_URL", "https://riot.example/content")
    monkeypatch.setenv("RIFTBOUND_API_KEY", "token")

    calls: list[str] = []

    class _FakeOfficialBackend:
        source_name = "official"

        @staticmethod
        def is_configured() -> bool:
            return True

        def fetch_all(self, **kwargs):
            calls.append("official")
            from app.ingest.connectors.riftbound_types import RiftboundBatch

            return RiftboundBatch(
                sets=[{"id": "s1", "code": "RB1", "name": "Foundations"}],
                cards=[{"id": "c1", "name": "Kai'Sa, Void Skirmisher"}],
                prints=[
                    {
                        "id": "p1",
                        "set_id": "s1",
                        "card_id": "c1",
                        "collector_number": "001",
                        "rarity": "mythic",
                        "language": "en",
                        "variant": "default",
                        "images": {"large": "https://cdn.riot.example/p1.webp"},
                    }
                ],
            )

        def to_logical_records(self, batch, **kwargs):
            from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend

            return RiftboundOfficialBackend(connector.logger).to_logical_records(batch)

    class _FakeFallbackBackend:
        source_name = "fallback"

    monkeypatch.setattr(connector, "_build_backends", lambda: (_FakeOfficialBackend(), _FakeFallbackBackend()))

    payloads = connector.load(fixture=False, limit=1)

    assert calls == ["official"]
    assert payloads[0][1]["print"]["source_system"] == "riot_riftbound_content_v1"


def test_riftbound_official_mode_requires_configuration(client, monkeypatch):
    connector = get_connector("riftbound")
    monkeypatch.setenv("RIFTBOUND_SOURCE", "official")
    monkeypatch.delenv("RIFTBOUND_API_BASE_URL", raising=False)
    monkeypatch.delenv("RIFTBOUND_API_KEY", raising=False)

    try:
        connector.load(fixture=False)
        assert False, "expected RuntimeError when official mode is missing credentials"
    except RuntimeError as exc:
        assert "missing official configuration" in str(exc)


def test_riftbound_official_backend_uses_x_riot_token_header_and_content_endpoint(client, monkeypatch):
    from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend

    monkeypatch.setenv("RIFTBOUND_API_BASE_URL", "https://americas.api.riotgames.com")
    monkeypatch.setenv("RIFTBOUND_API_KEY", "test-token")

    calls: list[dict] = []

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"sets": []}

    def _fake_get(url, timeout=None, params=None):
        calls.append({"url": url, "timeout": timeout, "params": params})
        return _FakeResponse()

    backend = RiftboundOfficialBackend(get_connector("riftbound").logger)
    monkeypatch.setattr(backend.session, "get", _fake_get)

    backend.fetch_all(locale="en")

    assert calls[0]["url"] == "https://americas.api.riotgames.com/riftbound/content/v1/contents"
    assert calls[0]["params"] == {"locale": "en"}
    assert backend.session.headers.get("X-Riot-Token") == "test-token"
    assert "Authorization" not in backend.session.headers


def test_riftbound_auto_falls_back_when_official_forbidden_403(client, monkeypatch):
    connector = get_connector("riftbound")
    monkeypatch.setenv("RIFTBOUND_SOURCE", "auto")
    monkeypatch.setenv("RIFTBOUND_API_BASE_URL", "https://americas.api.riotgames.com")
    monkeypatch.setenv("RIFTBOUND_API_KEY", "token")

    calls: list[str] = []

    class _FakeOfficialBackend:
        source_name = "official"

        @staticmethod
        def is_configured() -> bool:
            return True

        @staticmethod
        def fetch_all(**kwargs):
            raise RuntimeError(
                'Riftbound official request failed url=https://americas.api.riotgames.com/riftbound/content/v1/contents '
                'status=403 body={"status":{"message":"Forbidden","status_code":403}}'
            )

        @staticmethod
        def to_logical_records(batch, **kwargs):
            return []

    class _FakeFallbackBackend:
        source_name = "fallback"

        def fetch_all(self, **kwargs):
            calls.append("fallback")
            from app.ingest.connectors.riftbound_types import RiftboundBatch

            return RiftboundBatch(
                sets=[{"id": "s1", "code": "RB1", "name": "Foundations"}],
                cards=[{"id": "c1", "name": "Kai'Sa, Void Skirmisher"}],
                prints=[
                    {
                        "id": "fp1",
                        "set_id": "s1",
                        "card_id": "c1",
                        "collector_number": "001",
                        "rarity": "mythic",
                        "language": "en",
                        "variant": "default",
                        "primary_image_url": "/images/riftbound/rb1-placeholder.svg",
                    }
                ],
            )

        @staticmethod
        def to_logical_records(batch, **kwargs):
            from app.ingest.connectors.riftbound_fallback import RiftboundFallbackBackend

            return RiftboundFallbackBackend(get_connector("riftbound").logger).to_logical_records(batch)

    monkeypatch.setattr(connector, "_build_backends", lambda: (_FakeOfficialBackend(), _FakeFallbackBackend()))

    payloads = connector.load(fixture=False)

    assert calls == ["fallback"]
    assert payloads
    assert payloads[0][1]["print"]["source_system"] == "riftcodex"


def test_riftbound_official_403_error_is_actionable(client, monkeypatch):
    from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend

    monkeypatch.setenv("RIFTBOUND_API_BASE_URL", "https://americas.api.riotgames.com")
    monkeypatch.setenv("RIFTBOUND_API_KEY", "token")

    class _FakeResponse:
        status_code = 403
        text = '{"status":{"message":"Forbidden","status_code":403}}'

        @staticmethod
        def json() -> dict:
            return {"status": {"message": "Forbidden", "status_code": 403}}

    backend = RiftboundOfficialBackend(get_connector("riftbound").logger)
    monkeypatch.setattr(backend.session, "get", lambda *args, **kwargs: _FakeResponse())

    try:
        backend.fetch_all()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        message = str(exc)
        assert "url=https://americas.api.riotgames.com/riftbound/content/v1/contents" in message
        assert "status=403" in message
        assert "Forbidden" in message
        assert "does not have valid authorization for Riftbound content" in message
        assert "product/API entitlement for riftbound-content-v1" in message


def test_riftbound_normalization_is_homogeneous_between_official_and_fallback(client):
    connector = get_connector("riftbound")
    fallback_payloads = connector.load("data/fixtures/riftbound_fallback_like.json", fixture=True)

    official_payload = json.loads(Path("data/fixtures/riftbound_official_like.json").read_text(encoding="utf-8"))
    from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend
    backend = RiftboundOfficialBackend(connector.logger)
    records = backend.to_logical_records(backend.fetch_all_from_content(official_payload))
    official_rows = [connector.normalize(connector._logical_to_payload(item)) for item in records]
    fallback_rows = [connector.normalize(payload) for _, payload, _ in fallback_payloads[:2]]

    assert [row["set"]["code"] for row in official_rows] == [row["set"]["code"] for row in fallback_rows]
    assert [row["card"]["name"] for row in official_rows] == [row["card"]["name"] for row in fallback_rows]
    assert [row["print"]["collector_number"] for row in official_rows] == [row["print"]["collector_number"] for row in fallback_rows]


def test_riftbound_official_payload_parsing_and_image_fallback(client):
    connector = get_connector("riftbound")
    official_payload = json.loads(Path("data/fixtures/riftbound_official_like.json").read_text(encoding="utf-8"))
    from app.ingest.connectors.riftbound_official import RiftboundOfficialBackend

    backend = RiftboundOfficialBackend(connector.logger)
    batch = backend.fetch_all_from_content(official_payload)
    records = backend.to_logical_records(batch)
    normalized_rows = [connector.normalize(connector._logical_to_payload(item)) for item in records]

    assert normalized_rows[0]["print"]["primary_image_url"] == "https://cdn.riot.example/rb1/001-full.webp"
    assert normalized_rows[1]["print"]["primary_image_url"].startswith("/images/riftbound/")



def test_riftbound_fixture_incremental_idempotent_second_run_skips_existing(client):
    connector = get_connector("riftbound")

    with db.SessionLocal() as session:
        first = connector.run(
            session,
            "data/fixtures/riftbound_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        second = connector.run(
            session,
            "data/fixtures/riftbound_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    assert first.records_inserted > 0
    assert second.records_inserted == 0
    assert second.records_updated == 0
    assert second.files_skipped > 0

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


def test_onepiece_fixture_ingest_persists_sets_cards_prints_and_images(client):
    connector = get_connector("onepiece")
    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            "data/fixtures/onepiece_punkrecords_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    assert stats.records_inserted > 0

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        assert game is not None

        set_codes = session.execute(select(Set.code).where(Set.game_id == game.id).order_by(Set.code)).scalars().all()
        assert set_codes == ["eb-01", "op-01", "st-10"]

        total_prints = session.execute(
            select(func.count(Print.id)).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
        ).scalar_one()
        assert total_prints >= 5

        image_count = session.execute(
            select(func.count(PrintImage.id)).join(Print, Print.id == PrintImage.print_id).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
        ).scalar_one()
        assert image_count >= 5


def test_onepiece_fixture_incremental_idempotent_has_zero_second_run_changes(client):
    connector = get_connector("onepiece")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/onepiece_punkrecords_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/onepiece_punkrecords_sample.json",
            fixture=True,
            incremental=True,
        )
        session.commit()

    with db.SessionLocal() as session:
        run = session.execute(select(IngestRun).order_by(IngestRun.id.desc())).scalars().first()

    assert run is not None
    assert run.counts_json["inserted"] == 0
    assert run.counts_json["updated"] == 0


def test_onepiece_search_by_name_and_set_code(client):
    connector = get_connector("onepiece")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/onepiece_punkrecords_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    by_name = client.get(
        "/api/v1/search?q=luffy&game=onepiece",
        headers=_auth_headers("onepiece-search", ["read:catalog"]),
    )
    assert by_name.status_code == 200
    by_name_payload = by_name.get_json()
    assert any(item.get("game") == "onepiece" for item in by_name_payload)
    assert any("luffy" in str(item.get("title", "")).lower() for item in by_name_payload)

    by_set_code = client.get(
        "/api/v1/search?q=op-01&game=onepiece",
        headers=_auth_headers("opset-001", ["read:catalog"]),
    )
    assert by_set_code.status_code == 200
    by_set_payload = by_set_code.get_json()
    assert any(item.get("type") == "set" and item.get("subtitle") == "op-01" for item in by_set_payload)


def test_onepiece_prints_have_non_null_primary_images_when_available(client):
    connector = get_connector("onepiece")
    with db.SessionLocal() as session:
        connector.run(
            session,
            "data/fixtures/onepiece_punkrecords_sample.json",
            fixture=True,
            incremental=False,
        )
        session.commit()

    response = client.get(
        "/api/v1/search?q=luffy&game=onepiece&type=print",
        headers=_auth_headers("opimg-001", ["read:catalog"]),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert all(item.get("primary_image_url") for item in payload)


def _patch_onepiece_http(monkeypatch, fake_get):
    monkeypatch.setattr("app.ingest.connectors.onepiece.requests.get", fake_get)

    def _fake_session_get(self, url, timeout=0, **kwargs):
        return fake_get(url, timeout=timeout, headers=kwargs.get("headers"))

    monkeypatch.setattr("app.ingest.connectors.onepiece.requests.sessions.Session.get", _fake_session_get)

def test_onepiece_fixture_flag_false_forces_remote_even_if_env_defaults_fixture(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                from requests import HTTPError

                error = HTTPError(f"status={self.status_code}")
                error.response = self
                raise error

        def json(self):
            return self._payload

    remote_packs = {
        "569101": {
            "id": "569101",
            "raw_title": "Booster Pack Romance Dawn",
            "title_parts": {"label": "OP-01"},
            "code": "op-01",
            "release_date": "2022-07-22",
        }
    }
    tree_listing = {"tree": [{"path": "english/cards/569101/OP01-001.json", "type": "blob"}]}

    urls_requested: list[str] = []

    def _fake_get(url, timeout=0, headers=None):
        urls_requested.append(str(url))
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-001.json"):
            return _FakeResponse(
                {
                    "id": "OP01-001",
                    "pack_id": "op-01",
                    "name": "Monkey.D.Luffy",
                    "rarity": "L",
                    "img_full_url": "https://punkrecords.img.cdn/op01/op01-001.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "fixture")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    assert any(url.endswith("/english/packs.json") for url in urls_requested)


def test_onepiece_remote_falls_back_to_official_cardlist_when_punkrecords_404(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload=None, text="", status_code=200, headers=None):
            self._payload = payload
            self.text = text
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                from requests import HTTPError

                error = HTTPError(f"status={self.status_code}")
                error.response = self
                raise error

        def json(self):
            if self._payload is None:
                raise ValueError("missing json payload")
            return self._payload

    official_index = """
    <select id="series"> 
      <option value="569101">BOOSTER PACK [OP-01]</option>
    </select>
    """
    official_series_page = """
    <dl class="modalCol" id="OP01-001">
      <div class="infoCol"><span>OP01-001</span> | <span>L</span> | <span>LEADER</span></div>
      <div class="cardName">Monkey.D.Luffy</div>
      <img data-src="../images/cardlist/card/OP01-001.png?260305" />
    </dl>
    """

    urls_requested: list[str] = []

    def _fake_get(url, timeout=0, headers=None):
        target = str(url)
        urls_requested.append(target)
        if "api.github.com/repos/DevTheFrog/punk-records" in target and "git/trees" not in target:
            return _FakeResponse(status_code=404)
    def _fake_get(url, timeout=0, headers=None):
        target = str(url)
        if target.endswith("/english/packs.json"):
            return _FakeResponse(status_code=404)
        if target == "https://en.onepiece-cardgame.com/cardlist/":
            return _FakeResponse(text=official_index)
        if target == "https://en.onepiece-cardgame.com/cardlist/?series=569101":
            return _FakeResponse(text=official_series_page)
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_OFFICIAL_CARDLIST_URL", "https://en.onepiece-cardgame.com/cardlist/")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        stats = connector.run(session, fixture=False, incremental=False)
        session.commit()

    assert stats.records_inserted > 0

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        image_urls = session.execute(
            select(PrintImage.url)
            .join(Print, Print.id == PrintImage.print_id)
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id)
        ).scalars().all()

    assert image_urls
    assert all("example.cdn.onepiece" not in url for url in image_urls)
    assert any("en.onepiece-cardgame.com/images/cardlist/card/OP01-001.png" in url for url in image_urls)
    assert not any(url.endswith("/english/packs.json") for url in urls_requested)



def test_onepiece_remote_source_mode_reads_pack_directories_and_real_images(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                from requests import HTTPError

                error = HTTPError(f"status={self.status_code}")
                error.response = self
                raise error

        def json(self):
            return self._payload

    remote_packs = {
        "569101": {
            "id": "569101",
            "raw_title": "Booster Pack Romance Dawn",
            "title_parts": {"label": "OP-01"},
            "code": "op-01",
            "release_date": "2022-07-22",
        }
    }

    tree_listing = {
        "tree": [
            {"path": "english/cards/569101/OP01-001.json", "type": "blob"},
            {"path": "english/cards/569101/OP01-001_p1.json", "type": "blob"},
        ]
    }

    urls_requested: list[str] = []

    def _fake_get(url, timeout=0, headers=None):
        urls_requested.append(str(url))
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-001.json"):
            return _FakeResponse(
                {
                    "id": "OP01-001",
                    "pack_id": "op-01",
                    "name": "Monkey.D.Luffy",
                    "rarity": "L",
                    "img_full_url": "https://punkrecords.img.cdn/op01/op01-001.webp",
                    "img_url": "https://punkrecords.img.cdn/op01/op01-001-thumb.webp",
                }
            )
        if str(url).endswith("/english/cards/569101/OP01-001_p1.json"):
            return _FakeResponse(
                {
                    "id": "OP01-001_p1",
                    "pack_id": "op-01",
                    "name": "Monkey.D.Luffy",
                    "rarity": "L",
                    "img_url": "https://punkrecords.img.cdn/op01/op01-001_p1-thumb.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        stats = connector.run(session, fixture=False, incremental=False)
        session.commit()

    assert stats.records_inserted > 0
    assert any(url.endswith("/english/packs.json") for url in urls_requested)
    assert any("api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in url for url in urls_requested)
    assert any(url.endswith("OP01-001.json") for url in urls_requested)
    assert any(url.endswith("OP01-001_p1.json") for url in urls_requested)

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        prints_count = session.execute(
            select(Print.id).join(Set, Set.id == Print.set_id).where(Set.game_id == game.id)
        ).scalars().all()
        image_urls = session.execute(
            select(PrintImage.url)
            .join(Print, Print.id == PrintImage.print_id)
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id)
            .order_by(PrintImage.url)
        ).scalars().all()

    assert len(prints_count) == 2
    assert image_urls
    assert "https://punkrecords.img.cdn/op01/op01-001.webp" in image_urls
    assert "https://punkrecords.img.cdn/op01/op01-001_p1-thumb.webp" in image_urls


def test_onepiece_remote_raises_when_pack_tree_listing_has_no_cards(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569006", "code": "op-01", "name": "Romance Dawn", "release_date": "2022-07-22"}]

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse({"tree": []})
        if "api.github.com/repos/DevTheFrog/punk-records/contents/english/cards/569006" in str(url):
            return _FakeResponse([])
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with pytest.raises(ValueError, match="zero card json paths"):
        connector._load_remote()


def test_onepiece_remote_load_remote_does_not_return_empty_for_valid_pack_directories(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = {
        "569006": {
            "id": "569006",
            "raw_title": "Booster Pack Romance Dawn",
            "title_parts": {"label": "OP-01"},
            "code": "op-01",
            "release_date": "2022-07-22",
        }
    }
    tree_listing = {"tree": [{"path": "english/cards/569006/OP01-001.json", "type": "blob"}]}

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569006/OP01-001.json"):
            return _FakeResponse(
                {
                    "id": "OP01-001",
                    "pack_id": "op-01",
                    "name": "Monkey.D.Luffy",
                    "rarity": "L",
                    "img_full_url": "https://punkrecords.img.cdn/op01/op01-001.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    payload = connector._load_remote()
    assert payload.get("sets")
    assert payload.get("cards")
    assert len(payload["sets"]) > 0
    assert len(payload["cards"]) > 0


def test_onepiece_remote_updates_fake_primary_images_to_real_urls(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"}]
    tree_listing = {"tree": [{"path": "english/cards/569101/OP01-025.json", "type": "blob"}]}
    first_cards = {
        "id": "OP01-025",
        "pack_id": "569101",
        "set_code": "op-01",
        "name": "Roronoa Zoro",
        "rarity": "SR",
        "img_full_url": "https://example.cdn.onepiece/op01/op01-025.jpg",
    }
    second_cards = {
        "id": "OP01-025",
        "pack_id": "569101",
        "set_code": "op-01",
        "name": "Roronoa Zoro",
        "rarity": "SR",
        "img_full_url": "https://punkrecords.img.cdn/op01/op01-025.webp",
    }

    state = {"phase": "first"}

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-025.json"):
            return _FakeResponse(first_cards if state["phase"] == "first" else second_cards)
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    monkeypatch.setenv("ONEPIECE_IMAGE_FALLBACK_URL", "https://cdn.fallback/onepiece-missing.png")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    state["phase"] = "second"
    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        image_urls = session.execute(select(PrintImage.url).order_by(PrintImage.url)).scalars().all()

    assert image_urls
    assert "https://punkrecords.img.cdn/op01/op01-025.webp" in image_urls
    assert "https://cdn.fallback/onepiece-missing.png" not in image_urls
    assert all("example.cdn.onepiece" not in url for url in image_urls)


def test_onepiece_remote_incremental_second_run_is_idempotent(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"}]
    tree_listing = {"tree": [{"path": "english/cards/569101/OP01-025.json", "type": "blob"}]}
    op01_card = {
        "id": "OP01-025",
        "pack_id": "569101",
        "set_code": "op-01",
        "name": "Roronoa Zoro",
        "rarity": "SR",
        "img_full_url": "https://punkrecords.img.cdn/op01/op01-025.webp",
    }

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-025.json"):
            return _FakeResponse(op01_card)
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        first = connector.run(session, fixture=False, incremental=True)
        session.commit()
    with db.SessionLocal() as session:
        second = connector.run(session, fixture=False, incremental=True)
        session.commit()

    assert first.records_inserted > 0
    assert second.records_inserted == 0
    assert second.records_updated == 0
    assert second.files_skipped > 0


def test_onepiece_github_api_uses_github_token_header(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    captured_headers: dict[str, str] = {}

    def _fake_get(url, timeout=0, headers=None):
        captured_headers.update(headers or {})
        return _FakeResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    _patch_onepiece_http(monkeypatch, _fake_get)

    payload = connector._fetch_remote_json(
        url="https://api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1",
        timeout=30,
    )

    assert payload == {"ok": True}
    assert captured_headers.get("Authorization") == "Bearer ghp_test_token"
    assert captured_headers.get("Accept") == "application/vnd.github+json"


def test_onepiece_github_api_rate_limited_raises_actionable_error(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self):
            self.status_code = 403
            self.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1710000000"}

        def raise_for_status(self):
            from requests import HTTPError

            error = HTTPError("status=403")
            error.response = self
            raise error

        def json(self):
            return {}

    def _fake_get(url, timeout=0, headers=None):
        return _FakeResponse()

    _patch_onepiece_http(monkeypatch, _fake_get)

    with pytest.raises(RuntimeError, match="Set GITHUB_TOKEN"):
        connector._fetch_remote_json(
            url="https://api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1",
            timeout=30,
        )


def test_onepiece_remote_tree_listing_returns_pack_urls(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "tree": [
                    {"path": "english/cards/569006/OP01-001.json", "type": "blob"},
                    {"path": "english/cards/569006/OP01-001_p1.json", "type": "blob"},
                    {"path": "english/cards/569006/README.md", "type": "blob"},
                ]
            }

    def _fake_get(url, timeout=0, headers=None):
        return _FakeResponse()

    _patch_onepiece_http(monkeypatch, _fake_get)

    result = connector._fetch_pack_card_file_urls_from_tree(
        root_url="https://raw.githubusercontent.com/DevTheFrog/punk-records/main",
        language="english",
        timeout=30,
    )

    assert sorted(result["569006"]) == [
        "https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569006/OP01-001.json",
        "https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569006/OP01-001_p1.json",
    ]


def test_onepiece_remote_tree_lookup_resolves_pack_cards_from_pack_id_or_set_code(client):
    connector = get_connector("onepiece")
    card_urls_by_pack = {
        "569006": ["https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569006/ST06-001.json"],
        "op-01": ["https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569101/OP01-001.json"],
    }

    by_pack_id = connector._resolve_pack_card_urls_from_tree(
        card_urls_by_pack=card_urls_by_pack,
        lookup_keys=["569006", "st-06"],
    )
    by_set_code = connector._resolve_pack_card_urls_from_tree(
        card_urls_by_pack=card_urls_by_pack,
        lookup_keys=["st-99", "op-01"],
    )

    assert by_pack_id == [
        "https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569006/ST06-001.json"
    ]
    assert by_set_code == [
        "https://raw.githubusercontent.com/DevTheFrog/punk-records/main/english/cards/569101/OP01-001.json"
    ]


def test_onepiece_remote_does_not_probe_contents_api_when_tree_has_valid_paths(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569006", "code": "st-06", "name": "Starter Deck", "release_date": "2022-07-22"}]
    tree_listing = {"tree": [{"path": "english/cards/569006/ST06-001.json", "type": "blob"}]}

    urls_requested: list[str] = []

    def _fake_get(url, timeout=0, headers=None):
        url_str = str(url)
        urls_requested.append(url_str)
        if url_str.endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in url_str:
            return _FakeResponse(tree_listing)
        if url_str.endswith("/english/cards/569006/ST06-001.json"):
            return _FakeResponse(
                {
                    "id": "ST06-001",
                    "pack_id": "569006",
                    "set_code": "st-06",
                    "name": "Sample card",
                    "rarity": "C",
                    "img_full_url": "https://punkrecords.img.cdn/st06/st06-001.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    payload = connector._load_remote()

    assert payload.get("cards")
    assert not any("/contents/" in requested for requested in urls_requested)


def test_onepiece_identifier_collision_does_not_break_ingest_and_reconciles(client):
    connector = get_connector("onepiece")
    payload = {
        "source": "punk_records",
        "language": "en",
        "sets": [{"code": "op-07", "name": "Set 07", "release_date": "2024-01-01"}],
        "cards": [
            {
                "id": "card-a",
                "name": "Card A",
                "prints": [
                    {
                        "id": "OP07-091_p1",
                        "set_code": "op-07",
                        "collector_number": "OP07-091_p1",
                        "rarity": "SR",
                        "variant": "p1",
                        "image_url": "https://en.onepiece-cardgame.com/images/card-a.jpg",
                    }
                ],
            },
            {
                "id": "card-b",
                "name": "Card B",
                "prints": [
                    {
                        "id": "OP07-091_p1",
                        "set_code": "op-07",
                        "collector_number": "OP07-091_p1",
                        "rarity": "SR",
                        "variant": "p1",
                        "image_url": "https://en.onepiece-cardgame.com/images/card-b.jpg",
                    }
                ],
            },
        ],
    }

    with db.SessionLocal() as session:
        stats = IngestStats()
        connector.upsert(session, payload, stats)
        session.commit()

    with db.SessionLocal() as session:
        identifiers = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.source == "punk_records",
                PrintIdentifier.external_id == "OP07-091_p1",
            )
        ).scalars().all()
        prints = session.execute(select(Print.id).join(Set, Set.id == Print.set_id).where(Set.code == "op-07")).scalars().all()

    assert len(identifiers) == 1
    assert len(prints) == 1


def test_onepiece_remote_load_remote_concurrency_keeps_logical_payload(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569006", "code": "st-06", "name": "Starter Deck", "release_date": "2022-07-22"}]
    tree_listing = {
        "tree": [
            {"path": "english/cards/569006/ST06-001.json", "type": "blob"},
            {"path": "english/cards/569006/ST06-002.json", "type": "blob"},
        ]
    }

    def _fake_get(url, timeout=0, headers=None):
        url_str = str(url)
        if url_str.endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in url_str:
            return _FakeResponse(tree_listing)
        if url_str.endswith("/english/cards/569006/ST06-001.json"):
            return _FakeResponse(
                {
                    "id": "ST06-001",
                    "pack_id": "569006",
                    "set_code": "st-06",
                    "name": "Sample One",
                    "rarity": "C",
                    "img_full_url": "https://en.onepiece-cardgame.com/images/st06-001.png",
                }
            )
        if url_str.endswith("/english/cards/569006/ST06-002.json"):
            return _FakeResponse(
                {
                    "id": "ST06-002",
                    "pack_id": "569006",
                    "set_code": "st-06",
                    "name": "Sample Two",
                    "rarity": "C",
                    "img_full_url": "https://en.onepiece-cardgame.com/images/st06-002.png",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    monkeypatch.setenv("ONEPIECE_REMOTE_MAX_WORKERS", "4")
    _patch_onepiece_http(monkeypatch, _fake_get)

    payload = connector._load_remote()

    cards = sorted(payload.get("cards") or [], key=lambda c: c["id"])
    assert [item["id"] for item in cards] == ["st-06:st06-001", "st-06:st06-002"]

def test_onepiece_remote_pack_id_maps_to_commercial_set_codes(client):
    connector = get_connector("onepiece")

    payload = connector._normalize_remote_payload(
        packs_payload=[
            {"id": "569006", "raw_title": "STARTER DECK", "name": "Starter 6"},
            {"id": "569107", "raw_title": "Booster Pack 07", "name": "Booster 07"},
            {"id": "569101", "raw_title": "Romance Dawn"},
            {"id": "569201", "raw_title": "Extra Booster 01"},
        ],
        cards_payload_by_pack={
            "569006": [{"id": "ST06-001", "pack_id": "569006", "name": "Tashigi", "img_full_url": "https://img/st06-001.webp"}],
            "569107": [{"id": "OP07-001", "pack_id": "569107", "name": "Jewelry Bonney", "img_full_url": "https://img/op07-001.webp"}],
            "569101": [{"id": "OP01-025", "pack_id": "569101", "name": "Roronoa Zoro", "img_full_url": "https://img/op01-025.webp"}],
            "569201": [{"id": "EB01-004", "pack_id": "569201", "name": "Nami", "img_full_url": "https://img/eb01-004.webp"}],
        },
        language="english",
    )

    set_codes = sorted(item["code"] for item in payload["sets"])
    assert set_codes == ["eb-01", "op-01", "op-07", "st-06"]


def test_onepiece_remote_reconciles_legacy_and_remote_equivalent_sets(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"}]
    tree_listing = {"tree": [{"path": "english/cards/569101/OP01-025.json", "type": "blob"}]}

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-025.json"):
            return _FakeResponse(
                {
                    "id": "OP01-025",
                    "pack_id": "569101",
                    "name": "Roronoa Zoro",
                    "rarity": "SR",
                    "img_full_url": "https://punkrecords.img.cdn/op01/op01-025.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    with db.SessionLocal() as session:
        connector.run(session, fixture=True, incremental=False)
        session.commit()

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        set_codes = session.execute(select(Set.code).where(Set.game_id == game.id).order_by(Set.code)).scalars().all()
        op01_set = session.execute(select(Set).where(Set.game_id == game.id, Set.code == "op-01")).scalar_one()
        op01_prints = session.execute(select(Print.id).where(Print.set_id == op01_set.id)).scalars().all()

    assert "569101" not in set_codes
    assert "op-01" in set_codes
    assert len(op01_prints) == 2


def test_onepiece_remote_replaces_legacy_fake_urls_when_equivalent_print_exists(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [
        {"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"},
        {"id": "569010", "name": "The Three Captains", "release_date": "2023-11-10"},
        {"id": "569201", "name": "Memorial Collection", "release_date": "2024-05-03"},
    ]
    tree_listing = {
        "tree": [
            {"path": "english/cards/569101/OP01-001.json", "type": "blob"},
            {"path": "english/cards/569101/OP01-025.json", "type": "blob"},
            {"path": "english/cards/569010/ST10-001.json", "type": "blob"},
            {"path": "english/cards/569201/EB01-012_p1.json", "type": "blob"},
            {"path": "english/cards/569201/EB01-004.json", "type": "blob"},
        ]
    }

    cards = {
        "OP01-001": {"id": "OP01-001", "pack_id": "569101", "name": "Monkey.D.Luffy", "img_full_url": "https://punkrecords.img.cdn/op01/op01-001.webp"},
        "OP01-025": {"id": "OP01-025", "pack_id": "569101", "name": "Roronoa Zoro", "img_full_url": "https://punkrecords.img.cdn/op01/op01-025.webp"},
        "ST10-001": {"id": "ST10-001", "pack_id": "569010", "name": "Monkey.D.Luffy", "img_full_url": "https://punkrecords.img.cdn/st10/st10-001.webp"},
        "EB01-012_p1": {"id": "EB01-012_p1", "pack_id": "569201", "name": "Roronoa Zoro", "img_full_url": "https://punkrecords.img.cdn/eb01/eb01-012_p1.webp"},
        "EB01-004": {"id": "EB01-004", "pack_id": "569201", "name": "Nami", "img_full_url": "https://punkrecords.img.cdn/eb01/eb01-004.webp"},
    }

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        for key, payload in cards.items():
            if str(url).endswith(f"/{key}.json"):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected url requested: {url}")

    with db.SessionLocal() as session:
        connector.run(session, fixture=True, incremental=False)
        session.commit()

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        urls = session.execute(select(PrintImage.url).order_by(PrintImage.url)).scalars().all()

    assert urls
    assert all("example.cdn.onepiece" not in url for url in urls)

def test_onepiece_incremental_repair_cleans_residual_fake_images_even_when_payload_is_skipped(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"}]
    tree_listing = {"tree": [{"path": "english/cards/569101/OP01-025.json", "type": "blob"}]}

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-025.json"):
            return _FakeResponse(
                {
                    "id": "OP01-025",
                    "pack_id": "569101",
                    "name": "Roronoa Zoro",
                    "rarity": "SR",
                    "img_full_url": "https://punkrecords.img.cdn/op01/op01-025.webp",
                }
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        zoro_print = session.execute(
            select(Print)
            .join(Set, Set.id == Print.set_id)
            .where(Set.game_id == game.id, Print.collector_number == "OP01-025")
        ).scalar_one()
        primary = session.execute(select(PrintImage).where(PrintImage.print_id == zoro_print.id, PrintImage.is_primary.is_(True))).scalar_one()
        primary.url = "https://example.cdn.onepiece/op01/op01-025.jpg"
        session.add(
            PrintImage(
                print_id=zoro_print.id,
                url="https://example.cdn.onepiece/op01/op01-025-secondary.jpg",
                is_primary=False,
                source="legacy",
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        fake_count = session.execute(
            select(func.count(PrintImage.id)).where(PrintImage.url.ilike("%example.cdn.onepiece%"))
        ).scalar_one()

    assert fake_count == 0


def test_onepiece_reconcile_print_identifier_handles_multiple_candidates_deterministically(client, caplog):
    connector = get_connector("onepiece")
    caplog.set_level("WARNING")

    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        set_row = Set(game_id=1, code="op-01", name="Romance Dawn")
        set_row_2 = Set(game_id=1, code="st-10", name="The Three Captains")
        card_a = Card(game_id=1, name="Monkey.D.Luffy", card_key="luffy-a")
        card_b = Card(game_id=1, name="Monkey.D.Luffy Alt", card_key="luffy-b")
        session.add_all([game])
        session.flush()
        set_row.game_id = game.id
        card_a.game_id = game.id
        card_b.game_id = game.id
        session.add_all([set_row, set_row_2, card_a, card_b])
        session.flush()

        print_a = Print(set_id=set_row.id, card_id=card_a.id, collector_number="OP01-001", language="en", is_foil=False, variant="default")
        print_b = Print(set_id=set_row.id, card_id=card_b.id, collector_number="AA-001", language="en", is_foil=False, variant="default")
        print_c = Print(set_id=set_row_2.id, card_id=card_b.id, collector_number="ST10-001", language="en", is_foil=False, variant="default")
        session.add_all([print_a, print_b, print_c])
        session.flush()
        session.add_all(
            [
                PrintIdentifier(print_id=print_a.id, source="punk_records", external_id="OP01-001"),
                PrintIdentifier(print_id=print_c.id, source="punk_records", external_id="OP01-001"),
                PrintImage(print_id=print_a.id, url="https://en.onepiece-cardgame.com/images/op01-001.png", is_primary=True, source="punk_records"),
                PrintImage(print_id=print_b.id, url="https://example.cdn.onepiece/legacy/aa-001.jpg", is_primary=True, source="legacy"),
                PrintImage(print_id=print_c.id, url="https://example.cdn.onepiece/legacy/op01-001-c.jpg", is_primary=True, source="legacy"),
            ]
        )
        session.flush()
        stats = IngestStats()
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_b, external_print_id="OP01-001")
        print_a_id = print_a.id
        session.commit()

    with db.SessionLocal() as session:
        identifiers = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.source == "punk_records",
                PrintIdentifier.external_id == "OP01-001",
            )
        ).scalars().all()

    assert len(identifiers) == 1
    assert identifiers[0].print_id == print_a_id
    assert any("identifier_collision_multiple_candidates" in message for message in [record.message for record in caplog.records])


def test_onepiece_reconcile_preserves_canonical_owner_over_numeric_set_alias(client, caplog):
    connector = get_connector("onepiece")
    caplog.set_level("INFO")

    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()
        canonical_set = Set(game_id=game.id, code="st-10", name="The Three Captains")
        numeric_set = Set(game_id=game.id, code="569010", name="Legacy Numeric Pack")
        card = Card(game_id=game.id, name="Monkey.D.Luffy", card_key="luffy-st10")
        session.add_all([canonical_set, numeric_set, card])
        session.flush()

        canonical_print = Print(set_id=canonical_set.id, card_id=card.id, collector_number="ST10-001", language="en", variant="default", print_key="onepiece:st-10:st10-001:en:default")
        numeric_print = Print(set_id=numeric_set.id, card_id=card.id, collector_number="ST10-001", language="en", variant="default", print_key="onepiece:569010:st10-001:en:default")
        session.add_all([canonical_print, numeric_print])
        session.flush()

        session.add_all(
            [
                PrintIdentifier(print_id=canonical_print.id, source="punk_records", external_id="ST10-001"),
                PrintImage(print_id=canonical_print.id, url="https://en.onepiece-cardgame.com/images/st10-001.png", is_primary=True, source="punk_records"),
                PrintImage(print_id=numeric_print.id, url="https://en.onepiece-cardgame.com/images/st10-001-alt.png", is_primary=True, source="punk_records"),
            ]
        )
        session.flush()

        stats = IngestStats()
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=numeric_print, external_print_id="ST10-001")
        canonical_print_id = canonical_print.id
        session.commit()

    with db.SessionLocal() as session:
        owner = session.execute(
            select(PrintIdentifier.print_id).where(
                PrintIdentifier.source == "punk_records",
                PrintIdentifier.external_id == "ST10-001",
            )
        ).scalar_one()

    assert owner == canonical_print_id
    assert any("strategy=preserve_existing_owner" in str(record.message) for record in caplog.records)


def test_onepiece_reconcile_is_stable_across_repeated_calls(client, caplog):
    connector = get_connector("onepiece")
    caplog.set_level("WARNING")

    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        set_row = Set(game_id=1, code="op-09", name="Emperors in the New World")
        card = Card(game_id=1, name="Monkey.D.Luffy", card_key="luffy-op09")
        session.add(game)
        session.flush()
        set_row.game_id = game.id
        card.game_id = game.id
        session.add_all([set_row, card])
        session.flush()

        print_row = Print(set_id=set_row.id, card_id=card.id, collector_number="OP09-076", language="en", variant="default", print_key="onepiece:op-09:op09-076:en:default")
        session.add(print_row)
        session.flush()
        session.add(
            PrintIdentifier(print_id=print_row.id, source="punk_records", external_id="OP09-076_r2")
        )
        session.flush()

        stats = IngestStats()
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_row, external_print_id="OP09-076_r2")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_row, external_print_id="OP09-076_r2")
        session.commit()

    assert not any("identifier_reassigned" in str(record.message) for record in caplog.records)




def test_onepiece_reconcile_residual_variant_ping_pong_is_stable(client, caplog):
    connector = get_connector("onepiece")
    caplog.set_level("INFO")

    with db.SessionLocal() as session:
        game = Game(slug="onepiece", name="ONE PIECE Card Game")
        session.add(game)
        session.flush()

        op05 = Set(game_id=game.id, code="op-05", name="Awakening of the New Era")
        st01 = Set(game_id=game.id, code="st-01", name="Straw Hat Crew")
        session.add_all([op05, st01])
        session.flush()

        card_a = Card(game_id=game.id, name="Card OP05-118", card_key="op05-118")
        card_b = Card(game_id=game.id, name="Card OP05-119", card_key="op05-119")
        card_c = Card(game_id=game.id, name="Card ST01-012", card_key="st01-012")
        session.add_all([card_a, card_b, card_c])
        session.flush()

        print_118 = Print(set_id=op05.id, card_id=card_a.id, collector_number="OP05-118", language="en", variant="parallel", print_key="onepiece:op-05:op05-118:en:parallel")
        print_119 = Print(set_id=op05.id, card_id=card_b.id, collector_number="OP05-119", language="en", variant="parallel", print_key="onepiece:op-05:op05-119:en:parallel")
        print_st = Print(set_id=st01.id, card_id=card_c.id, collector_number="ST01-012", language="en", variant="parallel", print_key="onepiece:st-01:st01-012:en:parallel")
        session.add_all([print_118, print_119, print_st])
        session.flush()

        session.add_all(
            [
                PrintIdentifier(print_id=print_118.id, source="punk_records", external_id="OP05-118"),
                PrintIdentifier(print_id=print_119.id, source="punk_records", external_id="OP05-119_p2"),
                PrintIdentifier(print_id=print_st.id, source="punk_records", external_id="ST01-012_p3"),
            ]
        )
        session.flush()

        stats = IngestStats()
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_118, external_print_id="OP05-118_p1")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_119, external_print_id="OP05-119")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_119, external_print_id="OP05-119_p1")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_st, external_print_id="ST01-012_p2")

        # second pass should remain stable (idempotent for these residual families)
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_118, external_print_id="OP05-118_p1")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_119, external_print_id="OP05-119")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_119, external_print_id="OP05-119_p1")
        connector._reconcile_print_identifier(session=session, stats=stats, print_row=print_st, external_print_id="ST01-012_p2")

        print_118_id = print_118.id
        print_119_id = print_119.id
        print_st_id = print_st.id
        session.commit()

    with db.SessionLocal() as session:
        by_print = {
            print_id: external
            for print_id, external in session.execute(
                select(PrintIdentifier.print_id, PrintIdentifier.external_id).where(
                    PrintIdentifier.source == "punk_records",
                    PrintIdentifier.print_id.in_([print_118_id, print_119_id, print_st_id]),
                )
            ).all()
        }

    assert by_print[print_118_id] == "OP05-118"
    assert by_print[print_119_id] == "OP05-119_p2"
    assert by_print[print_st_id] == "ST01-012_p3"
    assert not any("identifier_reassigned" in str(record.message) for record in caplog.records)

def test_onepiece_remote_repair_prefers_real_images_over_fake_legacy_in_search(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569101", "name": "Romance Dawn", "release_date": "2022-07-22"}]
    tree_listing = {
        "tree": [
            {"path": "english/cards/569101/OP01-001.json", "type": "blob"},
            {"path": "english/cards/569101/OP01-025.json", "type": "blob"},
            {"path": "english/cards/569101/EB01-004.json", "type": "blob"},
        ]
    }

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569101/OP01-001.json"):
            return _FakeResponse(
                {"id": "OP01-001", "pack_id": "569101", "name": "Monkey.D.Luffy", "img_full_url": "https://en.onepiece-cardgame.com/images/op01-001.png"}
            )
        if str(url).endswith("/english/cards/569101/OP01-025.json"):
            return _FakeResponse(
                {"id": "OP01-025", "pack_id": "569101", "name": "Roronoa Zoro", "img_full_url": "https://en.onepiece-cardgame.com/images/op01-025.png"}
            )
        if str(url).endswith("/english/cards/569101/EB01-004.json"):
            return _FakeResponse(
                {"id": "EB01-004", "pack_id": "569101", "name": "Nami", "img_full_url": "https://en.onepiece-cardgame.com/images/eb01-004.png"}
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        luffy_print = session.execute(select(Print).where(Print.collector_number == "OP01-001")).scalar_one()
        session.add(
            PrintImage(
                print_id=luffy_print.id,
                url="https://example.cdn.onepiece/op01/op01-001-legacy.jpg",
                is_primary=True,
                source="legacy",
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    response = client.get(
        "/api/v1/search?q=luffy&game=onepiece&type=print",
        headers=_auth_headers("onepiece-real-image", ["read:catalog"]),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert all("example.cdn.onepiece" not in str(item.get("primary_image_url") or "") for item in payload)


def test_onepiece_remote_reingest_is_idempotent_without_duplicate_external_identifiers(client, monkeypatch):
    connector = get_connector("onepiece")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    remote_packs = [{"id": "569010", "name": "The Three Captains", "release_date": "2023-11-10"}]
    tree_listing = {
        "tree": [
            {"path": "english/cards/569010/ST10-001.json", "type": "blob"},
            {"path": "english/cards/569010/EB01-012_p1.json", "type": "blob"},
        ]
    }

    def _fake_get(url, timeout=0, headers=None):
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if "api.github.com/repos/DevTheFrog/punk-records" in str(url) and "git/trees" not in str(url):
            return _FakeResponse({"full_name": "DevTheFrog/punk-records"})
        if str(url).endswith("/english/packs.json"):
            return _FakeResponse(remote_packs)
        if "api.github.com/repos/DevTheFrog/punk-records/git/trees/main?recursive=1" in str(url):
            return _FakeResponse(tree_listing)
        if str(url).endswith("/english/cards/569010/ST10-001.json"):
            return _FakeResponse(
                {"id": "ST10-001", "pack_id": "569010", "name": "Monkey.D.Luffy", "img_full_url": "https://en.onepiece-cardgame.com/images/st10-001.png"}
            )
        if str(url).endswith("/english/cards/569010/EB01-012_p1.json"):
            return _FakeResponse(
                {"id": "EB01-012_p1", "pack_id": "569010", "name": "Roronoa Zoro", "img_full_url": "https://en.onepiece-cardgame.com/images/eb01-012-p1.png"}
            )
        raise AssertionError(f"unexpected url requested: {url}")

    monkeypatch.setenv("ONEPIECE_SOURCE", "remote")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_ROOT_URL", "https://raw.githubusercontent.com/DevTheFrog/punk-records/main")
    monkeypatch.setenv("ONEPIECE_PUNKRECORDS_LANGUAGE", "english")
    _patch_onepiece_http(monkeypatch, _fake_get)

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, fixture=False, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        duplicate_external_ids = session.execute(
            select(func.count(PrintIdentifier.id)).where(PrintIdentifier.source == "punk_records")
        ).scalar_one()
        unique_external_ids = session.execute(
            select(func.count(func.distinct(PrintIdentifier.external_id))).where(PrintIdentifier.source == "punk_records")
        ).scalar_one()

    assert duplicate_external_ids == unique_external_ids
