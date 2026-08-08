from __future__ import annotations

import pytest

from app.mtg_identity_v2 import (
    card_identity_key,
    exact_print_keys,
    finish_values,
    physical_print_key,
    rules_signature,
)


def _card(**overrides):
    payload = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "oracle_id": "11111111-2222-3333-4444-555555555555",
        "name": "Lightning Bolt",
        "layout": "normal",
        "mana_cost": "{R}",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "colors": ["R"],
        "color_identity": ["R"],
        "finishes": ["nonfoil", "foil"],
    }
    payload.update(overrides)
    return payload


def test_oracle_id_is_authoritative_logical_identity():
    first = _card(id="aaaaaaaa-bbbb-cccc-dddd-000000000001", set="lea", collector_number="161")
    second = _card(id="aaaaaaaa-bbbb-cccc-dddd-000000000002", set="4ed", collector_number="208")

    assert card_identity_key(first) == card_identity_key(second)
    assert card_identity_key(first) == "mtg:oracle:11111111-2222-3333-4444-555555555555"


def test_missing_oracle_same_rules_collapses_across_printings_without_using_name_only():
    first = _card(
        id="aaaaaaaa-bbbb-cccc-dddd-000000000003",
        oracle_id=None,
        name="Front // Back",
        layout="reversible_card",
        set="cmb2",
        collector_number="1",
    )
    second = dict(first, id="aaaaaaaa-bbbb-cccc-dddd-000000000004", set="sld", collector_number="999")

    assert card_identity_key(first) == card_identity_key(second)
    assert card_identity_key(first).startswith("mtg:fallback:")


def test_missing_oracle_same_name_layout_but_different_rules_never_merges():
    first = _card(oracle_id=None, name="Front // Back", layout="reversible_card")
    second = dict(first, oracle_text="A materially different rules object.")

    assert rules_signature(first) != rules_signature(second)
    assert card_identity_key(first) != card_identity_key(second)


def test_finish_values_are_exact_and_sorted():
    assert finish_values(_card(finishes=["foil", "nonfoil", "etched"])) == ("etched", "foil", "nonfoil")


def test_exact_print_identity_is_scryfall_object_plus_finish():
    card = _card(finishes=["nonfoil", "foil"])

    keys = exact_print_keys(card)
    assert len(keys) == 2
    assert keys[0] != keys[1]
    assert physical_print_key(card, "foil").endswith(":foil")
    assert physical_print_key(card, "nonfoil").endswith(":nonfoil")


def test_unknown_finish_is_a_hard_gate():
    with pytest.raises(ValueError, match="Unknown Scryfall finish"):
        finish_values(_card(finishes=["serialized"], foil=False, nonfoil=False))


def test_missing_scryfall_id_is_a_hard_gate_for_physical_print():
    with pytest.raises(ValueError, match="Scryfall object id"):
        physical_print_key(_card(id=None), "foil")
