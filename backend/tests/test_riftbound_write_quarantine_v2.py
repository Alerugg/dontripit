from __future__ import annotations

import pytest

from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.registry import WRITE_QUARANTINED_CONNECTORS, get_connector, is_connector_write_quarantined
from app.jobs import schedule


# Re-run marker: production traffic moved from Railway to Vercel on 2026-08-09.
def test_riftbound_generic_writer_is_quarantined():
    assert is_connector_write_quarantined("riftbound") is True
    assert "riftbound" in WRITE_QUARANTINED_CONNECTORS
    with pytest.raises(RuntimeError, match="Write connector quarantined: riftbound"):
        get_connector("riftbound")


def test_riftbound_source_class_remains_importable_for_read_only_audits():
    connector = RiftboundConnector()
    assert connector.name == "riftbound"
    assert callable(connector._build_backends)


def test_scheduler_filters_explicit_riftbound_configuration():
    jobs = schedule._scheduled_jobs("riftbound:daily,fixture_local:manual")
    assert jobs == [("fixture_local", "manual")]
