from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2

BASELINE_PATH = Path('/tmp/yugioh-multilingual-ephemeral-baseline.json')


def _log(message: str) -> None:
    print(f'[ygo-multilingual-lean-seed] {message}', flush=True)


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s)",
        (table,),
    )
    return bool(cur.fetchone()[0])


def _columns(cur, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",
        (table,),
    )
    return [str(row[0]) for row in cur.fetchall()]


def _copy_filtered(src, dst, table: str, where_sql: str = 'TRUE', params: tuple = ()) -> dict[str, Any]:
    if not _table_exists(src, table) or not _table_exists(dst, table):
        return {'table': table, 'rows': 0, 'skipped': True}
    source_columns = _columns(src, table)
    target_columns = set(_columns(dst, table))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        raise RuntimeError(f'No common columns for {table}')
    quoted = ','.join(f'"{column}"' for column in columns)
    where = src.mogrify(where_sql, params).decode('utf-8') if params else where_sql
    src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')
    count = int(src.fetchone()[0])
    order_column = 'id' if 'id' in columns else columns[0]
    fd, tmp_name = tempfile.mkstemp(prefix=f'dontripit-ygo-{table}-', suffix='.csv')
    os.close(fd)
    path = Path(tmp_name)
    started = time.monotonic()
    try:
        with path.open('w', encoding='utf-8', newline='') as out:
            src.copy_expert(
                f'COPY (SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY "{order_column}") TO STDOUT WITH (FORMAT CSV)',
                out,
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if count:
            with path.open('r', encoding='utf-8', newline='') as inp:
                dst.copy_expert(f'COPY "{table}" ({quoted}) FROM STDIN WITH (FORMAT CSV)', inp)
        _log(f'copy table={table} rows={count} elapsed={time.monotonic() - started:.1f}s')
        return {
            'table': table,
            'rows': count,
            'sha256': digest,
            'columns': columns,
            'order_column': order_column,
        }
    finally:
        path.unlink(missing_ok=True)


def _reset_sequence(cur, table: str) -> None:
    if not _table_exists(cur, table) or 'id' not in _columns(cur, table):
        return
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    row = cur.fetchone()
    sequence = row[0] if row else None
    if not sequence:
        return
    cur.execute(f'SELECT COALESCE(MAX(id),0) FROM "{table}"')
    maximum = int(cur.fetchone()[0] or 0)
    if maximum:
        cur.execute('SELECT setval(%s,%s,true)', (sequence, maximum))


def _digest_query(cur, query: str, params: tuple = ()) -> str:
    fd, tmp_name = tempfile.mkstemp(prefix='dontripit-ygo-digest-', suffix='.csv')
    os.close(fd)
    path = Path(tmp_name)
    try:
        rendered = cur.mogrify(query, params).decode('utf-8') if params else query
        with path.open('w', encoding='utf-8', newline='') as out:
            cur.copy_expert(f'COPY ({rendered}) TO STDOUT WITH (FORMAT CSV)', out)
        return hashlib.sha256(path.read_bytes()).hexdigest()
    finally:
        path.unlink(missing_ok=True)


def _install_guards(cur, game_id: int) -> dict[str, Any]:
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION ygo_cert_block_all_dml() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'YGO certification guard blocked % on %', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        f"""
        CREATE OR REPLACE FUNCTION ygo_cert_guard_sets() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.game_id <> {int(game_id)} OR lower(coalesce(NEW.region,'')) NOT IN ('global','jp') THEN
              RAISE EXCEPTION 'YGO certification only permits YGO global/jp Set inserts';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'YGO certification forbids Set %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        f"""
        CREATE OR REPLACE FUNCTION ygo_cert_guard_prints() RETURNS trigger AS $$
        DECLARE card_game integer;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            SELECT game_id INTO card_game FROM cards WHERE id=NEW.card_id;
            IF card_game <> {int(game_id)} OR lower(coalesce(NEW.language,'')) NOT IN ('es','ja') THEN
              RAISE EXCEPTION 'YGO certification only permits ES/JA YGO Print inserts';
            END IF;
            IF NEW.yugioh_id IS NOT NULL OR NEW.scryfall_id IS NOT NULL OR NEW.tcgdex_id IS NOT NULL OR NEW.riftbound_id IS NOT NULL THEN
              RAISE EXCEPTION 'YGO certification forbids foreign global Print IDs on localized inserts';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'YGO certification forbids Print %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION ygo_cert_guard_localization() RETURNS trigger AS $$
        DECLARE print_lang text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'YGO certification forbids localization %', TG_OP;
          END IF;
          SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
          IF print_lang NOT IN ('es','ja') OR lower(coalesce(NEW.language,'')) <> print_lang OR NEW.source <> 'ygojson' THEN
            RAISE EXCEPTION 'YGO localization certification scope violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION ygo_cert_guard_image() RETURNS trigger AS $$
        DECLARE print_lang text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'YGO certification forbids image %', TG_OP;
          END IF;
          SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
          IF print_lang NOT IN ('es','ja') OR NEW.source <> 'ygojson' THEN
            RAISE EXCEPTION 'YGO image certification scope violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        f"""
        CREATE OR REPLACE FUNCTION ygo_cert_guard_release() RETURNS trigger AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'YGO certification forbids release %', TG_OP;
          END IF;
          IF NEW.game_id <> {int(game_id)} OR NEW.source <> 'ygojson' THEN
            RAISE EXCEPTION 'YGO release certification scope violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION ygo_cert_guard_print_release() RETURNS trigger AS $$
        DECLARE release_source text;
        DECLARE print_lang text;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'YGO certification forbids print_release %', TG_OP;
          END IF;
          SELECT source INTO release_source FROM catalog_releases WHERE id=NEW.release_id;
          SELECT lower(coalesce(language,'')) INTO print_lang FROM prints WHERE id=NEW.print_id;
          IF release_source <> 'ygojson' OR print_lang NOT IN ('es','ja') THEN
            RAISE EXCEPTION 'YGO PrintRelease certification scope violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    blocked_all: list[str] = []
    for table in (
        'games', 'cards', 'card_identifiers', 'set_identifiers', 'print_identifiers',
        'card_attributes', 'print_attributes', 'products', 'product_variants',
        'product_images', 'product_identifiers', 'sources', 'source_records',
        'price_sources', 'prices', 'price_snapshots', 'price_daily_ohlc',
    ):
        if not _table_exists(cur, table):
            continue
        trigger = f'ygo_cert_block_all_dml_{table}'
        cur.execute(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        cur.execute(
            f'CREATE TRIGGER "{trigger}" BEFORE INSERT OR UPDATE OR DELETE ON "{table}" '
            'FOR EACH ROW EXECUTE FUNCTION ygo_cert_block_all_dml()'
        )
        blocked_all.append(table)

    cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_sets_trigger ON sets')
    cur.execute(
        'CREATE TRIGGER ygo_cert_guard_sets_trigger BEFORE INSERT OR UPDATE OR DELETE ON sets '
        'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_sets()'
    )
    cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_prints_trigger ON prints')
    cur.execute(
        'CREATE TRIGGER ygo_cert_guard_prints_trigger BEFORE INSERT OR UPDATE OR DELETE ON prints '
        'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_prints()'
    )
    if _table_exists(cur, 'print_localizations'):
        cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_localization_trigger ON print_localizations')
        cur.execute(
            'CREATE TRIGGER ygo_cert_guard_localization_trigger BEFORE INSERT OR UPDATE OR DELETE ON print_localizations '
            'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_localization()'
        )
    if _table_exists(cur, 'print_images'):
        cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_image_trigger ON print_images')
        cur.execute(
            'CREATE TRIGGER ygo_cert_guard_image_trigger BEFORE INSERT OR UPDATE OR DELETE ON print_images '
            'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_image()'
        )
    if _table_exists(cur, 'catalog_releases'):
        cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_release_trigger ON catalog_releases')
        cur.execute(
            'CREATE TRIGGER ygo_cert_guard_release_trigger BEFORE INSERT OR UPDATE OR DELETE ON catalog_releases '
            'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_release()'
        )
    if _table_exists(cur, 'print_releases'):
        cur.execute('DROP TRIGGER IF EXISTS ygo_cert_guard_print_release_trigger ON print_releases')
        cur.execute(
            'CREATE TRIGGER ygo_cert_guard_print_release_trigger BEFORE INSERT OR UPDATE OR DELETE ON print_releases '
            'FOR EACH ROW EXECUTE FUNCTION ygo_cert_guard_print_release()'
        )

    return {
        'blocked_all_dml_tables': blocked_all,
        'sets': 'insert-ygo-global-jp-only; update-delete-blocked',
        'prints': 'insert-ygo-es-ja-only; foreign-global-ids-null; update-delete-blocked',
        'print_localizations': 'insert-ygojson-es-ja-only; update-delete-blocked',
        'print_images': 'insert-ygojson-es-ja-only; update-delete-blocked',
        'catalog_releases': 'insert-ygojson-ygo-only; update-delete-blocked',
        'print_releases': 'insert-ygojson-target-print-only; update-delete-blocked',
    }


def seed_ephemeral_lean(production_url: str, target_url: str) -> dict[str, Any]:
    if production_url == target_url:
        raise RuntimeError('Safety guard: production and ephemeral URLs are identical')
    parsed = urlparse(target_url)
    if parsed.hostname not in {'127.0.0.1', 'localhost'}:
        raise RuntimeError(f'Safety guard: ephemeral host is not local: {parsed.hostname!r}')

    started = time.monotonic()
    source = psycopg2.connect(
        production_url,
        connect_timeout=30,
        application_name='dontripit_ygo_multilingual_lean_seed_readonly',
    )
    target = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name='dontripit_ygo_multilingual_lean_ephemeral',
    )
    source.set_session(readonly=True, autocommit=False)
    target.set_session(readonly=False, autocommit=False)
    copied: list[dict[str, Any]] = []
    try:
        with source.cursor() as src, target.cursor() as dst:
            src.execute('SHOW transaction_read_only')
            if str(src.fetchone()[0]).lower() != 'on':
                raise RuntimeError('Production read-only guard failed')
            src.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            row = src.fetchone()
            if not row:
                raise RuntimeError('Production Yu-Gi-Oh game row missing')
            game_id = int(row[0])
            src.execute('SELECT version_num FROM alembic_version LIMIT 1')
            source_revision = src.fetchone()[0]
            dst.execute('SELECT version_num FROM alembic_version LIMIT 1')
            target_revision = dst.fetchone()[0]
            dst.execute('SELECT count(*) FROM games')
            if int(dst.fetchone()[0]) != 0:
                raise RuntimeError('Ephemeral target already contains catalog data')

            copied.append(_copy_filtered(src, dst, 'games', 'id=%s', (game_id,)))
            copied.append(_copy_filtered(src, dst, 'sets', 'game_id=%s', (game_id,)))
            copied.append(_copy_filtered(src, dst, 'cards', 'game_id=%s', (game_id,)))
            ygo_print_filter = 'card_id IN (SELECT id FROM cards WHERE game_id=%s)'
            copied.append(_copy_filtered(src, dst, 'prints', ygo_print_filter, (game_id,)))
            ygo_set_filter = 'set_id IN (SELECT id FROM sets WHERE game_id=%s)'
            ygo_card_filter = 'card_id IN (SELECT id FROM cards WHERE game_id=%s)'
            ygo_print_child_filter = (
                'print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)'
            )
            for table, where in (
                ('set_identifiers', ygo_set_filter),
                ('card_identifiers', ygo_card_filter),
                ('print_identifiers', ygo_print_child_filter),
                ('print_images', ygo_print_child_filter),
                ('print_attributes', ygo_print_child_filter),
                ('print_localizations', ygo_print_child_filter),
            ):
                copied.append(_copy_filtered(src, dst, table, where, (game_id,)))
            copied.append(_copy_filtered(src, dst, 'catalog_releases', 'game_id=%s', (game_id,)))
            copied.append(
                _copy_filtered(
                    src,
                    dst,
                    'print_releases',
                    'release_id IN (SELECT id FROM catalog_releases WHERE game_id=%s)',
                    (game_id,),
                )
            )

            for table in (
                'games', 'sets', 'cards', 'prints', 'set_identifiers', 'card_identifiers',
                'print_identifiers', 'print_images', 'print_attributes', 'print_localizations',
                'catalog_releases', 'print_releases',
            ):
                _reset_sequence(dst, table)

            dst.execute('SELECT COALESCE(MAX(id),0) FROM sets WHERE game_id=%s', (game_id,))
            baseline_max_set_id = int(dst.fetchone()[0] or 0)
            dst.execute('SELECT COALESCE(MAX(id),0) FROM cards WHERE game_id=%s', (game_id,))
            baseline_max_card_id = int(dst.fetchone()[0] or 0)
            dst.execute(
                'SELECT COALESCE(MAX(p.id),0) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s',
                (game_id,),
            )
            baseline_max_print_id = int(dst.fetchone()[0] or 0)

            baseline = {
                'game_id': game_id,
                'source_revision': source_revision,
                'target_revision': target_revision,
                'baseline_max_set_id': baseline_max_set_id,
                'baseline_max_card_id': baseline_max_card_id,
                'baseline_max_print_id': baseline_max_print_id,
                'sets_digest': _digest_query(
                    dst, 'SELECT * FROM sets WHERE game_id=%s AND id<=%s ORDER BY id',
                    (game_id, baseline_max_set_id),
                ),
                'cards_digest': _digest_query(
                    dst, 'SELECT * FROM cards WHERE game_id=%s AND id<=%s ORDER BY id',
                    (game_id, baseline_max_card_id),
                ),
                'preexisting_prints_digest': _digest_query(
                    dst,
                    'SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id',
                    (game_id, baseline_max_print_id),
                ),
            }
            guards = _install_guards(dst, game_id)
            target.commit()
            source.rollback()
            baseline.update(
                {
                    'production_transaction_read_only': True,
                    'production_writes': 0,
                    'production_tables_read': [entry['table'] for entry in copied if not entry.get('skipped')],
                    'economics_copied': False,
                    'lean_clone': True,
                    'copied': copied,
                    'guards': guards,
                    'elapsed_seconds': round(time.monotonic() - started, 2),
                }
            )
            BASELINE_PATH.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            _log(f"complete elapsed={baseline['elapsed_seconds']}s source_rev={source_revision} target_rev={target_revision}")
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
        application_name='dontripit_ygo_multilingual_lean_invariants',
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id = int(baseline['game_id'])
            sets_digest = _digest_query(
                cur, 'SELECT * FROM sets WHERE game_id=%s AND id<=%s ORDER BY id',
                (game_id, int(baseline['baseline_max_set_id'])),
            )
            cards_digest = _digest_query(
                cur, 'SELECT * FROM cards WHERE game_id=%s AND id<=%s ORDER BY id',
                (game_id, int(baseline['baseline_max_card_id'])),
            )
            prints_digest = _digest_query(
                cur,
                'SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id',
                (game_id, int(baseline['baseline_max_print_id'])),
            )
            economics: dict[str, int] = {}
            for table in ('prices', 'price_snapshots', 'price_daily_ohlc'):
                if _table_exists(cur, table):
                    cur.execute(f'SELECT count(*) FROM "{table}"')
                    economics[table] = int(cur.fetchone()[0])
            conn.rollback()
            result = {
                'status': 'pass',
                'sets_digest_unchanged': sets_digest == baseline['sets_digest'],
                'cards_digest_unchanged': cards_digest == baseline['cards_digest'],
                'preexisting_prints_digest_unchanged': prints_digest == baseline['preexisting_prints_digest'],
                'economics_rows': economics,
                'economics_untouched': all(value == 0 for value in economics.values()),
            }
            if not all(
                result[key]
                for key in (
                    'sets_digest_unchanged', 'cards_digest_unchanged',
                    'preexisting_prints_digest_unchanged', 'economics_untouched',
                )
            ):
                result['status'] = 'fail'
                raise AssertionError(json.dumps(result, sort_keys=True))
            return result
    finally:
        conn.close()


def main() -> int:
    production_url = os.getenv('PRODUCTION_DATABASE_URL_UNPOOLED') or os.getenv('PRODUCTION_DATABASE_URL')
    target_url = os.getenv('EPHEMERAL_DATABASE_URL')
    if not production_url or not target_url:
        raise RuntimeError('PRODUCTION_DATABASE_URL(_UNPOOLED) and EPHEMERAL_DATABASE_URL are required')
    baseline = seed_ephemeral_lean(production_url, target_url)
    print(json.dumps(baseline, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
