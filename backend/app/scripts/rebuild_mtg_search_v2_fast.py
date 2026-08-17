from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.mtg_facets import mtg_facets
from app.search_v2.mtg_profiles import iter_mtg_card_profiles, iter_mtg_print_profiles

EXPECTED_CARDS = 37624
EXPECTED_PRINTS = 161275
EXPECTED_FACETS = 21
EXPECTED_QUICK_FILTERS = {"set", "color_identity", "card_type", "rarity", "finish"}


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


def _build_profiles() -> tuple[list[dict], list[dict], int]:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='mtg' LIMIT 1")).scalar_one())
        card_profiles = list(iter_mtg_card_profiles(session))
        print_profiles = list(iter_mtg_print_profiles(session))
        session.rollback()
    if len(card_profiles) != EXPECTED_CARDS:
        raise AssertionError(f"MTG Card profile source count moved: {len(card_profiles)} != {EXPECTED_CARDS}")
    if len(print_profiles) != EXPECTED_PRINTS:
        raise AssertionError(f"MTG Print profile source count moved: {len(print_profiles)} != {EXPECTED_PRINTS}")
    return card_profiles, print_profiles, game_id


def run(*, report_path: Path | None = None) -> dict:
    card_profiles, print_profiles, game_id = _build_profiles()
    facets = mtg_facets()
    if len(facets) != EXPECTED_FACETS:
        raise AssertionError(f"MTG facet contract moved: {len(facets)} != {EXPECTED_FACETS}")
    quick = {row["key"] for row in facets if bool(row.get("quick_filter", False))}
    if quick != EXPECTED_QUICK_FILTERS:
        raise AssertionError(f"MTG Quick Filter contract moved: {sorted(quick)}")

    raw = db.engine.raw_connection()
    cursor = raw.cursor()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_mtg_search_v2_lean_rebuild",
        "status": "running",
    }
    try:
        cursor.execute("SET LOCAL statement_timeout = '15min'")
        cursor.execute("SET LOCAL lock_timeout = '30s'")
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit:mtg-search-v2-rebuild'))")

        canonical_cards = _scalar(cursor, "SELECT COUNT(*) FROM cards WHERE game_id=%s", (game_id,))
        canonical_prints = _scalar(cursor, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,))
        if canonical_cards != EXPECTED_CARDS or canonical_prints != EXPECTED_PRINTS:
            raise AssertionError(f"Canonical MTG baseline moved cards={canonical_cards} prints={canonical_prints}")

        db_before = _scalar(cursor, "SELECT pg_database_size(current_database())")
        relations_before = {}
        for relation in ("card_search_profiles", "print_search_profiles", "facet_definitions"):
            relations_before[relation] = _scalar(cursor, "SELECT pg_total_relation_size(%s::regclass)", (relation,))

        cursor.execute("DELETE FROM print_search_profiles WHERE game_id=%s", (game_id,))
        deleted_print_profiles = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM card_search_profiles WHERE game_id=%s", (game_id,))
        deleted_card_profiles = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM facet_definitions WHERE game_id=%s", (game_id,))
        deleted_facets = int(cursor.rowcount or 0)

        inserted_cards = _copy(
            cursor,
            "COPY card_search_profiles (card_id, game_id, normalized_name, aliases_json, keywords_json, attributes_json, search_text) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
            ((row["card_id"], game_id, row["normalized_name"], _json(row["aliases_json"]), _json(row["keywords_json"]), _json(row["attributes_json"]), row["search_text"]) for row in card_profiles),
        )
        inserted_prints = _copy(
            cursor,
            "COPY print_search_profiles (print_id, card_id, game_id, normalized_name, normalized_set_code, normalized_collector_number, language, rarity, exact_variant, variant_family, release_names_json, aliases_json, keywords_json, attributes_json, search_text) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
            ((row["print_id"], row["card_id"], game_id, row["normalized_name"], row["normalized_set_code"], row["normalized_collector_number"], row["language"], row["rarity"], row["exact_variant"], row["variant_family"], _json(row["release_names_json"]), _json(row["aliases_json"]), _json(row["keywords_json"]), _json(row["attributes_json"]), row["search_text"]) for row in print_profiles),
        )

        for facet in facets:
            cursor.execute(
                """
                INSERT INTO facet_definitions (
                  game_id,scope,key,label,value_type,ui_type,group_name,source_path,
                  multi_value,filterable,sortable,searchable,quick_filter,display_order,options_json,active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    game_id, facet["scope"], facet["key"], facet["label"], facet["value_type"], facet["ui_type"],
                    facet.get("group_name"), facet["source_path"], bool(facet.get("multi_value", False)),
                    bool(facet.get("filterable", True)), bool(facet.get("sortable", False)),
                    bool(facet.get("searchable", False)), bool(facet.get("quick_filter", False)),
                    int(facet.get("display_order", 0)),
                    _json(facet.get("options_json")) if facet.get("options_json") is not None else "null",
                    bool(facet.get("active", True)),
                ),
            )

        post = {
            "card_search_profiles": _scalar(cursor, "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=%s", (game_id,)),
            "print_search_profiles": _scalar(cursor, "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s", (game_id,)),
            "facet_definitions": _scalar(cursor, "SELECT COUNT(*) FROM facet_definitions WHERE game_id=%s", (game_id,)),
            "missing_cards": _scalar(cursor, "SELECT COUNT(*) FROM cards c WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM card_search_profiles csp WHERE csp.card_id=c.id)", (game_id,)),
            "missing_prints": _scalar(cursor, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM print_search_profiles psp WHERE psp.print_id=p.id)", (game_id,)),
        }
        expected = {"card_search_profiles":EXPECTED_CARDS,"print_search_profiles":EXPECTED_PRINTS,"facet_definitions":EXPECTED_FACETS,"missing_cards":0,"missing_prints":0}
        if post != expected:
            raise AssertionError(f"MTG Search V2 postconditions failed: {post}")

        cursor.execute("SELECT key FROM facet_definitions WHERE game_id=%s AND active=true AND quick_filter=true ORDER BY key", (game_id,))
        quick_after = {str(row[0]) for row in cursor.fetchall()}
        if quick_after != EXPECTED_QUICK_FILTERS:
            raise AssertionError(f"MTG Quick Filters persisted incorrectly: {quick_after}")

        db_precommit = _scalar(cursor, "SELECT pg_database_size(current_database())")
        relations_precommit = {}
        for relation in ("card_search_profiles", "print_search_profiles", "facet_definitions"):
            relations_precommit[relation] = _scalar(cursor, "SELECT pg_total_relation_size(%s::regclass)", (relation,))

        report.update({
            "game_id":game_id,
            "deleted":{"card_profiles":deleted_card_profiles,"print_profiles":deleted_print_profiles,"facets":deleted_facets},
            "inserted":{"card_profiles":inserted_cards,"print_profiles":inserted_prints,"facets":len(facets)},
            "postconditions_precommit":post,
            "quick_filters":sorted(quick_after),
            "database_bytes_before":db_before,
            "database_bytes_precommit":db_precommit,
            "database_delta_bytes_precommit":db_precommit-db_before,
            "relation_sizes_before":relations_before,
            "relation_sizes_precommit":relations_precommit,
        })
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
        if visible != {"card_search_profiles":EXPECTED_CARDS,"print_search_profiles":EXPECTED_PRINTS,"facet_definitions":EXPECTED_FACETS}:
            raise AssertionError(f"MTG Search V2 visibility check failed: {visible}")
        database_after = _scalar(cur2, "SELECT pg_database_size(current_database())")
        relations_after = {}
        for relation in ("card_search_profiles", "print_search_profiles", "facet_definitions"):
            relations_after[relation] = _scalar(cur2, "SELECT pg_total_relation_size(%s::regclass)", (relation,))
        cur2.execute("ROLLBACK")
    finally:
        cur2.close()
        raw2.close()

    report["visible_after_commit"] = visible
    report["database_bytes_after_commit"] = database_after
    report["database_delta_bytes_after_commit"] = database_after - report["database_bytes_before"]
    report["relation_sizes_after_commit"] = relations_after
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
