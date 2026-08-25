from pathlib import Path

from app.ingest.base import IngestStats
from app.ingest.connectors import ygoprodeck_yugioh_v2 as module
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.added[-1].id = len(self.added)


def test_fixture_incremental_skips_remote_canonical_reconcile(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fixture incremental run must not call remote canonical reconciler")

    monkeypatch.setattr(connector, "_reconcile_current_canonical_catalog", forbidden)
    result = connector.repair_legacy_records(
        object(),
        object(),
        IngestStats(),
        incremental=True,
        fixture=True,
    )
    assert result == {}


def test_current_reconcile_inserts_only_planned_exact_prints_and_returns_touched_ids(monkeypatch, tmp_path):
    connector = YgoProDeckYugiohV2Connector()
    connector.canonical_print_reconcile_output_dir = Path(tmp_path)
    session = _FakeSession()
    stats = IngestStats()

    source_rows = [
        {
            "source_card_id": "100",
            "set_family": "MAMO",
            "collector_number": "MAMO-EN001",
            "language": "en",
            "rarity": "Ultra Rare",
            "is_foil": False,
            "variant": "rarity-ultra-rare",
            "print_key": "yugioh:100|mamo|mamo-en001|en|nonfoil|rarity-ultra-rare",
            "yugioh_id": "ygo-v2:test-1",
        },
        {
            "source_card_id": "200",
            "set_family": "MAMO",
            "collector_number": "MAMO-EN002",
            "language": "en",
            "rarity": "Secret Rare",
            "is_foil": False,
            "variant": "rarity-secret-rare",
            "print_key": "yugioh:200|mamo|mamo-en002|en|nonfoil|rarity-secret-rare",
            "yugioh_id": "ygo-v2:test-2",
        },
    ]
    planned = [
        {"source": source_rows[0], "card_id": 10, "set_id": 20},
        {"source": source_rows[1], "card_id": 11, "set_id": 20},
    ]

    monkeypatch.setattr(module, "build_canonical_snapshot", lambda output_dir: {"counts": {"prints": 2}})
    monkeypatch.setattr(module, "_load_source_prints", lambda output_dir: ({}, source_rows))

    captured = {}

    def fake_plan(_session, rows, *, max_writes):
        captured["session"] = _session
        captured["rows"] = rows
        captured["max_writes"] = max_writes
        return {"missing_before": 2, "planned": planned}

    monkeypatch.setattr(module, "_plan", fake_plan)

    result = connector._reconcile_current_canonical_prints(session, stats)

    assert captured["session"] is session
    assert captured["rows"] == source_rows
    assert captured["max_writes"] == 500
    assert stats.records_inserted == 2
    assert len(session.added) == 2
    assert [row.print_key for row in session.added] == [item["print_key"] for item in source_rows]
    assert [row.yugioh_id for row in session.added] == ["ygo-v2:test-1", "ygo-v2:test-2"]
    assert result == {
        "card_ids": {10, 11},
        "set_ids": {20},
        "print_ids": {1, 2},
    }


def test_remote_incremental_routes_to_bounded_canonical_catalog_reconcile(monkeypatch):
    connector = YgoProDeckYugiohV2Connector()
    expected = {"card_ids": {1}, "set_ids": {2}, "print_ids": {3}}
    calls = []

    def fake_reconcile(session, stats):
        calls.append((session, stats))
        return expected

    monkeypatch.setattr(connector, "_reconcile_current_canonical_catalog", fake_reconcile)
    session = object()
    stats = IngestStats()
    result = connector.repair_legacy_records(
        session,
        object(),
        stats,
        incremental=True,
        fixture=False,
    )
    assert calls == [(session, stats)]
    assert result == expected
