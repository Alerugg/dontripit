from __future__ import annotations

import pytest

from app.scripts.apply_mtg_v22_snapshot_live import _assert_supported_schema_revision
from app.scripts.build_mtg_v2_snapshot_v22 import card_attributes, print_attributes


def _card(**overrides):
    payload = {
        "layout": "normal",
        "mana_cost": "{U}",
        "cmc": 1.0,
        "type_line": "Instant",
        "oracle_text": "Draw a card.",
        "colors": ["U"],
        "color_identity": ["U"],
        "keywords": [],
        "power": None,
        "toughness": None,
        "loyalty": None,
        "defense": None,
        "produced_mana": [],
        "card_faces": [],
        "finishes": ["nonfoil"],
        "released_at": "1993-08-05",
        "set_type": "core",
        "set_id": "set-id",
        "artist": "Artist",
        "artist_ids": [],
        "frame_effects": [],
        "promo_types": [],
        "legalities": {"vintage": "legal", "legacy": "legal"},
        "reserved": True,
    }
    payload.update(overrides)
    return payload


def test_mtg_snapshot_loader_accepts_required_schema_and_descendants():
    _assert_supported_schema_revision("20260808_25")
    _assert_supported_schema_revision("20260810_30")


def test_mtg_snapshot_loader_rejects_schema_before_identity_revision():
    with pytest.raises(AssertionError, match="does not include required MTG schema"):
        _assert_supported_schema_revision("20260808_24")


def test_legality_and_reserved_do_not_change_logical_card_payload():
    original = _card()
    commemorative = _card(
        legalities={"vintage": "not_legal", "legacy": "not_legal"},
        reserved=False,
    )

    assert card_attributes(original) == card_attributes(commemorative)
    assert "legalities" not in card_attributes(original)
    assert "reserved" not in card_attributes(original)


def test_legality_and_reserved_are_preserved_per_print_context():
    original = _card()
    commemorative = _card(
        legalities={"vintage": "not_legal", "legacy": "not_legal"},
        reserved=False,
    )

    original_attrs = print_attributes(original, "nonfoil")
    commemorative_attrs = print_attributes(commemorative, "nonfoil")

    assert original_attrs["legalities"] == {"vintage": "legal", "legacy": "legal"}
    assert original_attrs["reserved"] is True
    assert commemorative_attrs["legalities"] == {"vintage": "not_legal", "legacy": "not_legal"}
    assert commemorative_attrs["reserved"] is False


def test_stable_rules_fields_remain_logical_card_attributes():
    attrs = card_attributes(_card())

    assert attrs["mana_cost"] == "{U}"
    assert attrs["mana_value"] == 1.0
    assert attrs["type_line"] == "Instant"
    assert attrs["oracle_text"] == "Draw a card."
    assert attrs["colors"] == ["U"]
    assert attrs["color_identity"] == ["U"]


def test_order_insensitive_logical_lists_are_canonicalized():
    first = _card(
        colors=["U", "R"],
        color_identity=["R", "U"],
        keywords=["Storm", "Buyback"],
        produced_mana=["U", "R"],
    )
    second = _card(
        colors=["R", "U"],
        color_identity=["U", "R"],
        keywords=["Buyback", "Storm"],
        produced_mana=["R", "U"],
    )

    assert card_attributes(first) == card_attributes(second)
    assert card_attributes(first)["keywords"] == ["Buyback", "Storm"]


def test_face_order_is_preserved_but_face_colors_are_canonicalized():
    first = _card(
        card_faces=[
            {"name": "Front", "colors": ["U", "R"]},
            {"name": "Back", "colors": ["G"]},
        ]
    )
    second = _card(
        card_faces=[
            {"name": "Front", "colors": ["R", "U"]},
            {"name": "Back", "colors": ["G"]},
        ]
    )

    assert card_attributes(first) == card_attributes(second)
    assert [face["name"] for face in card_attributes(first)["faces"]] == ["Front", "Back"]
