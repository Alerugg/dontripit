import json
from sqlalchemy import func, select

from app import db
from app.auth.service import hash_api_key
from app.ingest.registry import get_connector
from app.models import (
    ApiKey,
    ApiPlan,
    Card,
    Game,
    IngestRun,
    Print,
    SearchDocument,
    Set,
    Source,
    SourceRecord,
)


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


def test_tcgdex_remote_set_ingest_fetches_set_then_cards_with_limit(
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
        "https://api.tcgdex.net/v2/en/cards/base1-1": {
            "id": "base1-1",
            "localId": "1",
            "name": "Alakazam",
            "image": "https://img/base1-1",
        },
        "https://api.tcgdex.net/v2/en/cards/base1-2": {
            "id": "base1-2",
            "localId": "2",
            "name": "Blastoise",
            "image": "https://img/base1-2",
        },
    }

    def _fake_get(url, params=None, timeout=30):
        requested_urls.append(url)
        return _FakeResponse(payloads[url])

    monkeypatch.setattr("app.ingest.connectors.tcgdex_pokemon.requests.get", _fake_get)

    payloads = connector.load(None, fixture=False, set="base1", lang="en", limit=1)

    assert len(payloads) == 1
    assert requested_urls == [
        "https://api.tcgdex.net/v2/en/sets/base1",
        "https://api.tcgdex.net/v2/en/cards/base1-1",
    ]


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
            select(Print.language, Print.rarity, Print.variant).where(Print.collector_number == "MFS-001")
        ).first()

    assert row is not None
    assert row.language == "en"
    assert row.rarity == "unknown"
    assert row.variant == "default"


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
                select(Print.variant).where(Print.collector_number == "001").order_by(Print.variant.asc())
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
