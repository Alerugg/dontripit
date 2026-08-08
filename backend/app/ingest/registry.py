from app.ingest.base import SourceConnector
from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


CONNECTORS = {
    FixtureLocalConnector.name: FixtureLocalConnector,
    ScryfallMtgV2Connector.name: ScryfallMtgV2Connector,
    TcgdexPokemonConnector.name: TcgdexPokemonConnector,
    OnePieceCanonicalConnector.name: OnePieceCanonicalConnector,
    YgoProDeckYugiohV2Connector.name: YgoProDeckYugiohV2Connector,
    RiftboundConnector.name: RiftboundConnector,
}

# Canonical V2 identity can be stricter than the historical generic connector
# upsert contract. Keep the connector class importable for read-only source
# access (bulk metadata/downloads, audits, snapshot builders), but prevent the
# generic ingest framework and schedulers from invoking a writer that would
# collapse Scryfall finishes back into variant='default'.
WRITE_QUARANTINED_CONNECTORS = {
    "scryfall_mtg": (
        "MTG Canonical V2 uses exact physical Print identity "
        "(Scryfall object id + finish). The legacy generic Scryfall upsert is "
        "quarantined until a finish-aware incremental writer is certified."
    ),
}


def is_connector_write_quarantined(name: str) -> bool:
    return str(name or "").strip() in WRITE_QUARANTINED_CONNECTORS


def get_connector(name: str, *, allow_quarantined: bool = False) -> SourceConnector:
    connector_name = str(name or "").strip()
    connector_cls = CONNECTORS.get(connector_name)
    if not connector_cls:
        raise ValueError(f"Unknown connector: {connector_name}")
    if is_connector_write_quarantined(connector_name) and not allow_quarantined:
        raise RuntimeError(
            f"Write connector quarantined: {connector_name}. "
            f"{WRITE_QUARANTINED_CONNECTORS[connector_name]}"
        )
    return connector_cls()
