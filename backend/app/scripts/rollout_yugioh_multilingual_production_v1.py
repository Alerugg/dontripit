from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts import certify_yugioh_multilingual_ephemeral_v1 as v1
from app.scripts import certify_yugioh_multilingual_ephemeral_v2 as v2
from app.scripts.certify_yugioh_multilingual_ephemeral_v3 import STALE_BASELINE_MINIMUMS
from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import _digest_query

EXPECTED_SCHEMA = '20260815_36'
ACK = 'YGO_ES_JA_ROLLOUT_V1'
TARGET_TABLES = (
    'sets',
    'prints',
    'print_localizations',
    'print_images',
    'catalog_releases',
    'print_releases',
)
ECONOMICS_TABLES = ('prices', 'price_snapshots', 'price_daily_ohlc')


class _WriteTransactionView:
    """Let the certified writer operate inside our outer transaction.

    apply_plan() owns commit/close in certification mode. Production must not
    expose either until post-write validation and idempotence have passed.
    """

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return None

    def rollback(self):
        self._conn.rollback()

    def close(self):
        return None


class _ReadTransactionView:
    """Read the uncommitted rollout through the same outer transaction."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def set_session(self, *args, **kwargs):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def _production_url() -> str:
    url = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    return url


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s)",
        (table,),
    )
    return bool(cur.fetchone()[0])


def _economics_fingerprints(cur) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in ECONOMICS_TABLES:
        if not _table_exists(cur, table):
            continue
        # Server-side row-content fingerprint: catches UPDATE as well as row-count changes
        # without materializing the potentially large economics tables in the runner.
        cur.execute(
            f'''SELECT COUNT(*)::bigint,
                       COALESCE(SUM(hashtextextended(row_to_json(t)::text, 0)::numeric), 0)::text
                FROM "{table}" t'''
        )
        count, checksum = cur.fetchone()
        result[table] = {'rows': int(count), 'row_hash_sum': str(checksum)}
    return result


def _non_ygo_counts(cur, game_id: int) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    cur.execute(
        '''
        SELECT g.slug,
               COUNT(DISTINCT s.id)::bigint,
               COUNT(DISTINCT c.id)::bigint,
               COUNT(DISTINCT p.id)::bigint
        FROM games g
        LEFT JOIN sets s ON s.game_id=g.id
        LEFT JOIN cards c ON c.game_id=g.id
        LEFT JOIN prints p ON p.card_id=c.id
        WHERE g.id<>%s
        GROUP BY g.slug ORDER BY g.slug
        ''',
        (game_id,),
    )
    for slug, sets, cards, prints in cur.fetchall():
        rows[str(slug)] = {'sets': int(sets), 'cards': int(cards), 'prints': int(prints)}
    return rows


def _snapshot(cur, *, game_id: int, baseline: dict[str, Any]) -> dict[str, Any]:
    max_set = int(baseline['baseline_max_set_id'])
    max_card = int(baseline['baseline_max_card_id'])
    max_print = int(baseline['baseline_max_print_id'])

    cur.execute('SELECT COALESCE(MAX(id),0),COUNT(*) FROM sets WHERE game_id=%s', (game_id,))
    current_max_set, set_count = cur.fetchone()
    cur.execute('SELECT COALESCE(MAX(id),0),COUNT(*) FROM cards WHERE game_id=%s', (game_id,))
    current_max_card, card_count = cur.fetchone()
    cur.execute(
        'SELECT COALESCE(MAX(p.id),0),COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s',
        (game_id,),
    )
    current_max_print, print_count = cur.fetchone()

    return {
        'max_ids': {
            'sets': int(current_max_set or 0),
            'cards': int(current_max_card or 0),
            'prints': int(current_max_print or 0),
        },
        'counts': {'sets': int(set_count), 'cards': int(card_count), 'prints': int(print_count)},
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
        'economics': _economics_fingerprints(cur),
        'non_ygo_counts': _non_ygo_counts(cur, game_id),
    }


def _assert_live_matches_preflight(snapshot: dict[str, Any], baseline: dict[str, Any]) -> None:
    expected_max = {
        'sets': int(baseline['baseline_max_set_id']),
        'cards': int(baseline['baseline_max_card_id']),
        'prints': int(baseline['baseline_max_print_id']),
    }
    if snapshot['max_ids'] != expected_max:
        raise AssertionError(f'Production changed after preflight: max_ids {snapshot["max_ids"]} != {expected_max}')
    expected_counts = {key: int(value) for key, value in (baseline.get('counts') or {}).items()}
    if expected_counts and snapshot['counts'] != expected_counts:
        raise AssertionError(f'Production changed after preflight: counts {snapshot["counts"]} != {expected_counts}')
    for key in ('sets_digest', 'cards_digest', 'preexisting_prints_digest'):
        if snapshot[key] != baseline[key]:
            raise AssertionError(f'Production changed after preflight: {key} mismatch')


def _assert_invariants(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    checks = {
        'cards_digest_unchanged': before['cards_digest'] == after['cards_digest'],
        'preexisting_sets_digest_unchanged': before['sets_digest'] == after['sets_digest'],
        'preexisting_prints_digest_unchanged': before['preexisting_prints_digest'] == after['preexisting_prints_digest'],
        'economics_untouched': before['economics'] == after['economics'],
        'non_ygo_catalog_untouched': before['non_ygo_counts'] == after['non_ygo_counts'],
        'logical_card_count_unchanged': before['counts']['cards'] == after['counts']['cards'],
    }
    return {
        'status': 'pass' if all(checks.values()) else 'fail',
        **checks,
        'economics_rows': {k: v['rows'] for k, v in after['economics'].items()},
        'before_counts': before['counts'],
        'after_counts': after['counts'],
    }


def _assert_readiness(readiness: dict[str, Any], fresh: dict[str, Any]) -> None:
    assert readiness.get('status') == 'pass', readiness
    assert readiness.get('controlled_rollout_candidate_ready') is True, readiness
    assert readiness.get('automatic_production_write_authorized') is False, readiness
    assert readiness.get('production_writes') == 0, readiness
    assert readiness.get('gates') and all(readiness['gates'].values()), readiness
    assert fresh.get('status') == 'pass', fresh
    assert fresh.get('fresh_source_certified') is True, fresh
    assert fresh.get('production_writes') == 0, fresh
    assert fresh.get('pass2_zero_writes') is True, fresh
    assert fresh.get('source_freshness', {}).get('status') == 'current_enough', fresh


def _assert_plan_matches_certificate(plan: dict[str, Any], fresh: dict[str, Any]) -> None:
    expected = fresh.get('plan_summary') or {}
    assert plan.get('structural_pass') is True, plan.get('structural_gates')
    assert plan.get('rollout_freshness_pass') is True, plan.get('source_freshness')
    assert plan.get('source_freshness', {}).get('status') == 'current_enough', plan.get('source_freshness')
    for target in ('es', 'ja'):
        live = plan['targets'][target]
        cert = expected['targets'][target]
        actual = {
            'canonical_prints': int(live['canonical_prints']),
            'materializable_prints': len(live['materializable']),
            'retained_missing_card': len(live['retained_missing_card']),
        }
        certified = {key: int(cert[key]) for key in actual}
        if actual != certified:
            raise AssertionError(f'{target} live plan changed since preflight: {actual} != {certified}')
        minimum = STALE_BASELINE_MINIMUMS[target]
        assert actual['canonical_prints'] >= minimum['canonical_prints'], actual
        assert actual['materializable_prints'] >= minimum['materializable_prints'], actual
    if int(plan['source_memberships']) != int(expected['source_memberships']):
        raise AssertionError('source membership count changed since preflight')
    if int(plan['source_memberships']) < STALE_BASELINE_MINIMUMS['source_memberships']:
        raise AssertionError('source membership count regressed')
    if plan.get('localized_name_coverage') != expected.get('localized_name_coverage'):
        raise AssertionError('localized name coverage changed since preflight')


def _call_apply_in_outer_transaction(plan: dict[str, Any], url: str, real_conn) -> dict[str, Any]:
    original = v1.psycopg2.connect
    try:
        v1.psycopg2.connect = lambda *args, **kwargs: _WriteTransactionView(real_conn)
        return v1.apply_plan(plan, url)
    finally:
        v1.psycopg2.connect = original


def _call_validate_in_outer_transaction(
    plan: dict[str, Any], url: str, real_conn, live_invariants: dict[str, Any]
) -> dict[str, Any]:
    original_connect = v1.psycopg2.connect
    original_lean = v1.validate_lean_invariants
    try:
        v1.psycopg2.connect = lambda *args, **kwargs: _ReadTransactionView(real_conn)
        v1.validate_lean_invariants = lambda _target_url, _baseline: live_invariants
        return v1.validate(plan, url, {})
    finally:
        v1.psycopg2.connect = original_connect
        v1.validate_lean_invariants = original_lean


def run(
    *,
    input_dir: Path,
    yaml_cards: Path,
    yaml_meta: Path,
    fresh_certificate: Path,
    readiness_path: Path,
    output: Path,
    apply: bool,
) -> dict[str, Any]:
    url = _production_url()
    fresh = _load_json(fresh_certificate)
    readiness = _load_json(readiness_path)
    _assert_readiness(readiness, fresh)
    baseline = fresh['baseline']

    # Build the plan against live production before taking write locks. No writes.
    yaml_source_meta = _load_json(yaml_meta)
    plan = v1.build_plan(input_dir, url)
    plan = v2.enrich_plan_with_yaml_names(
        plan,
        root=input_dir,
        yaml_cards_path=yaml_cards,
        yaml_source_meta=yaml_source_meta,
    )
    _assert_plan_matches_certificate(plan, fresh)

    if not apply:
        report = {
            'status': 'pass',
            'mode': 'production-dry-run-no-writes',
            'production_writes': 0,
            'schema_required': EXPECTED_SCHEMA,
            'plan': {
                'es': len(plan['targets']['es']['materializable']),
                'ja': len(plan['targets']['ja']['materializable']),
                'source_memberships': plan['source_memberships'],
                'source_image_relations': plan['source_image_relations'],
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return report

    if os.getenv('YUGIOH_PRODUCTION_ROLLOUT_ACK') != ACK:
        raise RuntimeError(f'Production rollout requires YUGIOH_PRODUCTION_ROLLOUT_ACK={ACK}')

    conn = psycopg2.connect(url, connect_timeout=30, application_name='dontripit_ygo_multilingual_production_v1')
    conn.set_session(readonly=False, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version LIMIT 1')
            revision = str(cur.fetchone()[0])
            if revision != EXPECTED_SCHEMA:
                raise RuntimeError(f'Expected production schema {EXPECTED_SCHEMA}, got {revision}')
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise RuntimeError('Yu-Gi-Oh game row missing')
            game_id = int(row[0])

            # One rollout at a time and no concurrent writes to the six target tables.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit:yugioh-multilingual-rollout-v1'))")
            cur.execute('LOCK TABLE ' + ','.join(TARGET_TABLES) + ' IN SHARE ROW EXCLUSIVE MODE')

            before = _snapshot(cur, game_id=game_id, baseline=baseline)
            _assert_live_matches_preflight(before, baseline)

        pass1 = _call_apply_in_outer_transaction(plan, url, conn)

        with conn.cursor() as cur:
            after_pass1 = _snapshot(cur, game_id=game_id, baseline=baseline)
        invariants = _assert_invariants(before, after_pass1)
        if invariants['status'] != 'pass':
            raise AssertionError(json.dumps(invariants, sort_keys=True))

        validation = _call_validate_in_outer_transaction(plan, url, conn, invariants)
        if validation.get('status') != 'pass' or not all((validation.get('gates') or {}).values()):
            raise AssertionError(json.dumps(validation, sort_keys=True, default=str))

        pass2 = _call_apply_in_outer_transaction(plan, url, conn)
        if pass2.get('total_writes') != 0:
            raise AssertionError(f'Idempotence failure: second pass wrote {pass2}')

        with conn.cursor() as cur:
            final_snapshot = _snapshot(cur, game_id=game_id, baseline=baseline)
        final_invariants = _assert_invariants(before, final_snapshot)
        if final_invariants['status'] != 'pass':
            raise AssertionError(json.dumps(final_invariants, sort_keys=True))

        report = {
            'status': 'pass',
            'mode': 'atomic-production-yugioh-multilingual-rollout-v1',
            'schema': EXPECTED_SCHEMA,
            'preflight_status': readiness['status'],
            'fresh_source_certified': fresh['fresh_source_certified'],
            'pass1': pass1,
            'validation': validation,
            'pass2': pass2,
            'pass2_zero_writes': True,
            'invariants': final_invariants,
            'transaction_policy': 'single transaction; target-table write locks; commit only after validation+idempotence',
            'logical_cards_created': 0,
            'economics_untouched': final_invariants['economics_untouched'],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n', encoding='utf-8')
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--yaml-yugi-cards', type=Path, required=True)
    parser.add_argument('--yaml-yugi-meta', type=Path, required=True)
    parser.add_argument('--fresh-certificate', type=Path, required=True)
    parser.add_argument('--readiness', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = run(
        input_dir=args.input_dir,
        yaml_cards=args.yaml_yugi_cards,
        yaml_meta=args.yaml_yugi_meta,
        fresh_certificate=args.fresh_certificate,
        readiness_path=args.readiness,
        output=args.output,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return 0 if report.get('status') == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
