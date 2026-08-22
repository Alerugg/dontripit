from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


def test_incremental_v2_skips_catalog_wide_legacy_repair():
    connector = YgoProDeckYugiohV2Connector()
    assert connector.repair_legacy_records(
        None,
        None,
        IngestStats(),
        incremental=True,
    ) == {}
