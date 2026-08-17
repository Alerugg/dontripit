from __future__ import annotations

import json
from datetime import datetime, timezone

from app import db
from app.ingest.registry import get_connector
from app.scripts.catalog_health import get_catalog_health


def _game_snapshot(health: dict, slug: str) -> dict | None:
    return next((row for row in health.get("games", []) if row.get("slug") == slug), None)


def run_bootstrap() -> dict:
    started_at = datetime.now(timezone.utc)
    db.init_engine()

    with db.SessionLocal() as session:
        before_health = get_catalog_health(session, sample_limit=10, runs_limit=5)
        before = _game_snapshot(before_health, "onepiece")

        connector = get_connector("onepiece")
        try:
            stats = connector.run(
                session,
                None,
                fixture=False,
                incremental=False,
                limit=None,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

        after_health = get_catalog_health(session, sample_limit=20, runs_limit=10)
        after = _game_snapshot(after_health, "onepiece")

    return {
        "source": "onepiece",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "ingest_stats": {
            "files_seen": stats.files_seen,
            "files_skipped": stats.files_skipped,
            "inserted": stats.records_inserted,
            "updated": stats.records_updated,
            "errors": stats.errors,
        },
        "after": after,
        "catalog_totals_after": after_health.get("totals"),
    }


def main() -> int:
    payload = run_bootstrap()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    after = payload.get("after") or {}
    counts = after.get("counts") or {}
    if int(counts.get("cards") or 0) <= 0 or int(counts.get("prints") or 0) <= 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
