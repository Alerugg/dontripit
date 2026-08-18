from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.yugioh_facets import yugioh_facets
from app.search_v2.yugioh_profiles import iter_yugioh_card_profiles, iter_yugioh_print_profiles


EXPECTED_CARDS = 14479
EXPECTED_PRINTS_BY_LANGUAGE = {"en": 44226, "es": 38249, "ja": 36426}
EXPECTED_PRINTS = sum(EXPECTED_PRINTS_BY_LANGUAGE.values())
EXPECTED_FACETS = 20
EXPECTED_ACTIVE_FACETS = 19
EXPECTED_QUICK_FILTERS = {"set", "release", "card_class", "attribute", "archetype", "rarity"}
MAX_REBUILD_GROWTH_BYTES = 192 * 1024 * 1024


def _null(value):
    return r"\N" if value is None else value


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _copy(cursor, sql: str, rows) -> int:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    count = 0
    for row in rows:
        writer.writerow([_null(value) for value in row])
        count += 1
    buffer.seek(0)
    cursor.copy_expert(sql, buffer)
    return count


def _scalar(cursor, sql: str, params=()) -> int:
    cursor.execute(sql, params)
    return int(cursor.fetchone()[0] or 0)


def _language_counts(cursor, table: str, game_id: int) -> dict[str, int]:
    if table == "prints":
        cursor.execute(
            """
            SELECT lower(coalesce(p.language,'')), COUNT(*)
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es','ja')
            GROUP BY 1 ORDER BY 1
            """,
            (game_id,),
        )
    elif table == "print_search_profiles":
        cursor.execute(
            """
            SELECT lower(coalesce(language,'')), COUNT(*)
            FROM print_search_profiles
            WHERE game_id=%s AND lower(coalesce(language,'')) IN ('en','es','ja')
            GROUP BY 1 ORDER BY 1
            """,
            (game_id,),
        )
    else:
        raise ValueError(table)
    out = {key: 0 for key in EXPECTED_PRINTS_BY_LANGUAGE}
    for language, count in cursor.fetchall():
        if language in out:
            out[str(language)] = int(count or 0)
    return out


def _other_game_index_counts(cursor, game_id: int) -> dict[str, dict[str, int]]:
    cursor.execute(
        """
        SELECT g.slug,
               (SELECT COUNT(*) FROM card_search_profiles csp WHERE csp.game_id=g.id),
               (SELECT COUNT(*) FROM print_search_profiles psp WHERE psp.game_id=g.id),
               (SELECT COUNT(*) FROM facet_definitions fd WHERE fd.game_id=g.id)
        FROM games g WHERE g.id<>%s ORDER BY g.slug
        """,
        (game_id,),
    )
    return {
        str(slug): {"cards": int(cards), "prints": int(prints), "facets": int(facets)}
        for slug, cards, prints, facets in cursor.fetchall()
    }


def _build_profiles() -> tuple[list[dict], list[dict], int]:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one())
        card_profiles = list(iter_yugioh_card_profiles(session))
        print_profiles = list(iter_yugioh_print_profiles(session))
        session.rollback()

    if len(card_profiles) != EXPECTED_CARDS:
        raise AssertionError(f"YGO Card profile source count moved: {len(card_profiles)} != {EXPECTED_CARDS}")
    if len(print_profiles) != EXPECTED_PRINTS:
        raise AssertionError(f"YGO Print profile source count moved: {len(print_profiles)} != {EXPECTED_PRINTS}")

    ids = [int(row["print_id"]) for row in print_profiles]
    if len(ids) != len(set(ids)):
        duplicates = [value for value, count in Counter(ids).items() if count > 1][:20]
        raise AssertionError(f"YGO profile generator duplicated physical print_ids: {duplicates}")

    languages = Counter(str(row.get("language") or "").lower() for row in print_profiles)
    actual_languages = {key: int(languages.get(key, 0)) for key in EXPECTED_PRINTS_BY_LANGUAGE}
    if actual_languages != EXPECTED_PRINTS_BY_LANGUAGE:
        raise AssertionError(
            f"YGO generated profile language counts moved: {actual_languages} != {EXPECTED_PRINTS_BY_LANGUAGE}"
        )
    return card_profiles, print_profiles, game_id


def run(*, report_path: Path | None = None) -> dict:
    card_profiles, print_profiles, game_id = _build_profiles()
    facets = yugioh_facets()
    if len(facets) != EXPECTED_FACETS:
        raise AssertionError(f"YGO facet contract moved: {len(facets)}")
    active = [row for row in facets if bool(row.get("active", True))]
    if len(active) != EXPECTED_ACTIVE_FACETS:
        raise AssertionError(f"YGO active facet count moved: {len(active)}")
    quick = {row["key"] for row in active if bool(row.get("quick_filter", False))}
    if quick != EXPECTED_QUICK_FILTERS:
        raise AssertionError(f"YGO Quick Filter contract moved: {sorted(quick)}")

    raw = db.engine.raw_connection()
    cursor = raw.cursor()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_yugioh_search_v2_multilingual_exact_print_rebuild",
        "status": "running",
        "expected_prints_by_language": EXPECTED_PRINTS_BY_LANGUAGE,
    }
    try:
        cursor.execute("SET LOCAL statement_timeout = '20min'")
        cursor.execute("SET LOCAL lock_timeout = '30s'")
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit:yugioh-search-v2-rebuild'))")

        database_bytes_before = _scalar(cursor, "SELECT pg_database_size(current_database())")
        other_games_before = _other_game_index_counts(cursor, game_id)
        canonical_cards = _scalar(cursor, "SELECT COUNT(*) FROM cards WHERE game_id=%s", (game_id,))
        canonical_prints = _scalar(
            cursor,
            "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
            (game_id,),
        )
        canonical_languages = _language_counts(cursor, "prints", game_id)
        if canonical_cards != EXPECTED_CARDS or canonical_prints != EXPECTED_PRINTS:
            raise AssertionError(
                f"Canonical YGO baseline moved cards={canonical_cards} prints={canonical_prints}; expected {EXPECTED_CARDS}/{EXPECTED_PRINTS}"
            )
        if canonical_languages != EXPECTED_PRINTS_BY_LANGUAGE:
            raise AssertionError(
                f"Canonical YGO physical language counts moved: {canonical_languages} != {EXPECTED_PRINTS_BY_LANGUAGE}"
            )

        cursor.execute("DELETE FROM print_search_profiles WHERE game_id=%s", (game_id,))
        deleted_print_profiles = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM card_search_profiles WHERE game_id=%s", (game_id,))
        deleted_card_profiles = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM facet_definitions WHERE game_id=%s", (game_id,))
        deleted_facets = int(cursor.rowcount or 0)

        inserted_cards = _copy(
            cursor,
            "COPY card_search_profiles (card_id, game_id, normalized_name, aliases_json, keywords_json, attributes_json, search_text) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
            (
                (
                    row["card_id"], game_id, row["normalized_name"], _json(row["aliases_json"]),
                    _json(row["keywords_json"]), _json(row["attributes_json"]), row["search_text"],
                )
                for row in card_profiles
            ),
        )
        inserted_prints = _copy(
            cursor,
            "COPY print_search_profiles (print_id, card_id, game_id, normalized_name, normalized_set_code, normalized_collector_number, language, rarity, exact_variant, variant_family, release_names_json, aliases_json, keywords_json, attributes_json, search_text) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
            (
                (
                    row["print_id"], row["card_id"], game_id, row["normalized_name"],
                    row["normalized_set_code"], row["normalized_collector_number"], row["language"],
                    row["rarity"], row["exact_variant"], row["variant_family"],
                    _json(row["release_names_json"]), _json(row["aliases_json"]),
                    _json(row["keywords_json"]), _json(row["attributes_json"]), row["search_text"],
                )
                for row in print_profiles
            ),
        )

        for facet in facets:
            cursor.execute(
                """
                INSERT INTO facet_definitions (
                  game_id, scope, key, label, value_type, ui_type, group_name,
                  source_path, multi_value, filterable, sortable, searchable,
                  quick_filter, display_order, options_json, active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    game_id, facet["scope"], facet["key"], facet["label"], facet["value_type"],
                    facet["ui_type"], facet.get("group_name"), facet["source_path"],
                    bool(facet.get("multi_value", False)), bool(facet.get("filterable", True)),
                    bool(facet.get("sortable", False)), bool(facet.get("searchable", False)),
                    bool(facet.get("quick_filter", False)), int(facet.get("display_order", 0)),
                    _json(facet.get("options_json")) if facet.get("options_json") is not None else "null",
                    bool(facet.get("active", True)),
                ),
            )

        post = {
            "card_search_profiles": _scalar(cursor, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=%s", (game_id,)),
            "print_search_profiles": _scalar(cursor, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s", (game_id,)),
            "facet_definitions": _scalar(cursor, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=%s", (game_id,)),
            "active_facets": _scalar(cursor, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=%s AND active=true", (game_id,)),
            "profiles_missing_card": _scalar(
                cursor,
                "SELECT COUNT(*) FROM cards c WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM card_search_profiles csp WHERE csp.card_id=c.id)",
                (game_id,),
            ),
            "profiles_missing_print": _scalar(
                cursor,
                """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM print_search_profiles psp WHERE psp.print_id=p.id)
                """,
                (game_id,),
            ),
            "duplicate_print_profiles": _scalar(
                cursor,
                "SELECT COUNT(*) FROM (SELECT print_id FROM print_search_profiles WHERE game_id=%s GROUP BY print_id HAVING COUNT(*)>1) d",
                (game_id,),
            ),
        }
        expected_post = {
            "card_search_profiles": EXPECTED_CARDS,
            "print_search_profiles": EXPECTED_PRINTS,
            "facet_definitions": EXPECTED_FACETS,
            "active_facets": EXPECTED_ACTIVE_FACETS,
            "profiles_missing_card": 0,
            "profiles_missing_print": 0,
            "duplicate_print_profiles": 0,
        }
        if post != expected_post:
            raise AssertionError(f"YGO Search V2 postconditions failed: {post}")

        profile_languages = _language_counts(cursor, "print_search_profiles", game_id)
        if profile_languages != EXPECTED_PRINTS_BY_LANGUAGE:
            raise AssertionError(
                f"YGO Search V2 language projection failed: {profile_languages} != {EXPECTED_PRINTS_BY_LANGUAGE}"
            )

        cursor.execute(
            "SELECT key FROM facet_definitions WHERE game_id=%s AND active=true AND quick_filter=true ORDER BY key",
            (game_id,),
        )
        quick_after = {str(row[0]) for row in cursor.fetchall()}
        if quick_after != EXPECTED_QUICK_FILTERS:
            raise AssertionError(f"YGO Quick Filters persisted incorrectly: {quick_after}")

        other_games_after = _other_game_index_counts(cursor, game_id)
        if other_games_after != other_games_before:
            raise AssertionError("Non-Yu-Gi-Oh Search V2 rows changed during isolated rebuild")

        database_bytes_precommit = _scalar(cursor, "SELECT pg_database_size(current_database())")
        database_growth = database_bytes_precommit - database_bytes_before
        if database_growth > MAX_REBUILD_GROWTH_BYTES:
            raise AssertionError(
                f"YGO Search V2 rebuild grew database by {database_growth / 1024 / 1024:.2f} MiB; budget is {MAX_REBUILD_GROWTH_BYTES / 1024 / 1024:.0f} MiB"
            )

        report.update(
            {
                "game_id": game_id,
                "deleted": {"card_profiles": deleted_card_profiles, "print_profiles": deleted_print_profiles, "facets": deleted_facets},
                "inserted": {"card_profiles": inserted_cards, "print_profiles": inserted_prints, "facets": len(facets)},
                "postconditions_precommit": post,
                "profile_language_counts": profile_languages,
                "quick_filters": sorted(quick_after),
                "other_games_unchanged": True,
                "database_mib_before": round(database_bytes_before / 1024 / 1024, 2),
                "database_mib_precommit": round(database_bytes_precommit / 1024 / 1024, 2),
                "database_growth_mib": round(database_growth / 1024 / 1024, 2),
            }
        )
        raw.commit()
        report["status"] = "committed"
        report["committed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        raw.rollback()
        report["status"] = "rolled_back"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        _write(report_path, report)
        raise
    finally:
        cursor.close()
        raw.close()

    raw2 = db.engine.raw_connection()
    cur2 = raw2.cursor()
    try:
        visible = {
            "card_search_profiles": _scalar(cur2, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=%s", (game_id,)),
            "print_search_profiles": _scalar(cur2, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s", (game_id,)),
            "facet_definitions": _scalar(cur2, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=%s", (game_id,)),
        }
        expected_visible = {
            "card_search_profiles": EXPECTED_CARDS,
            "print_search_profiles": EXPECTED_PRINTS,
            "facet_definitions": EXPECTED_FACETS,
        }
        if visible != expected_visible:
            raise AssertionError(f"YGO Search V2 visibility check failed: {visible}")
        visible_languages = _language_counts(cur2, "print_search_profiles", game_id)
        if visible_languages != EXPECTED_PRINTS_BY_LANGUAGE:
            raise AssertionError(f"YGO Search V2 committed languages failed: {visible_languages}")
        report["visible_after_commit"] = visible
        report["visible_languages_after_commit"] = visible_languages
        report["database_mib_after_commit"] = round(_scalar(cur2, "SELECT pg_database_size(current_database())") / 1024 / 1024, 2)
        cur2.execute("ROLLBACK")
    finally:
        cur2.close()
        raw2.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
