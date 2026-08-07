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


def test_release_code_parser_handles_all_canonical_product_families():
    assert OnePieceCanonicalConnector._release_set_code("BOOSTER PACK [OP-05]") == "OP-05"
    assert OnePieceCanonicalConnector._release_set_code("STARTER DECK [ST30]") == "ST-30"
    assert OnePieceCanonicalConnector._release_set_code("EXTRA BOOSTER [EB-02]") == "EB-02"
    assert OnePieceCanonicalConnector._release_set_code("PREMIUM BOOSTER [PRB02]") == "PRB-02"


def test_set_names_come_from_origin_release_not_reprint_container():
    payload = {
        "sets": [
            {"code": "OP-05", "name": "WRONG PRB-02 LABEL"},
            {"code": "PRB-02", "name": "WRONG"},
            {"code": "P", "name": "WRONG"},
        ],
        "releases": [
            {"name": "BOOSTER PACK -AWAKENING OF THE NEW ERA- [OP-05]"},
            {"name": "PREMIUM BOOSTER -ONE PIECE CARD THE BEST vol.2- [PRB-02]"},
        ],
        "diagnostics": {},
    }

    result = OnePieceCanonicalConnector._canonicalize_set_names(payload)
    names = {row["code"]: row["name"] for row in result["sets"]}

    assert names["OP-05"] == "BOOSTER PACK -AWAKENING OF THE NEW ERA- [OP-05]"
    assert names["PRB-02"] == "PREMIUM BOOSTER -ONE PIECE CARD THE BEST vol.2- [PRB-02]"
    assert names["P"] == "Promotion Cards"
    assert result["diagnostics"]["canonical_set_names_unmatched"] == []
