from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.jobs.cardmarket_daily_sync import DailySyncPolicy, validate_daily_plan
from app.jobs.cardmarket_prices import ImportPlan


def _plan(**overrides):
    now = datetime(2026, 8, 10, 3, 30, tzinfo=timezone.utc)
    values = dict(
        as_of=now - timedelta(hours=3),
        total_rows=77000,
        unique_products=77000,
        mapped_exact=6000,
        unmapped=71000,
        ambiguous=0,
        cross_game_mappings=0,
        duplicate_feed_rows=0,
        missing_finish_prices=20,
        game_slug="pokemon",
        snapshots=tuple({"entity_id": index} for index in range(6000)),
    )
    values.update(overrides)
    return ImportPlan(**values), now


def test_safe_current_pokemon_plan_has_no_blockers():
    plan, now = _plan()
    assert validate_daily_plan(plan, now=now) == []


def test_stale_or_future_feed_is_blocked():
    plan, now = _plan(as_of=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))
    blockers = validate_daily_plan(plan, now=now)
    assert any(item.startswith("stale_feed_age_hours=") for item in blockers)

    future, now = _plan(as_of=now + timedelta(hours=2))
    blockers = validate_daily_plan(future, now=now)
    assert any(item.startswith("feed_from_future=") for item in blockers)


def test_identity_and_feed_integrity_issues_are_hard_blockers():
    plan, now = _plan(ambiguous=1, cross_game_mappings=2, duplicate_feed_rows=3)
    blockers = validate_daily_plan(plan, now=now)
    assert "ambiguous=1" in blockers
    assert "cross_game_mappings=2" in blockers
    assert "duplicate_feed_rows=3" in blockers


def test_snapshot_range_protects_against_feed_or_mapping_collapse():
    low, now = _plan(mapped_exact=4999, snapshots=tuple({"entity_id": index} for index in range(4999)))
    assert "snapshot_count_below_min=4999" in validate_daily_plan(low, now=now)

    policy = DailySyncPolicy(max_snapshots=7000)
    high, now = _plan(mapped_exact=7001, snapshots=tuple({"entity_id": index} for index in range(7001)))
    assert "snapshot_count_above_max=7001" in validate_daily_plan(high, now=now, policy=policy)


def test_wrong_game_is_blocked_even_if_every_other_metric_is_clean():
    plan, now = _plan(game_slug="mtg")
    blockers = validate_daily_plan(plan, now=now)
    assert "unexpected_game='mtg'" in blockers
