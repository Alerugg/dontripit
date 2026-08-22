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
