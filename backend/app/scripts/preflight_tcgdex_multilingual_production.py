from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2

from app.scripts.seed_multilingual_ephemeral_catalog import (
    CARD_COLUMNS,
    SET_COLUMNS,
    _hash_rows,
    _select_rows,
)


OUTPUT_PATH = Path("/tmp/tcgdex-multilingual-production-preflight.json")
EXPECTED_ALEMBIC_VERSION = "20260810_32"
EXPECTED_SETS = 203
EXPECTED_CARDS = 21065
EXPECTED_PRINTS = 33757
EXPECTED_SET_HASH = "8ca65b393e8754f89bc9944ca79c8705589d6524137e8c2729646f816dc5d553"
EXPECTED_CARD_HASH = "f749f6a5249083f862d543f174ffbf15f7e3c2dc402a6a41cd59c714937e0ce2"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    if value.startswith("postgresql+psycopg2://"):
        value = "postgresql://" + value[len("postgresql+psycopg2://") :]
    elif value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    return value


def run() -> dict:
    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=20,
        application_name="dontripit_multilingual_production_preflight_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = str(cur.fetchone()[0]).lower()
            if transaction_read_only != "on":
                raise RuntimeError(
                    f"Production read-only guard failed: {transaction_read_only!r}"
                )

            cur.execute("SELECT version_num FROM alembic_version")
            alembic_version = str(cur.fetchone()[0])

            cur.execute("SELECT current_database(), current_user")
            database_name, database_user = map(str, cur.fetchone())

            cur.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Production Pokémon game row is missing")
            game_id = int(row[0])

            set_rows = _select_rows(cur, "sets", SET_COLUMNS, "game_id = %s", (game_id,))
            card_rows = _select_rows(cur, "cards", CARD_COLUMNS, "game_id = %s", (game_id,))

            cur.execute(
                """
                SELECT lower(trim(coalesce(p.language, ''))) AS language, count(*)::bigint
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = %s
                GROUP BY 1
                ORDER BY 1
                """,
                (game_id,),
            )
            print_languages = {str(lang): int(count) for lang, count in cur.fetchall()}
            print_count = sum(print_languages.values())

            cur.execute(
                """
                SELECT relname
                FROM pg_class
                WHERE relkind = 'r'
                  AND relname IN ('print_localizations', 'set_identifiers', 'card_identifiers')
                ORDER BY relname
                """
            )
            future_tables_present = [str(row[0]) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT count(*)::bigint
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = %s AND lower(trim(coalesce(p.language, ''))) IN ('es', 'ja')
                """,
                (game_id,),
            )
            non_en_target_prints = int(cur.fetchone()[0])

            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0])

        conn.rollback()
    finally:
        conn.close()

    report = {
        "status": "pass",
        "mode": "strict-read-only-production-preflight",
        "database_writes": 0,
        "personal_data_tables_queried": False,
        "transaction_read_only": transaction_read_only,
        "database_identity": {
            "database_name": database_name,
            "database_user": database_user,
        },
        "database_bytes": database_bytes,
        "alembic_version": alembic_version,
        "pokemon": {
            "game_id": game_id,
            "sets": len(set_rows),
            "cards": len(card_rows),
            "prints": print_count,
            "print_language_counts": print_languages,
            "sets_sha256": _hash_rows(set_rows),
            "cards_sha256": _hash_rows(card_rows),
            "target_es_ja_prints_before_rollout": non_en_target_prints,
        },
        "future_multilingual_tables_present": future_tables_present,
        "expected": {
            "alembic_version": EXPECTED_ALEMBIC_VERSION,
            "sets": EXPECTED_SETS,
            "cards": EXPECTED_CARDS,
            "prints": EXPECTED_PRINTS,
            "print_language_counts": {"en": EXPECTED_PRINTS},
            "sets_sha256": EXPECTED_SET_HASH,
            "cards_sha256": EXPECTED_CARD_HASH,
            "future_multilingual_tables_present": [],
            "target_es_ja_prints_before_rollout": 0,
        },
    }

    actual = {
        "alembic_version": alembic_version,
        "sets": len(set_rows),
        "cards": len(card_rows),
        "prints": print_count,
        "print_language_counts": print_languages,
        "sets_sha256": report["pokemon"]["sets_sha256"],
        "cards_sha256": report["pokemon"]["cards_sha256"],
        "future_multilingual_tables_present": future_tables_present,
        "target_es_ja_prints_before_rollout": non_en_target_prints,
    }
    expected = report["expected"]
    if actual != expected:
        report["status"] = "blocked"
        report["actual_gate_state"] = actual
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise RuntimeError(
            "Production multilingual baseline moved or is partially applied; refusing rollout preflight"
        )

    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
