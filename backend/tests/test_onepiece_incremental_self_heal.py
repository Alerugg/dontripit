from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.connectors.onepiece_incremental_guard import SelfHealingOnePieceCanonicalConnector
from app.ingest.registry import get_connector
from app.models import Print


def _promo_payload(*, collector: str = "P-150", language: str = "ja") -> dict:
    return {
        "source": "onepiece_official_v2",
        "language": language,
        "region": "jp" if language == "ja" else "global-en",
        "sets": [{"id": "p", "code": "P", "name": "Promotion Cards"}],
        "releases": [],
        "cards": [
            {
                "id": f"onepiece:{collector.lower()}",
                "name": "Kuzan",
                "collector_number": collector,
                "prints": [
                    {
                        "id": collector,
                        "identity_key": f"{collector.lower()}:{language}:default",
                        "set_code": "P",
                        "collector_number": collector,
                        "rarity": "P",
                        "variant": "default",
                        "variant_family": "default",
                        "image_url": f"https://www.onepiece-cardgame.com/images/cardlist/card/{collector}.png",
                        "details": {},
                        "release_appearances": [],
                        "alternate_source_images": [],
                        "alternate_source_details": [],
                    }
                ],
            }
        ],
        "diagnostics": {},
    }


def _promo_payload_many(collectors: list[str], *, language: str = "ja") -> dict:
    payload = _promo_payload(collector=collectors[0], language=language)
    payload["cards"] = [
        deepcopy(_promo_payload(collector=collector, language=language)["cards"][0])
        for collector in collectors
    ]
    return payload


def test_registry_uses_self_healing_onepiece_writer():
    connector = get_connector("onepiece")
    assert isinstance(connector, SelfHealingOnePieceCanonicalConnector)
    assert isinstance(connector, OnePieceCanonicalConnector)


def test_known_checksum_is_skipped_only_while_physical_inventory_is_intact(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    payload = _promo_payload()
    source_record = SimpleNamespace(raw_json=payload)

    with db.SessionLocal() as session:
        connector.upsert(session, payload, IngestStats())
        session.flush()

        assert connector.should_skip_existing_record(source_record, session=session) is True

        p150 = session.execute(
            select(Print).where(Print.collector_number == "P-150", Print.language == "ja")
        ).scalar_one()
        session.delete(p150)
        session.flush()

        assert connector.should_skip_existing_record(source_record, session=session) is False


def test_missing_raw_payload_fails_open_to_replay(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    source_record = SimpleNamespace(raw_json={"_payload_omitted": True})

    with db.SessionLocal() as session:
        assert connector.should_skip_existing_record(source_record, session=session) is False


def test_materialized_payload_reduces_to_empty_delta_without_mutating_source(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    payload = _promo_payload_many([f"P-{number:03d}" for number in range(130, 150)])
    original = deepcopy(payload)

    with db.SessionLocal() as session:
        connector.upsert(session, payload, IngestStats())
        session.flush()

        delta = connector._delta_payload(session, payload)

        assert delta["cards"] == []
        assert delta["sets"] == []
        assert delta["diagnostics"]["incremental_delta"]["source_prints"] == 20
        assert delta["diagnostics"]["incremental_delta"]["delta_prints"] == 0
        assert payload == original


def test_changed_payload_writes_only_new_regional_promo(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    existing_collectors = [f"P-{number:03d}" for number in range(130, 150)]
    baseline = _promo_payload_many(existing_collectors)

    with db.SessionLocal() as session:
        connector.upsert(session, baseline, IngestStats())
        session.flush()

        expanded = _promo_payload_many(existing_collectors + ["P-150"])
        delta = connector._delta_payload(session, expanded)

        assert len(delta["cards"]) == 1
        assert delta["cards"][0]["id"] == "onepiece:p-150"
        assert [row["collector_number"] for row in delta["cards"][0]["prints"]] == ["P-150"]
        assert [row["code"] for row in delta["sets"]] == ["P"]
        assert delta["diagnostics"]["incremental_delta"]["source_prints"] == 21
        assert delta["diagnostics"]["incremental_delta"]["delta_prints"] == 1

        connector.upsert(session, expanded, IngestStats())
        session.flush()
        p150 = session.execute(
            select(Print).where(Print.collector_number == "P-150", Print.language == "ja")
        ).scalar_one()
        assert p150.print_key == "onepiece:p:P-150:ja:default"


def test_changed_primary_image_remains_a_real_delta(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    payload = _promo_payload(collector="P-151")

    with db.SessionLocal() as session:
        connector.upsert(session, payload, IngestStats())
        session.flush()

        changed = deepcopy(payload)
        changed["cards"][0]["prints"][0]["image_url"] = (
            "https://www.onepiece-cardgame.com/images/cardlist/card/P-151_p1.png"
        )
        delta = connector._delta_payload(session, changed)

        assert len(delta["cards"]) == 1
        assert len(delta["cards"][0]["prints"]) == 1
        assert delta["diagnostics"]["incremental_delta"]["delta_prints"] == 1


def test_canonical_refresh_does_not_run_full_legacy_image_sweep(client):
    connector = SelfHealingOnePieceCanonicalConnector()
    with db.SessionLocal() as session:
        assert connector.repair_legacy_records(session, SimpleNamespace(), IngestStats()) == {}
