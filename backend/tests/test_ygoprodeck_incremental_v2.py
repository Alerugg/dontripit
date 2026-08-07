from datetime import datetime, timezone

from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector
from app.ingest.registry import get_connector


def test_registry_uses_yugioh_v2_connector():
    connector = get_connector("ygoprodeck_yugioh")
    assert isinstance(connector, YgoProDeckYugiohV2Connector)


def test_incremental_yugioh_requests_new_cards_from_cutoff(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()
    calls = []

    def fake_request(url, params=None):
        calls.append((url, params))
        return {
            "data": [
                {"id": 2, "name": "Newer Card"},
                {"id": 1, "name": "Older Card"},
            ]
        }

    monkeypatch.setattr(connector, "_request_json", fake_request)
    cards = connector._load_incremental_remote(
        limit=2,
        page_size=2,
        last_run_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
    )

    assert [card["id"] for card in cards] == [2, 1]
    assert len(calls) == 1
    params = calls[0][1]
    assert params["sort"] == "new"
    assert params["startdate"] == "2026-07-04"
    assert params["dateregion"] == "tcg"
    assert params["num"] == 2
    assert params["offset"] == 0


def test_incremental_yugioh_paginates_without_duplicates(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()
    calls = []

    def fake_request(url, params=None):
        calls.append(dict(params or {}))
        if params["offset"] == 0:
            return {"data": [{"id": 3}, {"id": 2}]}
        return {"data": [{"id": 2}, {"id": 1}]}

    monkeypatch.setattr(connector, "_request_json", fake_request)
    cards = connector._load_incremental_remote(limit=3, page_size=2, last_run_at=None)

    assert [card["id"] for card in cards] == [3, 2, 1]
    assert [call["offset"] for call in calls] == [0, 2]
    assert all(call["sort"] == "new" for call in calls)
