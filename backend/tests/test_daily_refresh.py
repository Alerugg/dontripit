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
        "pokemon_all_sets": False,
        "pokemon_sets": None,
        "batch_size": 5,
        "pokemon_limit": 5,
        "onepiece_limit": 5,
        "mtg_limit": 5,
        "skip_pokemon": False,
        "skip_onepiece": False,
        "incremental": True,
        "fixture": True,
        "yugioh_limit": 5,
        "riftbound_limit": 5,
        "riftbound_fixture": True,
        "sleep_seconds": 0,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def _patch_runtime(monkeypatch, fake_get_connector, reindex=None):
    monkeypatch.setattr(daily_refresh.db, "SessionLocal", _FakeSessionFactory())
    monkeypatch.setattr(daily_refresh, "get_connector", fake_get_connector)
    monkeypatch.setattr(
        daily_refresh,
        "rebuild_search_documents",
        reindex or (lambda session: {"cards": 3, "sets": 2, "prints": 4}),
    )


def test_daily_refresh_calls_all_connectors_and_skips_global_reindex_in_incremental(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(_args())

    assert summary["exit_code"] == 0
    assert summary["status"] == "success"
    assert summary["pokemon"]["ok"] is True
    assert summary["onepiece"]["ok"] is True
    assert summary["mtg"]["ok"] is True
    assert summary["reindex"]["ok"] is True
    assert summary["reindex"]["stats"]["skipped"] is True
    assert summary["reindex"]["trigger"] == "connector_managed_or_no_mutations"
    assert set(summary.keys()) >= {
        "pokemon",
        "onepiece",
        "mtg",
        "yugioh",
        "riftbound",
        "connectors",
        "status",
        "reindex",
        "exit_code",
    }
    assert [call["name"] for call in calls] == [
        "tcgdex_pokemon",
        "onepiece",
        "scryfall_mtg",
        "ygoprodeck_yugioh",
        "riftbound",
    ]


def test_daily_refresh_partial_failure_is_degraded_and_non_zero(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls, should_fail=name == "scryfall_mtg")

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(_args())

    assert summary["status"] == "degraded"
    assert summary["exit_code"] == 1
    assert summary["mtg"]["ok"] is False
    assert summary["onepiece"]["ok"] is True
    assert summary["connectors"]["failed"] == ["mtg"]
    assert "onepiece" in summary["connectors"]["succeeded"]


def test_daily_refresh_exits_non_zero_when_all_attempted_connectors_fail(monkeypatch):
    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=[], should_fail=True)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(_args())

    assert summary["pokemon"]["ok"] is False
    assert summary["onepiece"]["ok"] is False
    assert summary["mtg"]["ok"] is False
    assert summary["yugioh"]["ok"] is False
    assert summary["riftbound"]["ok"] is False
    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1


def test_daily_refresh_pokemon_requires_all_requested_sets_to_succeed(monkeypatch):
    calls = []

    def fake_get_connector(name):
        should_fail = name == "tcgdex_pokemon" and len([row for row in calls if row["name"] == "tcgdex_pokemon"]) == 1
        return _FakeConnector(name=name, calls=calls, should_fail=should_fail)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(_args(pokemon_sets="base1,sv1", onepiece_limit=0, mtg_limit=0, yugioh_limit=0, riftbound_limit=0))

    assert len(summary["pokemon"]["runs"]) == 2
    assert summary["pokemon"]["ok"] is False
    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1


def test_daily_refresh_supports_explicit_pokemon_sets(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(_args(pokemon_sets="base1,sv1", batch_size=7, pokemon_limit=7))

    pokemon_calls = [call for call in calls if call["name"] == "tcgdex_pokemon"]
    assert [call["kwargs"]["set"] for call in pokemon_calls] == ["base1", "sv1"]
    assert all(call["kwargs"]["limit"] == 7 for call in pokemon_calls)
    assert summary["batch_size"] == 7


def test_daily_refresh_respects_zero_limits_as_skip(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(
        _args(pokemon_limit=0, onepiece_limit=0, mtg_limit=0, yugioh_limit=5, riftbound_limit=0)
    )

    assert [call["name"] for call in calls] == ["ygoprodeck_yugioh"]
    assert summary["pokemon"]["skipped"] is True
    assert summary["onepiece"]["skipped"] is True
    assert summary["mtg"]["skipped"] is True
    assert summary["yugioh"]["skipped"] is False
    assert summary["riftbound"]["skipped"] is True
    assert summary["connectors"]["skipped"] == ["pokemon", "onepiece", "mtg", "riftbound"]


def test_daily_refresh_yugioh_run_is_present_when_connector_fails(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls, should_fail=name == "ygoprodeck_yugioh")

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(
        _args(pokemon_limit=0, onepiece_limit=0, mtg_limit=0, yugioh_limit=5, riftbound_limit=0)
    )

    assert [call["name"] for call in calls] == ["ygoprodeck_yugioh"]
    assert summary["yugioh"]["run"] is not None
    assert summary["yugioh"]["run"]["connector"] == "ygoprodeck_yugioh"
    assert summary["yugioh"]["run"]["ok"] is False
    assert "failed" in summary["yugioh"]["run"]["error"]
    assert summary["exit_code"] == 1


def test_daily_refresh_none_limits_execute_connectors(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    _patch_runtime(monkeypatch, fake_get_connector)
    summary = daily_refresh.run_daily_refresh(
        _args(pokemon_limit=None, onepiece_limit=None, mtg_limit=None, yugioh_limit=None, riftbound_limit=None)
    )

    assert [call["name"] for call in calls] == [
        "tcgdex_pokemon",
        "onepiece",
        "scryfall_mtg",
        "ygoprodeck_yugioh",
        "riftbound",
    ]
    assert summary["pokemon"]["skipped"] is False
    assert summary["onepiece"]["skipped"] is False
    assert summary["yugioh"]["skipped"] is False


def test_daily_refresh_runs_global_reindex_for_non_incremental_refresh(monkeypatch):
    calls = []
    reindex_called = {"value": 0}

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    def fake_reindex(session):
        reindex_called["value"] += 1
        return {"cards": 5, "sets": 4, "prints": 3}

    _patch_runtime(monkeypatch, fake_get_connector, reindex=fake_reindex)
    summary = daily_refresh.run_daily_refresh(_args(incremental=False))

    assert summary["reindex"]["ok"] is True
    assert summary["reindex"]["trigger"] == "full_refresh"
    assert reindex_called["value"] == 1


def test_daily_refresh_pokemon_all_fetches_full_set_list(monkeypatch):
    calls = []

    def fake_get_connector(name):
        return _FakeConnector(name=name, calls=calls)

    _patch_runtime(monkeypatch, fake_get_connector)
    monkeypatch.setattr(daily_refresh, "_fetch_all_pokemon_sets", lambda: ["base1", "sv1", "sv2"])

    summary = daily_refresh.run_daily_refresh(_args(pokemon_all=True, pokemon_limit=2))

    pokemon_calls = [call for call in calls if call["name"] == "tcgdex_pokemon"]
    assert [call["kwargs"]["set"] for call in pokemon_calls] == ["base1", "sv1", "sv2"]
    assert summary["pokemon"]["set_source"] == "tcgdex"


def test_build_refresh_args_exposes_onepiece_controls():
    args = daily_refresh.build_refresh_args(onepiece_limit=250, skip_onepiece=True)
    assert args.onepiece_limit == 250
    assert args.skip_onepiece is True
