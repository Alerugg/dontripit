from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


BASE_URL = "https://en.onepiece-cardgame.com/cardlist/"


def _modal(print_id: str, name: str, rarity: str, image: str) -> str:
    return f'''
    <dl class="modalCol" id="{print_id}">
      <div class="infoCol"><span>CHARACTER</span> | <span>{rarity}</span></div>
      <div class="cardName">{name}</div>
      <img data-src="{image}" />
    </dl>
    '''


def test_v2_parser_accepts_families_and_preserves_exact_suffix():
    connector = OnePieceV2Connector()
    html = "".join([
        _modal("OP01-003", "Monkey.D.Luffy", "R", "/images/op.png"),
        _modal("ST01-001_P1", "Monkey.D.Luffy", "L", "/images/st.png"),
        _modal("EB02-001", "Tony Tony.Chopper", "R", "/images/eb.png"),
        _modal("P-001", "Monkey.D.Luffy", "P", "/images/p.png"),
        _modal("PRB01-001_R2", "Sanji", "C", "/images/prb.png"),
    ])
    rows = connector._parse_official_cards_page(html, base_url=BASE_URL)
    assert [row["set_code"] for row in rows] == ["OP-01", "ST-01", "EB-02", "P", "PRB-01"]
    assert rows[1]["variant"] == "p1"
    assert rows[1]["variant_family"] == "parallel"
    assert rows[4]["variant"] == "r2"
    assert rows[4]["variant_family"] == "reprint"


def test_logical_card_key_is_collector_number_not_visible_name():
    connector = OnePieceV2Connector()
    assert connector._logical_card_key("OP01-003") == "onepiece:op01-003"
    assert connector._logical_card_key("P-001") == "onepiece:p-001"
    assert connector._logical_card_key("PRB01-001") == "onepiece:prb01-001"


def test_same_character_different_numbers_create_distinct_logical_cards():
    connector = OnePieceV2Connector()
    cards = {}
    for number, set_code, series_id in [("OP01-003", "OP-01", "1"), ("OP02-041", "OP-02", "2")]:
        connector._merge_official_entry(
            cards_by_key=cards,
            entry={"print_id": number, "collector_number": number, "set_code": set_code, "name": "Monkey.D.Luffy", "rarity": "R", "variant": "default", "image_url": f"https://example.test/{number}.png"},
            series_id=series_id,
            series_label="Release",
            language="en",
        )
    assert set(cards) == {"onepiece:op01-003", "onepiece:op02-041"}


def test_repeated_exact_print_keeps_one_print_and_two_release_links():
    connector = OnePieceV2Connector()
    cards = {}
    entry = {"print_id": "OP01-003_P1", "collector_number": "OP01-003", "set_code": "OP-01", "name": "Monkey.D.Luffy", "rarity": "R", "variant": "p1", "variant_family": "parallel", "image_url": "https://example.test/luffy.png"}
    for series_id in ("1", "99"):
        connector._merge_official_entry(cards_by_key=cards, entry=entry, series_id=series_id, series_label="Release", language="en")
    card = cards["onepiece:op01-003"]
    assert len(card["prints"]) == 1
    assert len(card["prints"][0]["release_appearances"]) == 2


def test_p1_and_p2_are_distinct_physical_prints():
    connector = OnePieceV2Connector()
    cards = {}
    for suffix in ("p1", "p2"):
        connector._merge_official_entry(
            cards_by_key=cards,
            entry={"print_id": f"OP01-003_{suffix.upper()}", "collector_number": "OP01-003", "set_code": "OP-01", "name": "Monkey.D.Luffy", "rarity": "R", "variant": suffix, "variant_family": "parallel", "image_url": f"https://example.test/{suffix}.png"},
            series_id=suffix,
            series_label="Release",
            language="en",
        )
    prints = cards["onepiece:op01-003"]["prints"]
    assert len(prints) == 2
    assert {row["variant"] for row in prints} == {"p1", "p2"}
    assert all(not row["alternate_source_images"] for row in prints)
