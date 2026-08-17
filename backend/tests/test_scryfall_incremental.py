from datetime import datetime, timezone

from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector


def test_incremental_scryfall_uses_release_order_and_stops_at_cutoff(monkeypatch):
    connector = ScryfallMtgConnector()
    calls = []

    pages = {
        "https://api.scryfall.com/cards/search": {
            "data": [
                {"id": "new-1", "released_at": "2026-06-06"},
                {"id": "cutoff-day", "released_at": "2026-06-04"},
            ],
            "has_more": True,
            "next_page": "https://api.scryfall.com/cards/search?page=2",
        },
        "https://api.scryfall.com/cards/search?page=2": {
            "data": [
                {"id": "too-old", "released_at": "2026-06-03"},
                {"id": "older", "released_at": "2026-06-02"},
            ],
            "has_more": False,
        },
    }

    def fake_request(url, params=None):
        calls.append((url, params))
        return pages[url]

    monkeypatch.setattr(connector, "_request_json", fake_request)

    cards = connector._load_incremental(
        last_run_at=datetime(2026, 6, 4, 7, 35, tzinfo=timezone.utc)
    )

    assert [card["id"] for card in cards] == ["new-1", "cutoff-day"]
    assert len(calls) == 2
    first_params = calls[0][1]
    assert first_params["q"] == "game:paper"
    assert first_params["order"] == "released"
    assert first_params["dir"] == "desc"
    assert first_params["unique"] == "prints"
    assert "date" not in first_params["q"]
    assert calls[1][1] is None


def test_incremental_scryfall_respects_limit_before_next_page(monkeypatch):
    connector = ScryfallMtgConnector()
    calls = []

    def fake_request(url, params=None):
        calls.append((url, params))
        return {
            "data": [
                {"id": "new-1", "released_at": "2026-08-07"},
                {"id": "new-2", "released_at": "2026-08-07"},
            ],
            "has_more": True,
            "next_page": "https://api.scryfall.com/cards/search?page=2",
        }

    monkeypatch.setattr(connector, "_request_json", fake_request)

    cards = connector._load_incremental(limit=1, last_run_at=datetime(2026, 8, 6, tzinfo=timezone.utc))

    assert [card["id"] for card in cards] == ["new-1"]
    assert len(calls) == 1
