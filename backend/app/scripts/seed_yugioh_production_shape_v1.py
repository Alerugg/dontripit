from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import (
    _copy_filtered,
    _digest_query,
    _reset_sequence,
)

PRE_BASELINE_PATH = Path('/tmp/yugioh-production-shape-pre-migration.json')
EXPECTED_REVISION = '20260814_34'


def main() -> int:
    production_url = os.getenv('PRODUCTION_DATABASE_URL_UNPOOLED') or os.getenv('PRODUCTION_DATABASE_URL')
    target_url = os.getenv('EPHEMERAL_DATABASE_URL')
    if not production_url or not target_url:
        raise RuntimeError('production and ephemeral database URLs are required')
    if production_url == target_url:
        raise RuntimeError('Safety guard: production and ephemeral URLs are identical')
    if urlparse(target_url).hostname not in {'127.0.0.1', 'localhost'}:
        raise RuntimeError('Safety guard: ephemeral database must be local PostgreSQL')

    source = psycopg2.connect(
        production_url,
        connect_timeout=30,
        application_name='dontripit_ygo_schema34_clone_readonly',
    )
    target = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name='dontripit_ygo_schema34_clone_local',
    )
    source.set_session(readonly=True, autocommit=False)
    target.set_session(readonly=False, autocommit=False)
    copied = []
    try:
        with source.cursor() as src, target.cursor() as dst:
            src.execute('SHOW transaction_read_only')
            if str(src.fetchone()[0]).lower() != 'on':
                raise RuntimeError('Production read-only guard failed')
            src.execute('SELECT version_num FROM alembic_version LIMIT 1')
            source_revision = str(src.fetchone()[0])
            dst.execute('SELECT version_num FROM alembic_version LIMIT 1')
            target_revision = str(dst.fetchone()[0])
            if target_revision != EXPECTED_REVISION:
                raise RuntimeError(f'Expected ephemeral revision {EXPECTED_REVISION}, got {target_revision}')

            src.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            row = src.fetchone()
            if not row:
                raise RuntimeError('Production Yu-Gi-Oh game missing')
            game_id = int(row[0])
            dst.execute('SELECT count(*) FROM games')
            if int(dst.fetchone()[0]) != 0:
                raise RuntimeError('Ephemeral target is not empty')

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

            dst.execute('SELECT COALESCE(MAX(id),0), count(*) FROM sets WHERE game_id=%s', (game_id,))
            max_set_id, set_count = map(int, dst.fetchone())
            dst.execute('SELECT COALESCE(MAX(id),0), count(*) FROM cards WHERE game_id=%s', (game_id,))
            max_card_id, card_count = map(int, dst.fetchone())
            dst.execute(
                'SELECT COALESCE(MAX(p.id),0), count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s',
                (game_id,),
            )
            max_print_id, print_count = map(int, dst.fetchone())

            set_semantic_query = (
                'SELECT id,game_id,code,tcgdex_id,yugioh_id,riftbound_id,name,release_date,created_at '
                'FROM sets WHERE game_id=%s AND id<=%s ORDER BY id'
            )
            card_semantic_query = (
                'SELECT id,game_id,name,card_key,oracle_id,tcgdex_id,yugoprodeck_id,riftbound_id,created_at '
                'FROM cards WHERE game_id=%s AND id<=%s ORDER BY id'
            )
            print_semantic_query = (
                'SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id '
                'WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id'
            )
            baseline = {
                'mode': 'production-shape-schema34-readonly-clone',
                'production_writes': 0,
                'production_transaction_read_only': True,
                'economics_copied': False,
                'source_revision': source_revision,
                'target_revision_before_migration': target_revision,
                'game_id': game_id,
                'baseline_max_set_id': max_set_id,
                'baseline_max_card_id': max_card_id,
                'baseline_max_print_id': max_print_id,
                'counts': {'sets': set_count, 'cards': card_count, 'prints': print_count},
                'sets_semantic_digest': _digest_query(dst, set_semantic_query, (game_id, max_set_id)),
                'cards_semantic_digest': _digest_query(dst, card_semantic_query, (game_id, max_card_id)),
                'prints_semantic_digest': _digest_query(dst, print_semantic_query, (game_id, max_print_id)),
                'copied': copied,
            }
            target.commit()
            source.rollback()
            PRE_BASELINE_PATH.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            print(json.dumps(baseline, ensure_ascii=False, indent=2, default=str, sort_keys=True))
            return 0
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == '__main__':
    raise SystemExit(main())
