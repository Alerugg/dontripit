from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.jobs.cardmarket_prices import ImportPlan


@dataclass(frozen=True)
class DailySyncPolicy:
    game_slug: str = "pokemon"
    max_feed_age_hours: int = 36
    max_future_minutes: int = 60
    min_snapshots: int = 5000
    max_snapshots: int = 10000


def validate_daily_plan(
    plan: ImportPlan,
    *,
    now: datetime | None = None,
    policy: DailySyncPolicy | None = None,
) -> list[str]:
    """Return blockers for a Cardmarket daily sync plan.

    The function is deliberately strict about provenance and identity. Missing
    finish prices are allowed because those rows are already excluded from the
    plan; ambiguous, cross-game or duplicate feed identities are hard blockers.
    """
    policy = policy or DailySyncPolicy()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    blockers: list[str] = []
    if plan.game_slug != policy.game_slug:
        blockers.append(f"unexpected_game={plan.game_slug!r}")

    as_of = plan.as_of.astimezone(timezone.utc)
    if as_of > now + timedelta(minutes=policy.max_future_minutes):
        blockers.append(f"feed_from_future={as_of.isoformat()}")
    age = now - as_of
    if age > timedelta(hours=policy.max_feed_age_hours):
        blockers.append(f"stale_feed_age_hours={age.total_seconds() / 3600:.2f}")

    if plan.ambiguous:
        blockers.append(f"ambiguous={plan.ambiguous}")
    if plan.cross_game_mappings:
        blockers.append(f"cross_game_mappings={plan.cross_game_mappings}")
    if plan.duplicate_feed_rows:
        blockers.append(f"duplicate_feed_rows={plan.duplicate_feed_rows}")

    snapshot_count = len(plan.snapshots)
    if snapshot_count < policy.min_snapshots:
        blockers.append(f"snapshot_count_below_min={snapshot_count}")
    if snapshot_count > policy.max_snapshots:
        blockers.append(f"snapshot_count_above_max={snapshot_count}")

    return blockers
