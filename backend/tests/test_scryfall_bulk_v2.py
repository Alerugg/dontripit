from datetime import datetime, timezone

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.registry import get_connector


def test_registry_uses_scryfall_v2_connector():
    connector = get_connector("scryfall_mtg")
    assert isinstance(connector, ScryfallMtgV2Connector)


def test_bulk_incremental_filters_paper_cutoff_dedupes_and_sorts(monkeypatch):
    connector = ScryfallMtgV2Connector()
    payload = [
        {"id": "old", "released_at": "2026-06-03", "games": ["paper"]},
        {"id": "digital", "released_at": "2026-06-08", "games": ["arena"]},
        {"id": "same-day", "released_at": "2026-06-04", "games": ["paper"]},
        {"id": "newer", "released_at": "2026-06-07", "games": ["paper", "arena"]},
        {"id": "newer", "released_at": "2026-06-07", "games": ["paper"]},
    ]
    monkeypatch.setattr(connector, "_download_default_cards", lambda: payload)

    cards = connector._load_incremental(
        last_run_at=datetime(2026, 6, 4, 7, 35, tzinfo=timezone.utc)
    )

    assert [card["id"] for card in cards] == ["newer", "same-day"]


def test_bulk_incremental_respects_limit(monkeypatch):
    connector = ScryfallMtgV2Connector()
    monkeypatch.setattr(
        connector,
        "_download_default_cards",
        lambda: [
            {"id": "a", "released_at": "2026-08-07", "games": ["paper"]},
            {"id": "b", "released_at": "2026-08-06", "games": ["paper"]},
        ],
    )

    cards = connector._load_incremental(
        limit=1,
        last_run_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert [card["id"] for card in cards] == ["a"]


def test_full_remote_filters_digital_cards(monkeypatch):
    connector = ScryfallMtgV2Connector()
    monkeypatch.setattr(
        connector,
        "_download_default_cards",
        lambda: [
            {"id": "paper", "released_at": "2026-08-01", "games": ["paper"]},
            {"id": "arena", "released_at": "2026-08-07", "games": ["arena"]},
        ],
    )

    cards = connector._load_remote()
    assert [card["id"] for card in cards] == ["paper"]
