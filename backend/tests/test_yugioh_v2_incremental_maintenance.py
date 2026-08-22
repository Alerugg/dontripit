from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


def test_incremental_v2_skips_catalog_wide_legacy_repair(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("catalog-wide legacy repair must not run in incremental V2")

    monkeypatch.setattr(YgoProDeckYugiohConnector, "repair_legacy_records", fail_if_called)

    assert connector.repair_legacy_records(
        None,
        None,
        IngestStats(),
        incremental=True,
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
