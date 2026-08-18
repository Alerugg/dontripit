from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2

from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import (
    BASELINE_PATH,
    _digest_query,
    _install_guards,
)
from app.scripts.seed_yugioh_production_shape_v1 import PRE_BASELINE_PATH

EXPECTED_HEAD = '20260815_36'


def main() -> int:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL is required')
    pre = json.loads(PRE_BASELINE_PATH.read_text(encoding='utf-8'))
    game_id = int(pre['game_id'])

    conn = psycopg2.connect(
        target_url,
        connect_timeout=30,
        application_name='dontripit_ygo_prepare_ephemeral_head',
    )
    conn.set_session(readonly=False, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version LIMIT 1')
            revision = str(cur.fetchone()[0])
            if revision != EXPECTED_HEAD:
                raise RuntimeError(f'Expected head {EXPECTED_HEAD}, got {revision}')

            cur.execute('SELECT count(*) FROM sets WHERE game_id=%s AND coalesce(region,\'global\')<>\'global\'', (game_id,))
            non_global_preexisting = int(cur.fetchone()[0])
            if non_global_preexisting != 0:
                raise AssertionError(f'Pre-existing sets did not migrate to global region: {non_global_preexisting}')

            max_set = int(pre['baseline_max_set_id'])
            max_card = int(pre['baseline_max_card_id'])
            max_print = int(pre['baseline_max_print_id'])
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
            semantic = {
                'sets_unchanged': _digest_query(cur, set_semantic_query, (game_id, max_set)) == pre['sets_semantic_digest'],
                'cards_unchanged': _digest_query(cur, card_semantic_query, (game_id, max_card)) == pre['cards_semantic_digest'],
                'prints_unchanged': _digest_query(cur, print_semantic_query, (game_id, max_print)) == pre['prints_semantic_digest'],
            }
            if not all(semantic.values()):
                raise AssertionError(f'Migration changed pre-existing semantic data: {semantic}')

            cur.execute('SELECT count(*) FROM sets WHERE game_id=%s', (game_id,))
            set_count = int(cur.fetchone()[0])
            cur.execute('SELECT count(*) FROM cards WHERE game_id=%s', (game_id,))
            card_count = int(cur.fetchone()[0])
            cur.execute(
                'SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s',
                (game_id,),
            )
            print_count = int(cur.fetchone()[0])
            counts = {'sets': set_count, 'cards': card_count, 'prints': print_count}
            if counts != pre['counts']:
                raise AssertionError(f'Migration changed row counts: before={pre["counts"]} after={counts}')

            cur.execute(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=current_schema() AND t.relname='prints'
                  AND c.conname='uq_prints_set_number_language_is_foil_variant'
                LIMIT 1
                """
            )
            constraint = cur.fetchone()
            constraint_def = str(constraint[0]) if constraint else ''
            if 'card_id' not in constraint_def.lower():
                raise AssertionError(f'Print identity is not card-scoped: {constraint_def!r}')

            guards = _install_guards(cur, game_id)
            post = {
                **pre,
                'mode': 'ephemeral-production-shape-migrated-and-guarded',
                'target_revision_after_migration': revision,
                'migration_35_region_applied': True,
                'migration_36_card_scoped_print_identity_applied': True,
                'preexisting_regions_global': True,
                'migration_semantic_invariants': semantic,
                'print_identity_constraint': constraint_def,
                'sets_digest': _digest_query(
                    cur, 'SELECT * FROM sets WHERE game_id=%s AND id<=%s ORDER BY id', (game_id, max_set)
                ),
                'cards_digest': _digest_query(
                    cur, 'SELECT * FROM cards WHERE game_id=%s AND id<=%s ORDER BY id', (game_id, max_card)
                ),
                'preexisting_prints_digest': _digest_query(
                    cur,
                    'SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s AND p.id<=%s ORDER BY p.id',
                    (game_id, max_print),
                ),
                'guards': guards,
                'lean_clone': True,
                'economics_copied': False,
            }
            conn.commit()
            BASELINE_PATH.write_text(
                json.dumps(post, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            print(json.dumps(post, ensure_ascii=False, indent=2, default=str, sort_keys=True))
            return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
