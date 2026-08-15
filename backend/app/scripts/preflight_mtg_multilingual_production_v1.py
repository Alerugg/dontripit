from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification

EXPECTED_REVISION = "20260814_34"
EXPECTED_SETS = 986
EXPECTED_CARDS = 37624
EXPECTED_PRINTS = 161144
EXPECTED_LANG = {"en": 158246, "es": 1207, "ja": 875}


def _normalize_url(value: str) -> str:
    if value.startswith("postgresql+psycopg2://"):
        return "postgresql://" + value[len("postgresql+psycopg2://"):]
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://"):]
    return value


def _database_url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL[_UNPOOLED] is required")
    return _normalize_url(value)


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _economics(cur, game_id: int) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if certification._table_exists(cur, "prices"):
        output["prices"] = certification._digest_query(
            cur, "SELECT * FROM prices WHERE game_id=%s ORDER BY id", (game_id,)
        )
    if certification._table_exists(cur, "price_snapshots"):
        output["price_snapshots"] = certification._digest_query(
            cur,
            """
            SELECT * FROM price_snapshots WHERE
              (entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)) OR
              (entity_type='print' AND entity_id IN (
                SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
              ))
            ORDER BY id
            """,
            (game_id, game_id),
        )
    if certification._table_exists(cur, "price_daily_ohlc"):
        output["price_daily_ohlc"] = certification._digest_query(
            cur,
            """
            SELECT * FROM price_daily_ohlc WHERE
              (entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)) OR
              (entity_type='print' AND entity_id IN (
                SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
              ))
            ORDER BY id
            """,
            (game_id, game_id),
        )
    return output


def run(output: Path) -> dict[str, Any]:
    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_production_preflight_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            read_only = str(cur.fetchone()[0]).lower() == "on"
            if not read_only:
                raise RuntimeError("Production preflight is not read-only")

            cur.execute("SELECT version_num FROM alembic_version")
            revision = str(cur.fetchone()[0])
            if revision != EXPECTED_REVISION:
                raise AssertionError(f"Unexpected Alembic revision: {revision}")

            game_id, game_slug = certification._find_game(cur)
            sets = _count(cur, "SELECT count(*) FROM sets WHERE game_id=%s", (game_id,))
            cards = _count(cur, "SELECT count(*) FROM cards WHERE game_id=%s", (game_id,))
            prints = _count(
                cur,
                "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
                (game_id,),
            )
            if (sets, cards, prints) != (EXPECTED_SETS, EXPECTED_CARDS, EXPECTED_PRINTS):
                raise AssertionError(
                    f"MTG production baseline moved: sets={sets} cards={cards} prints={prints}"
                )

            cur.execute(
                """
                SELECT lower(coalesce(p.language,'')),count(*)
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s GROUP BY 1 ORDER BY 1
                """,
                (game_id,),
            )
            languages = {str(lang): int(count) for lang, count in cur.fetchall()}
            for lang, expected in EXPECTED_LANG.items():
                if languages.get(lang, 0) != expected:
                    raise AssertionError(f"MTG {lang} baseline moved: {languages.get(lang, 0)} != {expected}")

            missing_target_scryfall = _count(
                cur,
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                  AND p.scryfall_id IS NULL
                """,
                (game_id,),
            )
            duplicate_target_identity = _count(
                cur,
                """
                SELECT count(*) FROM (
                  SELECT p.scryfall_id,p.variant,count(*)
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                  GROUP BY p.scryfall_id,p.variant HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            scryfall_aux_identifiers = _count(
                cur,
                """
                SELECT count(*) FROM print_identifiers pi
                JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND pi.source='scryfall'
                """,
                (game_id,),
            )
            target_localizations = _count(
                cur,
                """
                SELECT count(*) FROM print_localizations l
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                """,
                (game_id,),
            )
            if missing_target_scryfall or duplicate_target_identity or scryfall_aux_identifiers:
                raise AssertionError(
                    "MTG target identity preflight failed: "
                    f"missing_scryfall={missing_target_scryfall} duplicates={duplicate_target_identity} "
                    f"aux_scryfall_ids={scryfall_aux_identifiers}"
                )
            if target_localizations != 0:
                raise AssertionError(f"Expected zero pre-rollout MTG ES/JA localizations, got {target_localizations}")

            cur.execute(
                "SELECT COALESCE(MAX(p.id),0) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
                (game_id,),
            )
            baseline_max_print_id = int(cur.fetchone()[0] or 0)

            digests = {
                "sets": certification._digest_query(
                    cur, "SELECT * FROM sets WHERE game_id=%s ORDER BY id", (game_id,)
                ),
                "cards": certification._digest_query(
                    cur, "SELECT * FROM cards WHERE game_id=%s ORDER BY id", (game_id,)
                ),
                "prints": certification._digest_query(
                    cur,
                    """
                    SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s ORDER BY p.id
                    """,
                    (game_id,),
                ),
                "print_attributes": certification._digest_query(
                    cur,
                    """
                    SELECT pa.* FROM print_attributes pa
                    JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s ORDER BY pa.print_id
                    """,
                    (game_id,),
                ),
                "print_identifiers": certification._digest_query(
                    cur,
                    """
                    SELECT pi.* FROM print_identifiers pi
                    JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s ORDER BY pi.id
                    """,
                    (game_id,),
                ),
                "print_images": certification._digest_query(
                    cur,
                    """
                    SELECT i.* FROM print_images i
                    JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=%s ORDER BY i.id
                    """,
                    (game_id,),
                ),
                "economics": _economics(cur, game_id),
            }

            report = {
                "status": "pass",
                "mode": "strict-read-only-production-preflight",
                "database_writes": 0,
                "transaction_read_only": read_only,
                "alembic_revision": revision,
                "game": {"id": game_id, "slug": game_slug},
                "baseline": {
                    "sets": sets,
                    "cards": cards,
                    "prints": prints,
                    "languages": languages,
                    "baseline_max_print_id": baseline_max_print_id,
                    "target_localizations": target_localizations,
                    "missing_target_scryfall_ids": missing_target_scryfall,
                    "duplicate_target_scryfall_finish_identities": duplicate_target_identity,
                    "auxiliary_scryfall_print_identifiers": scryfall_aux_identifiers,
                },
                "digests": digests,
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            conn.rollback()
            return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict read-only MTG ES/JA production rollout preflight")
    parser.add_argument("--output", default="/tmp/mtg-multilingual-production-preflight.json")
    args = parser.parse_args()
    run(Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
