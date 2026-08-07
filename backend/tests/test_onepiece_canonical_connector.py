from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.registry import get_connector


def test_registry_uses_canonical_onepiece_connector():
    connector = get_connector("onepiece")
    assert isinstance(connector, OnePieceCanonicalConnector)


def test_canonical_remote_load_uses_only_official_v2(monkeypatch):
    connector = OnePieceCanonicalConnector()
    expected = {"source": "onepiece_official_v2", "cards": []}
    calls = {"official": 0, "punk": 0}

    def official(*, limit=None):
        calls["official"] += 1
        assert limit == 12
        return expected

    def punk(*, limit=None):
        calls["punk"] += 1
        raise AssertionError("legacy/secondary source must not own canonical identity")

    monkeypatch.setattr(connector, "_load_official_cardlist_remote", official)
    monkeypatch.setattr(connector, "_load_punkrecords_remote", punk)

    assert connector._load_remote(limit=12) is expected
    assert calls == {"official": 1, "punk": 0}
