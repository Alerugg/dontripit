from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from app import db
from app.scripts.benchmark_search_v2 import run as run_benchmark


def _scalar(session, sql: str, params: dict | None = None) -> int:
    return int(session.execute(text(sql), params or {}).scalar_one())


def run() -> dict:
    benchmark = run_benchmark()
    db.init_engine()

    with db.SessionLocal() as session:
        game = session.execute(text(
            "SELECT id, slug, name FROM games WHERE slug='onepiece' LIMIT 1"
        )).mappings().first()
        if not game:
            raise AssertionError("One Piece game row not found")
        game_id = int(game["id"])

        cards = _scalar(session, "SELECT COUNT(*) FROM cards WHERE game_id=:game", {"game": game_id})
        prints = _scalar(
            session,
            "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game",
            {"game": game_id},
        )
        card_profiles = _scalar(session, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game", {"game": game_id})
        print_profiles = _scalar(session, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game", {"game": game_id})
        active_facets = _scalar(session, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game AND active=TRUE", {"game": game_id})
        missing_print_images = _scalar(
            session,
            """
            SELECT COUNT(*)
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=:game
              AND NOT EXISTS (SELECT 1 FROM print_images pi WHERE pi.print_id=p.id)
            """,
            {"game": game_id},
        )
        duplicate_print_keys = _scalar(
            session,
            """
            SELECT COUNT(*) FROM (
              SELECT p.print_key
              FROM prints p
              JOIN cards c ON c.id=p.card_id
              WHERE c.game_id=:game
              GROUP BY p.print_key
              HAVING COUNT(*) > 1
            ) duplicates
            """,
            {"game": game_id},
        )

    expected = {
        "cards": 2665,
        "prints": 4672,
        "card_profiles": 2665,
        "print_profiles": 4672,
        "min_active_facets": 19,
    }
    actual = {
        "cards": cards,
        "prints": prints,
        "card_profiles": card_profiles,
        "print_profiles": print_profiles,
        "active_facets": active_facets,
        "missing_print_images": missing_print_images,
        "duplicate_print_keys": duplicate_print_keys,
    }

    failures = []
    for key in ("cards", "prints", "card_profiles", "print_profiles"):
        if actual[key] != expected[key]:
            failures.append(f"{key}: expected {expected[key]}, got {actual[key]}")
    if active_facets < expected["min_active_facets"]:
        failures.append(f"active_facets: expected >= {expected['min_active_facets']}, got {active_facets}")
    if duplicate_print_keys:
        failures.append(f"duplicate_print_keys: expected 0, got {duplicate_print_keys}")
    if benchmark.get("status") != "pass":
        failures.append("benchmark did not pass")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game": "onepiece",
        "database": "neon",
        "expected": expected,
        "actual": actual,
        "benchmark": {
            "case_count": benchmark.get("case_count"),
            "hard_gate": benchmark.get("hard_gate"),
            "soft_quality": benchmark.get("soft_quality"),
            "latency_ms": benchmark.get("latency_ms"),
        },
        "status": "pass" if not failures else "fail",
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if failures:
        raise AssertionError("; ".join(failures))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
