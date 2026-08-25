from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


def test_incremental_v2_uses_bounded_current_source_reconcile_not_catalog_wide_legacy_repair(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()
    observed = {}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("catalog-wide legacy repair must not run in incremental V2")

    def fake_current_reconcile(session, stats):
        observed["session"] = session
        observed["stats"] = stats
        return {"card_ids": {1}, "set_ids": {2}, "print_ids": {3}}

    monkeypatch.setattr(YgoProDeckYugiohConnector, "repair_legacy_records", fail_if_called)
    monkeypatch.setattr(connector, "_reconcile_current_canonical_catalog", fake_current_reconcile)

    stats = IngestStats()
    result = connector.repair_legacy_records(
        "session",
        "source",
        stats,
        incremental=True,
        fixture=False,
    )

    assert result == {"card_ids": {1}, "set_ids": {2}, "print_ids": {3}}
    assert observed == {"session": "session", "stats": stats}


def test_incremental_fixture_skips_remote_current_source_reconcile(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("fixture maintenance must stay local")

    monkeypatch.setattr(connector, "_reconcile_current_canonical_catalog", fail_if_called)

    assert connector.repair_legacy_records(
        "session",
        "source",
        IngestStats(),
        incremental=True,
        fixture=True,
    ) == {}


def test_full_v2_runs_inherited_legacy_repair_with_parent_gate_enabled(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()
    observed = {}

    def fake_repair(self, session, source, stats, **kwargs):
        observed["session"] = session
        observed["source"] = source
        observed["incremental"] = kwargs.get("incremental")
        return {"legacy_repaired": 7}

    monkeypatch.setattr(YgoProDeckYugiohConnector, "repair_legacy_records", fake_repair)

    result = connector.repair_legacy_records(
        "session",
        "source",
        IngestStats(),
        incremental=False,
    )

    assert result == {"legacy_repaired": 7}
    assert observed == {
        "session": "session",
        "source": "source",
        "incremental": True,
    }
