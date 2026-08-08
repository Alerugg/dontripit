from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_query import normal_yugioh_search


MAX_NORMAL_MS = 1500.0
MAX_ADVANCED_MS = 1800.0
EXPECTED_CARDS = 14479
EXPECTED_PRINTS = 44226
EXPECTED_FACETS = 20
EXPECTED_ACTIVE_FACETS = 19


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _timed(label: str, fn, *, ceiling_ms: float, checks: list[dict]):
    started = time.perf_counter()
    result = fn()
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    checks.append({"label": label, "elapsed_ms": elapsed_ms, "ceiling_ms": ceiling_ms})
    if elapsed_ms > ceiling_ms:
        raise AssertionError(f"{label} latency {elapsed_ms}ms exceeds {ceiling_ms}ms")
    return result


def _assert_card_result(items: list[dict], expected_name: str, label: str) -> dict:
    if not items:
        raise AssertionError(f"{label} returned no results")
    expected = expected_name.casefold()
    for row in items[:10]:
        if str(row.get("name") or "").casefold() == expected:
            return row
    names = [row.get("name") for row in items[:10]]
    raise AssertionError(f"{label} did not rank {expected_name!r} in top 10: {names}")


def run(*, report_path: Path | None = None) -> dict:
    db.init_engine()
    checks: list[dict] = []
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game": "yugioh",
        "mode": "strict_search_v2_certification",
        "status": "running",
    }

    try:
        with db.SessionLocal() as session:
            game_id = int(session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one())
            counts = dict(session.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM cards WHERE game_id=:game_id) AS cards,
                  (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS prints,
                  (SELECT COUNT(*) FROM card_search_profiles WHERE game_id=:game_id) AS card_profiles,
                  (SELECT COUNT(*) FROM print_search_profiles WHERE game_id=:game_id) AS print_profiles,
                  (SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game_id) AS facets,
                  (SELECT COUNT(*) FROM facet_definitions WHERE game_id=:game_id AND active=true) AS active_facets
                """
            ), {"game_id": game_id}).mappings().one())
            counts = {key: int(value or 0) for key, value in counts.items()}
            expected = {
                "cards": EXPECTED_CARDS,
                "prints": EXPECTED_PRINTS,
                "card_profiles": EXPECTED_CARDS,
                "print_profiles": EXPECTED_PRINTS,
                "facets": EXPECTED_FACETS,
                "active_facets": EXPECTED_ACTIVE_FACETS,
            }
            if counts != expected:
                raise AssertionError(f"YGO Search V2 counts moved: {counts} != {expected}")

            dark_magician = _timed(
                "normal: Dark Magician",
                lambda: normal_yugioh_search(session, query="Dark Magician", limit=24),
                ceiling_ms=MAX_NORMAL_MS,
                checks=checks,
            )
            dm = _assert_card_result(dark_magician, "Dark Magician", "Dark Magician")
            if str((dm.get("attributes") or {}).get("attribute") or "").upper() != "DARK":
                raise AssertionError("Dark Magician result is missing DARK attribute evidence")

            blue_eyes = _timed(
                "normal: Blue-Eyes White Dragon",
                lambda: normal_yugioh_search(session, query="Blue-Eyes White Dragon", limit=24),
                ceiling_ms=MAX_NORMAL_MS,
                checks=checks,
            )
            bewd = _assert_card_result(blue_eyes, "Blue-Eyes White Dragon", "Blue-Eyes White Dragon")
            if int((bewd.get("attributes") or {}).get("atk") or 0) != 3000:
                raise AssertionError("Blue-Eyes result is missing ATK 3000 evidence")

            exact_seed = session.execute(text(
                """
                SELECT c.name, p.collector_number, p.rarity, s.code, s.name,
                       csp.attributes_json AS card_attributes,
                       psp.release_names_json
                FROM print_search_profiles psp
                JOIN prints p ON p.id=psp.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                JOIN card_search_profiles csp ON csp.card_id=c.id
                WHERE psp.game_id=:game_id
                  AND p.collector_number IS NOT NULL
                  AND btrim(p.collector_number) <> ''
                  AND p.rarity IS NOT NULL
                  AND btrim(p.rarity) <> ''
                ORDER BY p.id ASC
                LIMIT 1
                """
            ), {"game_id": game_id}).mappings().one()
            collector = str(exact_seed["collector_number"])
            exact_code_results = _timed(
                f"normal: exact collector {collector}",
                lambda: normal_yugioh_search(session, query=collector, limit=24),
                ceiling_ms=MAX_NORMAL_MS,
                checks=checks,
            )
            if not exact_code_results:
                raise AssertionError(f"Exact collector search {collector} returned no results")
            matched_collectors = {
                str((row.get("matched_print") or {}).get("collector_number") or "").casefold()
                for row in exact_code_results[:10]
            }
            if collector.casefold() not in matched_collectors:
                raise AssertionError(f"Exact collector {collector} was not resolved in top results")

            monster_dark = _timed(
                "advanced: Monster + DARK",
                lambda: advanced_yugioh_search(
                    session,
                    filters={"card_class": "Monster", "attribute": "DARK"},
                    limit=25,
                ),
                ceiling_ms=MAX_ADVANCED_MS,
                checks=checks,
            )
            if not monster_dark["items"]:
                raise AssertionError("Monster + DARK advanced search returned no Prints")
            for item in monster_dark["items"][:10]:
                attrs = item.get("attributes") or {}
                if str(attrs.get("card_class") or "").casefold() != "monster":
                    raise AssertionError("Monster filter returned a non-Monster Print")
                if str(attrs.get("attribute") or "").upper() != "DARK":
                    raise AssertionError("DARK filter returned a non-DARK Print")

            high_atk = _timed(
                "advanced: ATK >= 3000",
                lambda: advanced_yugioh_search(session, filters={"atk": {"min": 3000}}, limit=25),
                ceiling_ms=MAX_ADVANCED_MS,
                checks=checks,
            )
            if not high_atk["items"]:
                raise AssertionError("ATK >= 3000 returned no Prints")
            if any(int((item.get("attributes") or {}).get("atk") or -1) < 3000 for item in high_atk["items"]):
                raise AssertionError("ATK >= 3000 returned a Print below threshold")

            rarity_value = str(exact_seed["rarity"])
            rarity_results = _timed(
                f"advanced: rarity {rarity_value}",
                lambda: advanced_yugioh_search(session, filters={"rarity": rarity_value}, limit=25),
                ceiling_ms=MAX_ADVANCED_MS,
                checks=checks,
            )
            if not rarity_results["items"]:
                raise AssertionError(f"Rarity filter {rarity_value} returned no Prints")
            if any(str(item.get("rarity") or "").casefold() != rarity_value.casefold() for item in rarity_results["items"]):
                raise AssertionError(f"Rarity filter {rarity_value} returned mismatched rarity")

            collector_results = _timed(
                f"advanced: collector {collector}",
                lambda: advanced_yugioh_search(session, filters={"collector_number": collector}, limit=25),
                ceiling_ms=MAX_ADVANCED_MS,
                checks=checks,
            )
            if not collector_results["items"]:
                raise AssertionError(f"Collector filter {collector} returned no Prints")
            if any(str(item.get("collector_number") or "").casefold() != collector.casefold() for item in collector_results["items"]):
                raise AssertionError("Collector filter returned a different printing code")

            release_names = list(exact_seed["release_names_json"] or [])
            if release_names:
                release = str(release_names[0])
                release_results = _timed(
                    f"advanced: release {release}",
                    lambda: advanced_yugioh_search(session, filters={"release": release}, limit=25),
                    ceiling_ms=MAX_ADVANCED_MS,
                    checks=checks,
                )
                if not release_results["items"]:
                    raise AssertionError(f"Release filter {release!r} returned no Prints")
                if any(release.casefold() not in {str(v).casefold() for v in (item.get("attributes") or {}).get("release_names", [])} for item in release_results["items"]):
                    raise AssertionError("Release filter returned a Print outside the requested release")

            try:
                advanced_yugioh_search(session, filters={"finish": "holo"}, limit=5)
            except ValueError as exc:
                if "Unsupported Yu-Gi-Oh filters" not in str(exc):
                    raise
            else:
                raise AssertionError("Unsupported finish filter was accepted; source does not certify finish")

            session.rollback()

        max_ms = max((row["elapsed_ms"] for row in checks), default=0.0)
        report.update({
            "status": "pass",
            "counts": counts,
            "checks": checks,
            "max_elapsed_ms": max_ms,
            "latency_limits_ms": {"normal": MAX_NORMAL_MS, "advanced": MAX_ADVANCED_MS},
            "evidence": {
                "dark_magician_top_result": dm.get("name"),
                "blue_eyes_top_result": bewd.get("name"),
                "exact_collector_probe": collector,
                "rarity_probe": rarity_value,
                "invalid_finish_filter_rejected": True,
            },
        })
    except Exception as exc:
        report["status"] = "fail"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["checks"] = checks
        _write(report_path, report)
        raise

    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
