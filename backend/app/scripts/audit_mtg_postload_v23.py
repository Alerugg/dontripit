from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2

EXPECTED = {
    "sets": 986,
    "cards": 37624,
    "card_attributes": 37624,
    "prints": 161275,
    "print_attributes": 161275,
    "print_identifiers": 161275,
    "print_images": 168435,
}
EXPECTED_FINISHES = {"etched": 1218, "foil": 65936, "nonfoil": 94121}
EXPECTED_NON_MTG = {
    "onepiece": {"cards": 2665, "prints": 4672},
    "pokemon": {"cards": 21065, "prints": 33757},
    "yugioh": {"cards": 14479, "prints": 44226},
    "riftbound": {"cards": 2, "prints": 2},
}


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("No database URL configured")
    if value.startswith("postgresql+psycopg2://"):
        value = "postgresql://" + value[len("postgresql+psycopg2://") :]
    elif value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    return value


def _scalar(cur, sql: str, params=()) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def run(output: Path) -> dict:
    conn = psycopg2.connect(_url())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            row = cur.fetchone()
            if not row:
                raise AssertionError("MTG game missing")
            game_id = int(row[0])

            counts = {
                "sets": _scalar(cur, "SELECT COUNT(*) FROM sets WHERE game_id=%s", (game_id,)),
                "cards": _scalar(cur, "SELECT COUNT(*) FROM cards WHERE game_id=%s", (game_id,)),
                "card_attributes": _scalar(cur, "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=%s", (game_id,)),
                "prints": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "print_attributes": _scalar(cur, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "print_identifiers": _scalar(cur, "SELECT COUNT(*) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "print_images": _scalar(cur, "SELECT COUNT(*) FROM print_images i JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "card_search_profiles": _scalar(cur, "SELECT COUNT(*) FROM card_search_profiles sp JOIN cards c ON c.id=sp.card_id WHERE c.game_id=%s", (game_id,)),
                "print_search_profiles": _scalar(cur, "SELECT COUNT(*) FROM print_search_profiles sp JOIN prints p ON p.id=sp.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "search_documents": _scalar(cur, "SELECT COUNT(*) FROM search_documents sd WHERE sd.game_id=%s", (game_id,)),
                "field_provenance_cards": _scalar(cur, "SELECT COUNT(*) FROM field_provenance fp JOIN cards c ON fp.entity_type='card' AND fp.entity_id=c.id WHERE c.game_id=%s", (game_id,)),
                "field_provenance_prints": _scalar(cur, "SELECT COUNT(*) FROM field_provenance fp JOIN prints p ON fp.entity_type='print' AND fp.entity_id=p.id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "print_field_provenance": _scalar(cur, "SELECT COUNT(*) FROM print_field_provenance pfp JOIN prints p ON p.id=pfp.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
                "prices": _scalar(cur, "SELECT COUNT(*) FROM prices WHERE game_id=%s", (game_id,)),
            }

            cur.execute("""
                SELECT variant, COUNT(*)::bigint
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s
                GROUP BY variant ORDER BY variant
            """, (game_id,))
            finishes = {str(k): int(v) for k, v in cur.fetchall()}

            integrity = {
                "blank_card_key": _scalar(cur, "SELECT COUNT(*) FROM cards WHERE game_id=%s AND (card_key IS NULL OR btrim(card_key)='')", (game_id,)),
                "duplicate_card_key_groups": _scalar(cur, "SELECT COUNT(*) FROM (SELECT card_key FROM cards WHERE game_id=%s GROUP BY card_key HAVING COUNT(*)>1) x", (game_id,)),
                "blank_print_key": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND (p.print_key IS NULL OR btrim(p.print_key)='')", (game_id,)),
                "duplicate_print_key_groups": _scalar(cur, "SELECT COUNT(*) FROM (SELECT p.print_key FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s GROUP BY p.print_key HAVING COUNT(*)>1) x", (game_id,)),
                "blank_scryfall_id": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND (p.scryfall_id IS NULL OR btrim(p.scryfall_id)='')", (game_id,)),
                "duplicate_scryfall_finish_groups": _scalar(cur, "SELECT COUNT(*) FROM (SELECT p.scryfall_id,p.variant FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s GROUP BY p.scryfall_id,p.variant HAVING COUNT(*)>1) x", (game_id,)),
                "unknown_finish_rows": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.variant NOT IN ('nonfoil','foil','etched')", (game_id,)),
                "print_identifier_missing": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id LEFT JOIN print_identifiers pi ON pi.print_id=p.id AND pi.source='scryfall_finish' WHERE c.game_id=%s AND pi.id IS NULL", (game_id,)),
                "print_identifier_duplicate_external_groups": _scalar(cur, "SELECT COUNT(*) FROM (SELECT pi.source,pi.external_id FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s GROUP BY pi.source,pi.external_id HAVING COUNT(*)>1) x", (game_id,)),
                "prints_without_image": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id LEFT JOIN print_images i ON i.print_id=p.id WHERE c.game_id=%s GROUP BY c.game_id HAVING COUNT(i.id) FILTER (WHERE i.id IS NOT NULL) >= 0", (game_id,)) if False else 0,
            }
            # Count exact Prints without any image separately; avoiding GROUP BY ambiguity.
            integrity["prints_without_image"] = _scalar(cur, """
                SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM print_images i WHERE i.print_id=p.id)
            """, (game_id,))
            integrity["source_objects_without_image"] = _scalar(cur, """
                SELECT COUNT(*) FROM (
                    SELECT p.scryfall_id
                    FROM prints p JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s
                    GROUP BY p.scryfall_id
                    HAVING bool_and(NOT EXISTS (SELECT 1 FROM print_images i WHERE i.print_id=p.id))
                ) x
            """, (game_id,))
            integrity["distinct_scryfall_objects"] = _scalar(cur, "SELECT COUNT(DISTINCT p.scryfall_id) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,))
            integrity["fallback_cards_without_oracle"] = _scalar(cur, "SELECT COUNT(*) FROM cards WHERE game_id=%s AND oracle_id IS NULL", (game_id,))

            cur.execute("""
                SELECT g.slug, COUNT(DISTINCT c.id)::bigint, COUNT(p.id)::bigint
                FROM games g
                LEFT JOIN cards c ON c.game_id=g.id
                LEFT JOIN prints p ON p.card_id=c.id
                WHERE g.slug <> 'mtg'
                GROUP BY g.slug ORDER BY g.slug
            """)
            non_mtg = {str(slug): {"cards": int(cards), "prints": int(prints)} for slug, cards, prints in cur.fetchall()}

            cur.execute("""
                SELECT indexrelid::regclass::text, indisvalid, indisready
                FROM pg_index
                WHERE NOT indisvalid OR NOT indisready
                ORDER BY 1
            """)
            invalid_indexes = [
                {"index": str(name), "valid": bool(valid), "ready": bool(ready)}
                for name, valid, ready in cur.fetchall()
            ]

            cur.execute("""
                SELECT s.name,
                       COUNT(sr.id)::bigint,
                       COALESCE(pg_total_relation_size('source_records'::regclass),0)::bigint
                FROM sources s
                LEFT JOIN source_records sr ON sr.source_id=s.id
                WHERE lower(s.name) LIKE '%scryfall%'
                GROUP BY s.name
                ORDER BY s.name
            """)
            legacy_source_records = [
                {"source": str(name), "rows": int(rows), "table_total_bytes": int(total_bytes)}
                for name, rows, total_bytes in cur.fetchall()
            ]
            scryfall_raw_rows = sum(item["rows"] for item in legacy_source_records)

            cur.execute("""
                SELECT s.name, COUNT(ss.id)::bigint
                FROM sources s LEFT JOIN source_sync_state ss ON ss.source_id=s.id
                WHERE lower(s.name) LIKE '%scryfall%'
                GROUP BY s.name ORDER BY s.name
            """)
            legacy_sync_state = [{"source": str(name), "rows": int(rows)} for name, rows in cur.fetchall()]
            cur.execute("""
                SELECT s.name, COUNT(ir.id)::bigint
                FROM sources s LEFT JOIN ingest_runs ir ON ir.source_id=s.id
                WHERE lower(s.name) LIKE '%scryfall%'
                GROUP BY s.name ORDER BY s.name
            """)
            legacy_ingest_runs = [{"source": str(name), "rows": int(rows)} for name, rows in cur.fetchall()]

            relation_names = [
                "sets", "cards", "card_attributes", "prints", "print_attributes",
                "print_identifiers", "print_images", "card_search_profiles", "print_search_profiles",
                "source_records",
            ]
            relation_sizes = {}
            for name in relation_names:
                cur.execute("SELECT pg_total_relation_size(%s::regclass)", (name,))
                relation_sizes[name] = int(cur.fetchone()[0])

            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0])
            cur.execute("SELECT current_setting('neon.max_cluster_size', true)")
            neon_limit = str(cur.fetchone()[0])
            cur.execute("SELECT version_num FROM alembic_version")
            alembic = str(cur.fetchone()[0])

        conn.rollback()
    finally:
        conn.close()

    blockers = []
    for key, expected in EXPECTED.items():
        if counts[key] != expected:
            blockers.append(f"count:{key}:{counts[key]}!={expected}")
    if finishes != EXPECTED_FINISHES:
        blockers.append(f"finish_counts:{finishes!r}")
    for key in (
        "blank_card_key", "duplicate_card_key_groups", "blank_print_key",
        "duplicate_print_key_groups", "blank_scryfall_id",
        "duplicate_scryfall_finish_groups", "unknown_finish_rows",
        "print_identifier_missing", "print_identifier_duplicate_external_groups",
    ):
        if integrity[key] != 0:
            blockers.append(f"integrity:{key}:{integrity[key]}")
    if integrity["fallback_cards_without_oracle"] != 71:
        blockers.append(f"fallback_cards:{integrity['fallback_cards_without_oracle']}!=71")
    if invalid_indexes:
        blockers.append(f"invalid_indexes:{len(invalid_indexes)}")
    for slug, expected in EXPECTED_NON_MTG.items():
        if non_mtg.get(slug) != expected:
            blockers.append(f"non_mtg:{slug}:{non_mtg.get(slug)!r}!={expected!r}")
    if counts["prices"] != 0:
        blockers.append(f"economics:prices:{counts['prices']}")

    report = {
        "status": "pass" if not blockers else "blocked",
        "mode": "read_only",
        "database_writes": 0,
        "alembic_version": alembic,
        "neon_max_cluster_size": neon_limit,
        "database_bytes": database_bytes,
        "counts": counts,
        "finish_counts": finishes,
        "integrity": integrity,
        "invalid_indexes": invalid_indexes,
        "non_mtg_catalogs": non_mtg,
        "legacy_scryfall_source_records": legacy_source_records,
        "legacy_scryfall_source_records_total": scryfall_raw_rows,
        "legacy_scryfall_sync_state": legacy_sync_state,
        "legacy_scryfall_ingest_runs": legacy_ingest_runs,
        "relation_sizes_bytes": relation_sizes,
        "blockers": blockers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if blockers:
        raise SystemExit("MTG V2.3 post-load audit BLOCKED")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
