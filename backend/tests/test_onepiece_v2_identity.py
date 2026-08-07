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


def test_v2_parser_accepts_op_st_eb_p_and_prb_families():
    connector = OnePieceV2Connector()
    html = "".join(
        [
            _modal("OP01-003", "Monkey.D.Luffy", "R", "/images/op.png"),
            _modal("ST01-001_P1", "Monkey.D.Luffy", "L", "/images/st.png"),
            _modal("EB02-001", "Tony Tony.Chopper", "R", "/images/eb.png"),
            _modal("P-001", "Monkey.D.Luffy", "P", "/images/p.png"),
            _modal("PRB01-001", "Sanji", "C", "/images/prb.png"),
        ]
    )

    rows = connector._parse_official_cards_page(html, base_url=BASE_URL)

    assert [row["set_code"] for row in rows] == ["OP-01", "ST-01", "EB-02", "P", "PRB-01"]
    assert rows[0]["collector_number"] == "OP01-003"
    assert rows[1]["variant"] == "parallel"
    assert rows[3]["card_id"] == "P-001"
    assert rows[4]["card_id"] == "PRB01-001"


def test_logical_card_key_is_collector_number_not_visible_name():
    connector = OnePieceV2Connector()
    assert connector._logical_card_key("OP01-003") == "onepiece:op01-003"
    assert connector._logical_card_key("P-001") == "onepiece:p-001"
    assert connector._logical_card_key("PRB01-001") == "onepiece:prb01-001"


def test_same_character_different_numbers_create_distinct_logical_cards():
    connector = OnePieceV2Connector()
    cards = {}
    connector._merge_official_entry(
        cards_by_key=cards,
        entry={
            "print_id": "OP01-003",
            "collector_number": "OP01-003",
            "set_code": "OP-01",
            "name": "Monkey.D.Luffy",
            "rarity": "R",
            "variant": "default",
            "image_url": "https://example.test/op01.png",
        },
        series_id="1",
        series_label="Romance Dawn",
        language="en",
    )
    connector._merge_official_entry(
        cards_by_key=cards,
        entry={
            "print_id": "OP02-041",
            "collector_number": "OP02-041",
            "set_code": "OP-02",
            "name": "Monkey.D.Luffy",
            "rarity": "R",
            "variant": "default",
            "image_url": "https://example.test/op02.png",
        },
        series_id="2",
        series_label="Paramount War",
        language="en",
    )

    assert set(cards) == {"onepiece:op01-003", "onepiece:op02-041"}


def test_repeated_exact_print_keeps_one_print_and_two_release_links():
    connector = OnePieceV2Connector()
    cards = {}
    entry = {
        "print_id": "OP01-003",
        "collector_number": "OP01-003",
        "set_code": "OP-01",
        "name": "Monkey.D.Luffy",
        "rarity": "R",
        "variant": "default",
        "image_url": "https://example.test/luffy.png",
    }

    connector._merge_official_entry(
        cards_by_key=cards,
        entry=entry,
        series_id="1",
        series_label="Original Release",
        language="en",
    )
    connector._merge_official_entry(
        cards_by_key=cards,
        entry=entry,
        series_id="99",
        series_label="Reprint Product",
        language="en",
    )

    card = cards["onepiece:op01-003"]
    assert len(card["prints"]) == 1
    assert len(card["prints"][0]["release_appearances"]) == 2


def test_different_image_under_same_identity_is_flagged_not_silently_replaced():
    connector = OnePieceV2Connector()
    cards = {}
    base = {
        "print_id": "OP01-003",
        "collector_number": "OP01-003",
        "set_code": "OP-01",
        "name": "Monkey.D.Luffy",
        "rarity": "R",
        "variant": "default",
    }

    connector._merge_official_entry(
        cards_by_key=cards,
        entry=base | {"image_url": "https://example.test/a.png"},
        series_id="1",
        series_label="A",
        language="en",
    )
    connector._merge_official_entry(
        cards_by_key=cards,
        entry=base | {"image_url": "https://example.test/b.png"},
        series_id="2",
        series_label="B",
        language="en",
    )

    print_row = cards["onepiece:op01-003"]["prints"][0]
    assert print_row["image_url"] == "https://example.test/a.png"
    assert print_row["alternate_source_images"] == ["https://example.test/b.png"]
    assert len(print_row["release_appearances"]) == 2
