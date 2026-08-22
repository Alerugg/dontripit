import pytest

from app.scripts.build_yugioh_v2_snapshot_canonical import (
    EXPECTED_STATIC,
    SOURCE_MINIMUMS,
    _assert_snapshot_gates,
)


def _valid_counts(**overrides):
    counts = {
        "source_cards": SOURCE_MINIMUMS["source_cards"],
        "releases": SOURCE_MINIMUMS["releases"],
        "source_card_aliases_merged": EXPECTED_STATIC["source_card_aliases_merged"],
        "excluded_source_print_rows": EXPECTED_STATIC["excluded_source_print_rows"],
        # Dynamic catalog metrics are intentionally not exact snapshot gates.
        "canonical_cards": 99999,
        "cards_without_print_evidence": 123,
        "noisy_rarity_rows": 456,
        "no_hyphen_family_fallback_rows": 789,
    }
    counts.update(overrides)
    return counts


def test_snapshot_gate_accepts_verified_upstream_growth():
    _assert_snapshot_gates(
        _valid_counts(
            source_cards=SOURCE_MINIMUMS["source_cards"] + 39,
            releases=SOURCE_MINIMUMS["releases"] + 4,
        )
    )


def test_snapshot_gate_rejects_truncated_card_source():
    with pytest.raises(AssertionError, match="source_cards"):
        _assert_snapshot_gates(
            _valid_counts(source_cards=SOURCE_MINIMUMS["source_cards"] - 1)
        )


def test_snapshot_gate_rejects_truncated_release_source():
    with pytest.raises(AssertionError, match="releases"):
        _assert_snapshot_gates(
            _valid_counts(releases=SOURCE_MINIMUMS["releases"] - 1)
        )


def test_snapshot_gate_keeps_curated_policy_exact():
    with pytest.raises(AssertionError, match="source_card_aliases_merged"):
        _assert_snapshot_gates(
            _valid_counts(
                source_card_aliases_merged=(
                    EXPECTED_STATIC["source_card_aliases_merged"] + 1
                )
            )
        )


def test_snapshot_gate_keeps_reviewed_exclusion_row_count_exact():
    with pytest.raises(AssertionError, match="excluded_source_print_rows"):
        _assert_snapshot_gates(
            _valid_counts(
                excluded_source_print_rows=(
                    EXPECTED_STATIC["excluded_source_print_rows"] + 1
                )
            )
        )
