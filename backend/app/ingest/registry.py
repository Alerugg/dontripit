from app.ingest.connectors.fixture_local import FixtureLocalConnector

CONNECTORS = {
    "fixture_local": FixtureLocalConnector,
}


def get_connector(name: str):
    connector_cls = CONNECTORS.get(name)
    if connector_cls is None:
        raise ValueError(f"Unknown connector '{name}'. Available: {', '.join(sorted(CONNECTORS))}")
    return connector_cls()
