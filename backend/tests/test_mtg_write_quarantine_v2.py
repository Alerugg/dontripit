from __future__ import annotations

import pytest

from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.registry import (
    WRITE_QUARANTINED_CONNECTORS,
    get_connector,
    is_connector_write_quarantined,
)
from app.jobs import runtime, schedule


def test_scryfall_generic_writer_is_quarantined():
    assert is_connector_write_quarantined("scryfall_mtg") is True
    assert "scryfall_mtg" in WRITE_QUARANTINED_CONNECTORS

    with pytest.raises(RuntimeError, match="Write connector quarantined: scryfall_mtg"):
        get_connector("scryfall_mtg")


def test_scryfall_source_class_remains_available_for_read_only_snapshot_tools():
    connector = ScryfallMtgV2Connector()
    assert connector.name == "scryfall_mtg"
    assert callable(connector._bulk_metadata)
    assert callable(connector.probe_remote)


def test_scheduler_filters_even_explicit_scryfall_configuration():
    jobs = schedule._scheduled_jobs("scryfall_mtg:daily,fixture_local:manual")
    assert jobs == [("fixture_local", "manual")]


def test_scheduler_defaults_do_not_schedule_scryfall():
    assert "scryfall_mtg" not in schedule.DEFAULT_SCHEDULER_JOBS
    assert "scryfall_mtg" not in runtime.DEFAULT_SCHEDULER_JOBS


def test_non_quarantined_connectors_remain_resolvable():
    assert is_connector_write_quarantined("fixture_local") is False
    assert get_connector("fixture_local").name == "fixture_local"
