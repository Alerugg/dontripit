from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification

LANGUAGES_SQL = "('es','ja')"


def _log(message: str) -> None:
    print(f"[mtg-multilingual-lean-seed] {message}", flush=True)


def _copy_filtered(src, dst, table: str, where_sql: str = "TRUE", params: tuple = ()) -> dict[str, Any]:
    """Schema-safe COPY for tables keyed either by id or directly by FK."""
    if not certification._table_exists(src, table) or not certification._table_exists(dst, table):
        return {"table": table, "rows": 0, "skipped": True}
    source_columns = certification._columns(src, table)
    target_columns = set(certification._columns(dst, table))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        raise RuntimeError(f"No common columns for {table}")
    quoted = ",".join(f'"{column}"' for column in columns)
    where = src.mogrify(where_sql, params).decode("utf-8") if params else where_sql
    src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')
    count = int(src.fetchone()[0])
    order_column = "id" if "id" in columns else columns[0]
    fd, tmp_name = tempfile.mkstemp(prefix=f"dontripit-{table}-", suffix=".csv")
    os.close(fd)
    path = Path(tmp_name)
    started = time.monotonic()
    try:
        with path.open("w", encoding="utf-8", newline="") as out:
            src.copy_expert(
                f'COPY (SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY "{order_column}") TO STDOUT WITH (FORMAT CSV)',
                out,
            )
        digest = certification._sha256(path)
        if count:
            with path.open("r", encoding="utf-8", newline="") as inp:
                dst.copy_expert(f'COPY "{table}" ({quoted}) FROM STDIN WITH (FORMAT CSV)', inp)
        _log(f"copy table={table} rows={count} elapsed={time.monotonic() - started:.1f}s")
        return {
            "table": table,
            "rows": count,
            "sha256": digest,
            "columns": columns,
            "order_column": order_column,
        }
    finally:
        path.unlink(missing_ok=True)


def _reset_sequence(cur, table: str) -> None:
    columns = certification._columns(cur, table)
    if "id" not in columns:
        return
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    row = cur.fetchone()
    sequence = row[0] if row else None
    if not sequence:
        return
    cur.execute(f'SELECT COALESCE(MAX(id),0) FROM "{table}"')
    maximum = int(cur.fetchone()[0] or 0)
    if maximum:
        cur.execute("SELECT setval(%s,%s,true)", (sequence, maximum))


def _install_guards(cur) -> dict[str, Any]:
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION mtg_cert_block_all_dml() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'MTG certification guard blocked % on %', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION mtg_cert_guard_prints() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF lower(coalesce(NEW.language,'')) NOT IN ('es','ja') THEN
              RAISE EXCEPTION 'MTG certification only permits ES/JA Print inserts';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'MTG certification forbids Print %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION mtg_cert_insert_only() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'MTG certification insert-only guard blocked % on %', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION mtg_cert_localization_guard() RETURNS trigger AS $$
        DECLARE print_lang text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'MTG certification forbids localization DELETE';
          END IF;
          SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
          IF print_lang NOT IN ('es','ja') OR lower(coalesce(NEW.language,'')) NOT IN ('es','ja') THEN
            RAISE EXCEPTION 'MTG certification only permits ES/JA localization writes';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    blocked_all: list[str] = []
    for table in (
        "games",
        "sets",
        "cards",
        "set_identifiers",
        "card_identifiers",
        "card_attributes",
        "print_identifiers",
        "prices",
        "price_snapshots",
        "price_daily_ohlc",
    ):
        if not certification._table_exists(cur, table):
            continue
        cur.execute(f'DROP TRIGGER IF EXISTS mtg_cert_block_all_dml_trigger ON "{table}"')
        cur.execute(
            f'CREATE TRIGGER mtg_cert_block_all_dml_trigger BEFORE INSERT OR UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION mtg_cert_block_all_dml()"
        )
        blocked_all.append(table)

    cur.execute('DROP TRIGGER IF EXISTS mtg_cert_guard_prints_trigger ON prints')
    cur.execute(
        "CREATE TRIGGER mtg_cert_guard_prints_trigger BEFORE INSERT OR UPDATE OR DELETE ON prints "
        "FOR EACH ROW EXECUTE FUNCTION mtg_cert_guard_prints()"
    )

    insert_only: list[str] = []
    for table in ("print_attributes", "print_images"):
        if not certification._table_exists(cur, table):
            continue
        cur.execute(f'DROP TRIGGER IF EXISTS mtg_cert_insert_only_trigger ON "{table}"')
        cur.execute(
            f'CREATE TRIGGER mtg_cert_insert_only_trigger BEFORE INSERT OR UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION mtg_cert_insert_only()"
        )
        insert_only.append(table)

    if certification._table_exists(cur, "print_localizations"):
        cur.execute('DROP TRIGGER IF EXISTS mtg_cert_localization_guard_trigger ON print_localizations')
        cur.execute(
            "CREATE TRIGGER mtg_cert_localization_guard_trigger BEFORE INSERT OR UPDATE OR DELETE ON print_localizations "
            "FOR EACH ROW EXECUTE FUNCTION mtg_cert_localization_guard()"
        )

    return {
        "blocked_all_dml_tables": blocked_all,
        "prints": "insert-es-ja-only; update-delete-blocked",
        "insert_only_tables": insert_only,
        "print_localizations": "insert-update-es-ja-only; delete-blocked",
    }


def seed_ephemeral_lean(production_url: str, target_url: str) -> dict[str, Any]:
    started = time.monotonic()
    source = psycopg2.connect(
        production_url,
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_lean_seed_readonly",
    )
    target = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_lean_ephemeral",
    )
    source.set_session(readonly=True, autocommit=False)
    target.set_session(readonly=False, autocommit=False)
    copied: list[dict[str, Any]] = []
    try:
        with source.cursor() as src, target.cursor() as dst:
            src.execute("SHOW transaction_read_only")
            if str(src.fetchone()[0]).lower() != "on":
                raise RuntimeError("Production read-only guard failed")
            game_id, slug = certification._find_game(src)
            dst.execute("SELECT count(*) FROM games")
            if int(dst.fetchone()[0]) != 0:
                raise RuntimeError("Ephemeral target already has catalog data")

            _log("copy canonical game/sets/cards")
            copied.append(_copy_filtered(src, dst, "games", "id=%s", (game_id,)))
            copied.append(_copy_filtered(src, dst, "sets", "game_id=%s", (game_id,)))
            copied.append(_copy_filtered(src, dst, "cards", "game_id=%s", (game_id,)))

            _log("copy all existing MTG Print identities")
            copied.append(
                _copy_filtered(
                    src,
                    dst,
                    "prints",
                    "card_id IN (SELECT id FROM cards WHERE game_id=%s)",
                    (game_id,),
                )
            )

            _log("copy only existing ES/JA child evidence needed for idempotence/fidelity")
            target_prints = (
                "print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id "
                f"WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN {LANGUAGES_SQL})"
            )
            for table in ("print_attributes", "print_images", "print_localizations"):
                copied.append(_copy_filtered(src, dst, table, target_prints, (game_id,)))

            for table in (
                "games",
                "sets",
                "cards",
                "prints",
                "print_attributes",
                "print_images",
                "print_localizations",
            ):
                if certification._table_exists(dst, table):
                    _reset_sequence(dst, table)

            baseline = certification._state(dst, game_id)
            baseline["sets_digest"] = certification._digest_query(
                dst, "SELECT * FROM sets WHERE game_id=%s ORDER BY id", (game_id,)
            )
            baseline["cards_digest"] = certification._digest_query(
                dst, "SELECT * FROM cards WHERE game_id=%s ORDER BY id", (game_id,)
            )
            dst.execute(
                "SELECT COALESCE(MAX(p.id),0) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
                (game_id,),
            )
            baseline_max_print_id = int(dst.fetchone()[0] or 0)
            baseline["baseline_max_print_id"] = baseline_max_print_id
            baseline["preexisting_prints_digest"] = certification._digest_query(
                dst,
                """
                SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id
                """,
                (game_id, baseline_max_print_id),
            )

            guards = _install_guards(dst)
            target.commit()
            source.rollback()
            baseline.update(
                {
                    "game_id": game_id,
                    "game_slug": slug,
                    "production_transaction_read_only": True,
                    "copied": copied,
                    "lean_clone": True,
                    "economics_copied": False,
                    "economics_write_guard": True,
                    "guards": guards,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            )
            _log(f"complete elapsed={baseline['elapsed_seconds']}s")
            return baseline
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def validate_lean_invariants(target_url: str, baseline: dict[str, Any]) -> dict[str, Any]:
    conn = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name="dontripit_mtg_multilingual_lean_invariants",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id = int(baseline["game_id"])
            sets_digest = certification._digest_query(
                cur, "SELECT * FROM sets WHERE game_id=%s ORDER BY id", (game_id,)
            )
            cards_digest = certification._digest_query(
                cur, "SELECT * FROM cards WHERE game_id=%s ORDER BY id", (game_id,)
            )
            preexisting_prints_digest = certification._digest_query(
                cur,
                """
                SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id
                """,
                (game_id, int(baseline["baseline_max_print_id"])),
            )
            if sets_digest != baseline["sets_digest"]:
                raise AssertionError("Canonical MTG Sets changed in lean certification")
            if cards_digest != baseline["cards_digest"]:
                raise AssertionError("Canonical MTG Cards changed in lean certification")
            if preexisting_prints_digest != baseline["preexisting_prints_digest"]:
                raise AssertionError("Pre-existing MTG Print rows changed in lean certification")

            economics = {}
            for table in ("prices", "price_snapshots", "price_daily_ohlc"):
                if certification._table_exists(cur, table):
                    cur.execute(f'SELECT count(*) FROM "{table}"')
                    count = int(cur.fetchone()[0])
                    economics[table] = count
                    if count != 0:
                        raise AssertionError(f"Lean certification economics table unexpectedly non-empty: {table}={count}")
            conn.rollback()
            return {
                "status": "pass",
                "sets_digest_unchanged": True,
                "cards_digest_unchanged": True,
                "preexisting_prints_digest_unchanged": True,
                "economics_write_guard": True,
                "economics_rows": economics,
            }
    finally:
        conn.close()
