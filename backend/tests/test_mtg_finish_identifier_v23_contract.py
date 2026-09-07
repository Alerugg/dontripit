from __future__ import annotations

import pytest

from app.scripts.reconcile_mtg_current_source_v1 import (
    EXACT_IDENTIFIER_SOURCE,
    _exact_print_identifier,
)


def test_finish_aware_identifier_matches_certified_v23_contract():
    source, external_id = _exact_print_identifier(
        {"scryfall_id": "ABCDEF00-1111-2222-3333-444444444444", "variant": "NONFOIL"}
    )

    assert source == "scryfall_finish"
    assert source == EXACT_IDENTIFIER_SOURCE
    assert external_id == "abcdef00-1111-2222-3333-444444444444:nonfoil"


def test_same_scryfall_object_has_distinct_exact_identifiers_per_finish():
    scryfall_id = "abcdef00-1111-2222-3333-444444444444"
    nonfoil = _exact_print_identifier({"scryfall_id": scryfall_id, "variant": "nonfoil"})
    foil = _exact_print_identifier({"scryfall_id": scryfall_id, "variant": "foil"})
    etched = _exact_print_identifier({"scryfall_id": scryfall_id, "variant": "etched"})

    assert nonfoil == ("scryfall_finish", f"{scryfall_id}:nonfoil")
    assert foil == ("scryfall_finish", f"{scryfall_id}:foil")
    assert etched == ("scryfall_finish", f"{scryfall_id}:etched")
    assert len({nonfoil, foil, etched}) == 3


@pytest.mark.parametrize(
    "row",
    [
        {"scryfall_id": "", "variant": "foil"},
        {"scryfall_id": "abc", "variant": ""},
        {},
    ],
)
def test_finish_aware_identifier_fails_closed_without_both_dimensions(row):
    with pytest.raises(AssertionError, match="requires scryfall_id and finish"):
        _exact_print_identifier(row)
