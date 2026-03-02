from app.ingest.connectors.fixture_local import FixtureLocalConnector
from app.ingest.connectors.scryfall_mtg import ScryfallMtgConnector

CONNECTORS = {
    "fixture_local": FixtureLocalConnector,
    "scryfall_mtg": ScryfallMtgConnector,
}


def get_connector(name: str):
    connector_cls = CONNECTORS.get(name)
    if connector_cls is None:
        raise ValueError(f"Unknown connector '{name}'. Available: {', '.join(sorted(CONNECTORS))}")
    return connector_cls()
