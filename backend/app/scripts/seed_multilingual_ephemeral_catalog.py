from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values


BASELINE_PATH = Path("/tmp/pokemon-multilingual-ephemeral-baseline.json")

GAME_COLUMNS = ("id", "slug", "name", "created_at")
SET_COLUMNS = (
    "id",
    "game_id",
    "code",
    "tcgdex_id",
    "yugioh_id",
    "riftbound_id",
    "name",
    "release_date",
    "created_at",
)
CARD_COLUMNS = (
    "id",
    "game_id",
    "name",
    "card_key",
    "oracle_id",
    "tcgdex_id",
    "yugoprodeck_id",
    "riftbound_id",
    "created_at",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _hash_rows(rows: list[tuple[Any, ...]]) -> str:
    payload = [list(map(_json_value, row)) for row in rows]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _select_rows(cur, table: str, columns: tuple[str, ...], where: str, params: tuple) -> list[tuple]:
    quoted = ", ".join(f'"{column}"' for column in columns)
    cur.execute(f'SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY id', params)
    return list(cur.fetchall())


def _insert_rows(cur, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    if not rows:
        return
    quoted = ", ".join(f'"{column}"' for column in columns)
    execute_values(
        cur,
        f'INSERT INTO "{table}" ({quoted}) VALUES %s',
        rows,
        page_size=1000,
    )


def _reset_sequence(cur, table: str) -> None:
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    sequence = cur.fetchone()[0]
    if not sequence:
        return
    cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
    maximum = int(cur.fetchone()[0] or 0)
    if maximum:
        cur.execute("SELECT setval(%s, %s, true)", (sequence, maximum))


def main() -> int:
    production_url = os.getenv("PRODUCTION_DATABASE_URL")
    target_url = os.getenv("EPHEMERAL_DATABASE_URL")
    if not production_url or not target_url:
        raise RuntimeError("PRODUCTION_DATABASE_URL and EPHEMERAL_DATABASE_URL are required")
    if production_url == target_url:
        raise RuntimeError("Safety guard: production and ephemeral database URLs must differ")

    source = psycopg2.connect(
        production_url,
        connect_timeout=20,
        application_name="dontripit_multilingual_ephemeral_seed_readonly",
    )
    source.set_session(readonly=True, autocommit=False)
    target = psycopg2.connect(
        target_url,
        connect_timeout=20,
        application_name="dontripit_multilingual_ephemeral_target",
    )
    target.set_session(readonly=False, autocommit=False)

    try:
        with source.cursor() as src:
            src.execute("SHOW transaction_read_only")
            read_only = str(src.fetchone()[0]).lower()
            if read_only != "on":
                raise RuntimeError(f"Production read-only guard failed: {read_only!r}")

            src.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            row = src.fetchone()
            if row is None:
                raise RuntimeError("Production Pokémon game row is missing")
            game_id = int(row[0])

            game_rows = _select_rows(src, "games", GAME_COLUMNS, "id = %s", (game_id,))
            set_rows = _select_rows(src, "sets", SET_COLUMNS, "game_id = %s", (game_id,))
            card_rows = _select_rows(src, "cards", CARD_COLUMNS, "game_id = %s", (game_id,))
            source.rollback()

        if len(game_rows) != 1 or not set_rows or not card_rows:
            raise RuntimeError(
                f"Unexpected production seed size: games={len(game_rows)} sets={len(set_rows)} cards={len(card_rows)}"
            )

        with target.cursor() as dst:
            dst.execute("SELECT count(*) FROM games")
            if int(dst.fetchone()[0]) != 0:
                raise RuntimeError("Ephemeral target is not empty; refusing to seed")

            _insert_rows(dst, "games", GAME_COLUMNS, game_rows)
            _insert_rows(dst, "sets", SET_COLUMNS, set_rows)
            _insert_rows(dst, "cards", CARD_COLUMNS, card_rows)
            for table in ("games", "sets", "cards"):
                _reset_sequence(dst, table)
        target.commit()

        baseline = {
            "production_transaction_read_only": "on",
            "production_tables_read": ["games", "sets", "cards"],
            "production_personal_tables_read": False,
            "target": "ephemeral-postgresql-only",
            "pokemon_game_id": game_id,
            "games": len(game_rows),
            "sets": len(set_rows),
            "cards": len(card_rows),
            "sets_sha256": _hash_rows(set_rows),
            "cards_sha256": _hash_rows(card_rows),
        }
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(baseline, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
