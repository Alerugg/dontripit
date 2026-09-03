from __future__ import annotations

import pytest

from app.scripts.reconcile_mtg_current_source_v1 import (
    MAX_NEW_PRINTS,
    Plan,
    assert_plan_bounds,
    build_plan,
)


def _set(code="abc"):
    return {"code": code, "name": code.upper(), "release_date": None}


def _card(key="mtg:oracle:1", oracle="oracle-1"):
    return {"card_key": key, "name": "Card", "oracle_id": oracle}


def _print(key="mtg:scryfall:s1:nonfoil", collector="1", rarity="rare"):
    return {
        "print_key": key,
        "card_key": "mtg:oracle:1",
        "set_code": "abc",
        "collector_number": collector,
        "language": "en",
        "rarity": rarity,
        "is_foil": False,
        "variant": "nonfoil",
        "scryfall_id": "s1",
    }


def test_plan_is_additive_and_allows_only_collector_correction():
    source_sets = {"abc": _set(), "new": _set("new")}
    source_cards = {"mtg:oracle:1": _card(), "mtg:oracle:2": _card("mtg:oracle:2", "oracle-2")}
    source_prints = {
        "mtg:scryfall:s1:nonfoil": _print(collector="001"),
        "mtg:scryfall:s2:nonfoil": {
            **_print("mtg:scryfall:s2:nonfoil", collector="2"),
            "card_key": "mtg:oracle:2",
            "set_code": "new",
            "scryfall_id": "s2",
        },
    }
    prod_sets = {"abc": {"id": 1}}
    prod_cards = {"mtg:oracle:1": {"id": 1}}
    prod_prints = {"mtg:scryfall:s1:nonfoil": {**_print(collector="1"), "id": 1}}

    plan = build_plan(
        source_sets=source_sets,
        source_cards=source_cards,
        source_prints=source_prints,
        prod_sets=prod_sets,
        prod_cards=prod_cards,
        prod_prints=prod_prints,
    )

    assert plan.new_sets == ("new",)
    assert plan.new_cards == ("mtg:oracle:2",)
    assert plan.new_prints == ("mtg:scryfall:s2:nonfoil",)
    assert plan.collector_corrections == ("mtg:scryfall:s1:nonfoil",)
    assert plan.forbidden_mismatches == ()
    assert plan.write_count == 4


def test_non_collector_field_drift_fails_closed():
    source = _print(rarity="mythic")
    prod = {**_print(rarity="rare"), "id": 1}
    with pytest.raises(AssertionError, match="forbidden identity/field drift"):
        build_plan(
            source_sets={"abc": _set()},
            source_cards={"mtg:oracle:1": _card()},
            source_prints={source["print_key"]: source},
            prod_sets={"abc": {"id": 1}},
            prod_cards={"mtg:oracle:1": {"id": 1}},
            prod_prints={prod["print_key"]: prod},
        )


def test_large_source_drift_exceeds_ceiling_and_fails_closed():
    plan = Plan(
        new_sets=(),
        new_cards=(),
        new_prints=tuple(f"p{i}" for i in range(MAX_NEW_PRINTS + 1)),
        collector_corrections=(),
        forbidden_mismatches=(),
    )
    with pytest.raises(AssertionError, match="new_prints"):
        assert_plan_bounds(plan)


def test_production_extras_are_not_classified_as_deletes():
    source_print = _print()
    prod_print = {**source_print, "id": 1}
    historical = {**_print("mtg:scryfall:historical:nonfoil"), "id": 2, "scryfall_id": "historical"}
    plan = build_plan(
        source_sets={"abc": _set()},
        source_cards={"mtg:oracle:1": _card()},
        source_prints={source_print["print_key"]: source_print},
        prod_sets={"abc": {"id": 1}},
        prod_cards={"mtg:oracle:1": {"id": 1}},
        prod_prints={source_print["print_key"]: prod_print, historical["print_key"]: historical},
    )
    assert plan.write_count == 0
