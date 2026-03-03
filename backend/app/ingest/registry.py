from app.ingest.base import SourceConnector
from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


CONNECTORS = {
    FixtureLocalConnector.name: FixtureLocalConnector,
    ScryfallMtgConnector.name: ScryfallMtgConnector,
    TcgdexPokemonConnector.name: TcgdexPokemonConnector,
    YgoProDeckYugiohConnector.name: YgoProDeckYugiohConnector,
    RiftboundConnector.name: RiftboundConnector,
}


def get_connector(name: str) -> SourceConnector:
    connector_cls = CONNECTORS.get(name)
    if not connector_cls:
        raise ValueError(f"Unknown connector: {name}")
    return connector_cls()
