from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.ingest.registry import get_connector
from app.models import Card, Print


def _promo_payload(*, collector: str, name: str, language: str, region: str, image_url: str) -> dict:
    return {
        "source": "onepiece_official_v2",
        "language": language,
        "region": region,
        "sets": [{"id": "p", "code": "P", "name": "Promotion Cards"}],
        "releases": [],
        "cards": [
            {
                "id": f"onepiece:{collector.lower()}",
                "name": name,
                "collector_number": collector,
                "prints": [
                    {
                        "id": collector,
                        "identity_key": f"{collector.lower()}:{language}:default",
                        "set_code": "P",
                        "collector_number": collector,
                        "rarity": "P",
                        "variant": "default",
                        "variant_family": "default",
                        "image_url": image_url,
                        "details": {},
                        "release_appearances": [],
                        "alternate_source_images": [],
                        "alternate_source_details": [],
                    }
                ],
            }
        ],
        "diagnostics": {},
    }


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


def test_remote_load_federates_global_asia_and_japan_and_audits_promos(monkeypatch):
    connector = OnePieceCanonicalConnector()
    global_en = _promo_payload(
        collector="P-149",
        name="Global Promo",
        language="en",
        region="global-en",
        image_url="https://en.onepiece-cardgame.com/P-149.png",
    )
    asia_en = _promo_payload(
        collector="P-150",
        name="Kuzan",
        language="en",
        region="asia-en",
        image_url="https://asia-en.onepiece-cardgame.com/P-150.png",
    )
    jp = _promo_payload(
        collector="P-151",
        name="日本語プロモ",
        language="ja",
        region="jp",
        image_url="https://www.onepiece-cardgame.com/P-151.png",
    )

    monkeypatch.setattr(connector, "_load_official_cardlist_remote", lambda *, limit=None: global_en)
    monkeypatch.setattr(connector, "_load_official_asia_en_cardlist_remote", lambda *, limit=None: asia_en)
    monkeypatch.setattr(connector, "_load_official_jp_cardlist_remote", lambda *, limit=None: jp)

    loaded = connector.load(fixture=False, limit=20)
    assert [path.name for path, _payload, _checksum in loaded] == [
        "onepiece_official_global_en.json",
        "onepiece_official_asia_en.json",
        "onepiece_official_jp.json",
    ]
    audit = loaded[0][1]["diagnostics"]["regional_promo_audit"]
    assert audit["global_en_count"] == 1
    assert audit["asia_en_count"] == 1
    assert audit["jp_count"] == 1
    assert audit["regional_only_vs_global"] == ["P-150", "P-151"]
    assert audit["asia_only_vs_global"] == ["P-150"]
    assert audit["jp_only_vs_global"] == ["P-151"]


def test_regional_promo_upsert_keeps_card_key_identity_and_language_prints(client):
    connector = OnePieceCanonicalConnector()
    stats = IngestStats()
    global_en = _promo_payload(
        collector="P-100",
        name="Kuzan",
        language="en",
        region="global-en",
        image_url="https://en.onepiece-cardgame.com/P-100.png",
    )
    asia_en = _promo_payload(
        collector="P-150",
        name="Kuzan",
        language="en",
        region="asia-en",
        image_url="https://asia-en.onepiece-cardgame.com/P-150.png",
    )
    jp = _promo_payload(
        collector="P-150",
        name="クザン",
        language="ja",
        region="jp",
        image_url="https://www.onepiece-cardgame.com/P-150.png",
    )

    with db.SessionLocal() as session:
        connector.upsert(session, global_en, stats)
        connector.upsert(session, asia_en, stats)
        connector.upsert(session, jp, stats)
        session.commit()

        cards = session.execute(
            select(Card).where(Card.card_key.in_(["onepiece:p-100", "onepiece:p-150"])).order_by(Card.card_key)
        ).scalars().all()
        assert [(row.card_key, row.name) for row in cards] == [
            ("onepiece:p-100", "Kuzan"),
            ("onepiece:p-150", "Kuzan"),
        ]

        p150 = next(row for row in cards if row.card_key == "onepiece:p-150")
        prints = session.execute(
            select(Print).where(Print.card_id == p150.id).order_by(Print.language)
        ).scalars().all()
        assert [(row.collector_number, row.language, row.variant) for row in prints] == [
            ("P-150", "en", "default"),
            ("P-150", "ja", "default"),
        ]


def test_release_code_parser_handles_all_canonical_product_families():
    assert OnePieceCanonicalConnector._release_set_code("BOOSTER PACK [OP-05]") == "OP-05"
    assert OnePieceCanonicalConnector._release_set_code("STARTER DECK [ST30]") == "ST-30"
    assert OnePieceCanonicalConnector._release_set_code("EXTRA BOOSTER [EB-02]") == "EB-02"
    assert OnePieceCanonicalConnector._release_set_code("PREMIUM BOOSTER [PRB02]") == "PRB-02"
    assert OnePieceCanonicalConnector._release_set_codes("BOOSTER PACK [OP14-EB04]") == ["OP-14", "EB-04"]


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


def test_hybrid_release_keeps_ambiguous_set_neutral_and_products_as_releases():
    payload = {
        "sets": [
            {"code": "OP-14", "name": "WRONG"},
            {"code": "OP-15", "name": "WRONG"},
            {"code": "EB-04", "name": "WRONG"},
        ],
        "releases": [
            {"name": "BOOSTER PACK -THE AZURE SEA'S SEVEN- [OP14-EB04]"},
            {"name": "BOOSTER PACK -ADVENTURE ON KAMI'S ISLAND- [OP15-EB04]"},
        ],
        "diagnostics": {},
    }

    result = OnePieceCanonicalConnector._canonicalize_set_names(payload)
    names = {row["code"]: row["name"] for row in result["sets"]}

    assert "OP14-EB04" in names["OP-14"]
    assert "OP15-EB04" in names["OP-15"]
    assert names["EB-04"] == "Extra Booster Series [EB-04]"
    assert result["diagnostics"]["canonical_set_names_unmatched"] == []
    assert len(result["diagnostics"]["canonical_set_names_ambiguous"]["EB-04"]) == 2
