from app.ingest.base import SourceConnector
from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.connectors.ygoprodeck_yugioh_v2 import YgoProDeckYugiohV2Connector


CONNECTORS = {
    FixtureLocalConnector.name: FixtureLocalConnector,
    ScryfallMtgV2Connector.name: ScryfallMtgV2Connector,
    TcgdexPokemonConnector.name: TcgdexPokemonConnector,
    OnePieceV2Connector.name: OnePieceV2Connector,
    YgoProDeckYugiohV2Connector.name: YgoProDeckYugiohV2Connector,
    RiftboundConnector.name: RiftboundConnector,
}


def get_connector(name: str) -> SourceConnector:
    connector_cls = CONNECTORS.get(name)
    if not connector_cls:
        raise ValueError(f"Unknown connector: {name}")
    return connector_cls()
