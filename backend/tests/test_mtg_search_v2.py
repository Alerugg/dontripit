from __future__ import annotations

import pytest

from app.search_v2.mtg_advanced import ALLOWED_FILTERS, _boolean, _range
from app.search_v2.mtg_facets import mtg_facets
from app.search_v2.mtg_profiles import compact_card_attributes, compact_print_attributes


def test_mtg_card_profile_is_lean_and_source_backed():
    raw = {
        "layout": "normal",
        "mana_value": 1000000,
        "type_line": "Legendary Creature — Human Wizard",
        "oracle_text": "Long rules text that belongs in canonical attributes, not Search V2.",
        "colors": ["U"],
        "color_identity": ["U", "B"],
        "keywords": ["Flying"],
        "legalities": {"modern": "legal"},
        "reserved": True,
    }
    attrs = compact_card_attributes(raw)
    assert attrs["mana_value"] == 1000000
    assert attrs["card_types"] == ["Creature"]
    assert attrs["color_identity"] == ["U", "B"]
    assert "oracle_text" not in attrs
    assert "legalities" not in attrs
    assert "reserved" not in attrs


def test_mtg_card_types_support_multi_type_lines():
    attrs = compact_card_attributes({"type_line": "Artifact Creature — Golem"})
    assert attrs["card_types"] == ["Artifact", "Creature"]


def test_mtg_print_profile_keeps_collecting_filters_only():
    raw = {
        "released_at": "2026-08-08",
        "set_type": "expansion",
        "artist": "Example Artist",
        "frame": "2015",
        "frame_effects": ["showcase"],
        "border_color": "black",
        "security_stamp": "oval",
        "promo": True,
        "promo_types": ["prerelease"],
        "full_art": False,
        "textless": False,
        "booster": True,
        "reprint": False,
        "oversized": False,
        "story_spotlight": False,
        "reserved": True,
        "scryfall_uri": "https://example.invalid/large-field-not-needed-for-search",
        "tcgplayer_id": 123,
    }
    attrs = compact_print_attributes(raw)
    assert attrs["release_year"] == 2026
    assert attrs["artist"] == "Example Artist"
    assert attrs["promo_types"] == ["prerelease"]
    assert attrs["reserved"] is True
    assert "scryfall_uri" not in attrs
    assert "tcgplayer_id" not in attrs


def test_mtg_facets_are_unique_and_exactly_21():
    facets = mtg_facets()
    keys = [row["key"] for row in facets]
    assert len(facets) == 21
    assert len(keys) == len(set(keys))
    assert {"set", "collector_number", "color_identity", "mana_value", "finish", "artist"} <= set(keys)
    assert "legality" not in keys


def test_advanced_filter_contract_matches_facets_that_are_filterable():
    facet_keys = {row["key"] for row in mtg_facets()}
    assert ALLOWED_FILTERS <= facet_keys
    assert "finish" in ALLOWED_FILTERS
    assert "mana_value" in ALLOWED_FILTERS
    assert "reserved" in ALLOWED_FILTERS


def test_boolean_parser_is_strict():
    assert _boolean(True) is True
    assert _boolean("yes") is True
    assert _boolean("false") is False
    with pytest.raises(ValueError):
        _boolean("maybe")


def test_mana_value_range_accepts_large_and_fractional_values():
    low, high = _range({"min": 0.5, "max": 1000000}, "mana_value", "numeric")
    assert low == 0.5
    assert high == 1000000.0
    with pytest.raises(ValueError):
        _range({"min": 5, "max": 4}, "mana_value", "numeric")
