from __future__ import annotations

import pytest

from app.scripts.reconcile_mtg_current_source_batched_v1 import (
    MAX_BATCH_WRITES,
    MAX_TOTAL_WRITES,
    _delta_write_count,
    _next_batch,
    project_batches,
)


def _delta(*, sets=0, cards=0, prints=0, corrections=0, forbidden=None):
    return {
        "new_sets": [f"s{i:04d}" for i in range(sets)],
        "new_cards": [f"c{i:04d}" for i in range(cards)],
        "new_prints": [f"p{i:05d}" for i in range(prints)],
        "collector_corrections": [f"x{i:04d}" for i in range(corrections)],
        "forbidden_mismatches": list(forbidden or []),
        "production_extra_sets": [],
        "production_extra_cards": [],
        "production_extra_prints": [],
    }


def test_current_delta_projects_to_500_500_51_batches():
    delta = _delta(sets=2, cards=10, prints=1026, corrections=13)

    batches = project_batches(delta)

    assert [batch.write_count for batch in batches] == [500, 500, 51]
    assert batches[0].new_sets == tuple(delta["new_sets"])
    assert batches[0].new_cards == tuple(delta["new_cards"])
    assert batches[0].collector_corrections == tuple(delta["collector_corrections"])
    assert len(batches[0].new_prints) == 475
    assert len(batches[1].new_prints) == 500
    assert len(batches[2].new_prints) == 51


def test_batch_planning_is_deterministic_and_parent_first():
    delta = _delta(sets=2, cards=3, prints=600, corrections=4)

    first = _next_batch(delta)
    second = _next_batch(delta)

    assert first == second
    assert first.write_count == MAX_BATCH_WRITES
    assert first.new_sets == tuple(delta["new_sets"])
    assert first.new_cards == tuple(delta["new_cards"])
    assert first.collector_corrections == tuple(delta["collector_corrections"])
    assert len(first.new_prints) == MAX_BATCH_WRITES - 2 - 3 - 4


def test_forbidden_mismatch_still_fails_closed():
    with pytest.raises(AssertionError, match="forbidden mismatches"):
        project_batches(_delta(prints=1, forbidden=[{"print_key": "p1", "fields": ["rarity"]}]))


def test_total_write_ceiling_is_independent_of_batch_ceiling():
    delta = _delta(prints=MAX_TOTAL_WRITES + 1)
    with pytest.raises(AssertionError, match="total ceiling"):
        project_batches(delta)


def test_empty_delta_is_zero_batches_and_zero_writes():
    delta = _delta()
    assert _delta_write_count(delta) == 0
    assert project_batches(delta) == []


def test_single_batch_never_exceeds_500_writes():
    delta = _delta(sets=1, cards=2, prints=1000, corrections=3)
    batch = _next_batch(delta)
    assert batch.write_count == 500
    assert len(batch.new_prints) == 494
