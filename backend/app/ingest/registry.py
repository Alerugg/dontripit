from app.ingest.base import SourceConnector
from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector


CONNECTORS = {
    FixtureLocalConnector.name: FixtureLocalConnector,
    ScryfallMtgConnector.name: ScryfallMtgConnector,
    TcgdexPokemonConnector.name: TcgdexPokemonConnector,
}


def get_connector(name: str) -> SourceConnector:
    connector_cls = CONNECTORS.get(name)
    if not connector_cls:
        raise ValueError(f"Unknown connector: {name}")
    return connector_cls()
