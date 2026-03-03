from sqlalchemy import func, select

from app import db
from app.auth.service import hash_api_key
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, Card, IngestRun, SearchDocument, Source, SourceRecord


def _auth_headers(key: str = "admin-key", scopes: list[str] | None = None) -> dict[str, str]:
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
        run = session.execute(select(IngestRun).order_by(IngestRun.id.desc())).scalars().first()

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

    response = client.get("/api/search?q=pika&game=pokemon", headers=_auth_headers("catalog-key", ["read:catalog"]))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Pikachu" in item["title"] for item in payload)


def test_scryfall_bootstrap_when_incremental_true_on_empty_db(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        stats = connector.run(session, "data/fixtures/scryfall_mtg_sample.json", fixture=True, incremental=True, limit=5)
        session.commit()

    with db.SessionLocal() as session:
        source = session.execute(select(Source).where(Source.name == "scryfall_mtg")).scalar_one()
        mtg_cards = session.execute(select(func.count(Card.id))).scalar_one()
        source_records = session.execute(select(func.count(SourceRecord.id)).where(SourceRecord.source_id == source.id)).scalar_one()

    assert stats.records_inserted > 0
    assert mtg_cards > 0
    assert source_records > 0


def test_scryfall_fixture_incremental_idempotent_has_zero_second_run_changes(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/scryfall_mtg_sample.json", fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/scryfall_mtg_sample.json", fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        run = session.execute(select(IngestRun).order_by(IngestRun.id.desc())).scalars().first()

    assert run.counts_json["inserted"] == 0
    assert run.counts_json["updated"] == 0


def test_scryfall_search_finds_forest_after_ingest(client):
    connector = get_connector("scryfall_mtg")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/scryfall_mtg_sample.json", fixture=True, incremental=False)
        session.commit()

    response = client.get("/api/v1/search?q=Forest&game=mtg", headers=_auth_headers("mtg-search", ["read:catalog"]))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Forest" in item["title"] for item in payload)


def test_admin_requires_scope(client):
    response = client.get("/api/v1/admin/ingest/runs", headers=_auth_headers("catalog-only", ["read:catalog"]))
    assert response.status_code == 403


def test_tcgdex_bootstrap_incremental_inserts_pokemon(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        stats = connector.run(session, "data/fixtures/tcgdex_pokemon_sample.json", fixture=True, incremental=True, limit=5)
        session.commit()

    with db.SessionLocal() as session:
        source = session.execute(select(Source).where(Source.name == "tcgdex_pokemon")).scalar_one()
        pokemon_cards = session.execute(select(func.count(Card.id))).scalar_one()
        source_records = session.execute(select(func.count(SourceRecord.id)).where(SourceRecord.source_id == source.id)).scalar_one()

    assert stats.records_inserted > 0
    assert pokemon_cards > 0
    assert source_records > 0


def test_tcgdex_fixture_incremental_idempotent_has_zero_second_run_changes(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/tcgdex_pokemon_sample.json", fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/tcgdex_pokemon_sample.json", fixture=True, incremental=True)
        session.commit()

    with db.SessionLocal() as session:
        run = session.execute(select(IngestRun).order_by(IngestRun.id.desc())).scalars().first()

    assert run.counts_json["inserted"] == 0
    assert run.counts_json["updated"] == 0


def test_tcgdex_search_finds_pikachu_after_ingest(client):
    connector = get_connector("tcgdex_pokemon")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/tcgdex_pokemon_sample.json", fixture=True, incremental=False)
        session.commit()

    response = client.get("/api/v1/search?q=Pikachu&game=pokemon", headers=_auth_headers("pokemon-search", ["read:catalog"]))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Pikachu" in item["title"] for item in payload)
