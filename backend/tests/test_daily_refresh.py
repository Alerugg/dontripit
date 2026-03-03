import argparse

from app.scripts import daily_refresh


class _FakeStats:
    def __init__(self, files_seen=1, files_skipped=0, inserted=0, updated=0, errors=0):
        self.files_seen = files_seen
        self.files_skipped = files_skipped
        self.records_inserted = inserted
        self.records_updated = updated
        self.errors = errors


class _FakeSession:
    def commit(self):
        return None

    def rollback(self):
        return None


class _FakeSessionFactory:
    def __call__(self):
        return self

    def __enter__(self):
        return _FakeSession()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnector:
    def __init__(self, name, calls, should_fail=False):
        self.name = name
        self.calls = calls
        self.should_fail = should_fail

    def run(self, session, path, **kwargs):
        self.calls.append({"name": self.name, "path": path, "kwargs": kwargs})
        if self.should_fail:
            raise RuntimeError(f"{self.name} failed")
        return _FakeStats(inserted=2, updated=1)


def _args(**overrides):
    data = {
        "path": "backend/data/fixtures",
        "pokemon_set": None,
        "pokemon_all": False,
        "pokemon_limit": 5,
        "mtg_limit": 5,
        "incremental": True,
        "fixture": True,
        "sleep_seconds": 0,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_daily_refresh_calls_both_connectors_and_reindex(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    monkeypatch.setattr(daily_refresh.db, "SessionLocal", _FakeSessionFactory())
    monkeypatch.setattr(daily_refresh, "get_connector", fake_get_connector)
    monkeypatch.setattr(daily_refresh, "rebuild_search_documents", lambda session: {"cards": 3, "sets": 2, "prints": 4})

    summary = daily_refresh.run_daily_refresh(_args())

    assert summary["exit_code"] == 0
    assert summary["pokemon"]["ok"] is True
    assert summary["mtg"]["ok"] is True
    assert summary["reindex"]["ok"] is True
    assert [call["name"] for call in calls] == ["tcgdex_pokemon", "scryfall_mtg"]


def test_daily_refresh_exits_non_zero_when_both_connectors_fail(monkeypatch):
    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=[], should_fail=True)

    monkeypatch.setattr(daily_refresh.db, "SessionLocal", _FakeSessionFactory())
    monkeypatch.setattr(daily_refresh, "get_connector", fake_get_connector)
    monkeypatch.setattr(daily_refresh, "rebuild_search_documents", lambda session: {"cards": 0, "sets": 0, "prints": 0})

    summary = daily_refresh.run_daily_refresh(_args())

    assert summary["pokemon"]["ok"] is False
    assert summary["mtg"]["ok"] is False
    assert summary["exit_code"] == 1
