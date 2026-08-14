from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts.seed_multilingual_ephemeral_catalog import (
    CARD_COLUMNS,
    SET_COLUMNS,
    _hash_rows,
    _select_rows,
)
from app.scripts.validate_tcgdex_multilingual_ephemeral import _physical_remote

BASELINE_PATH = Path('/tmp/tcgdex-multilingual-production-before.json')
REPORT_PATH = Path('/tmp/tcgdex-multilingual-production-after.json')

BEFORE_REV = '20260810_32'
AFTER_REV = '20260814_34'
CANONICAL_SETS = 203
CANONICAL_CARDS = 21065
EN_PRINTS = 33757
ES_CARDS = 14046
JA_CARDS = 8159
ES_SETS = 102
JA_SETS = 70
FINAL_SETS = CANONICAL_SETS + JA_SETS
FINAL_CARDS = CANONICAL_CARDS + JA_CARDS
FINAL_PRINTS = EN_PRINTS + ES_CARDS + JA_CARDS
SET_HASH = '8ca65b393e8754f89bc9944ca79c8705589d6524137e8c2729646f816dc5d553'
CARD_HASH = 'f749f6a5249083f862d543f174ffbf15f7e3c2dc402a6a41cd59c714937e0ce2'

PRINT_COLUMNS = (
    'id', 'set_id', 'card_id', 'collector_number', 'language', 'rarity',
    'is_foil', 'variant', 'print_key', 'scryfall_id', 'tcgdex_id',
    'yugioh_id', 'riftbound_id', 'created_at',
)


def _url() -> str:
    value = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not value:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    if value.startswith('postgresql+psycopg2://'):
        return 'postgresql://' + value[len('postgresql+psycopg2://'):]
    if value.startswith('postgres://'):
        return 'postgresql://' + value[len('postgres://'):]
    return value


def _connect():
    conn = psycopg2.connect(
        _url(), connect_timeout=20,
        application_name='dontripit_multilingual_release_gate_readonly',
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _rows(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return list(cur.fetchall())


def _one(cur, sql: str, params: tuple = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _count(cur, sql: str, params: tuple = ()) -> int:
    return int(_one(cur, sql, params) or 0)


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _hash_query(cur, table: str, columns: tuple[str, ...], where: str, params: tuple) -> tuple[int, str]:
    cols = ', '.join(f'"{column}"' for column in columns)
    data = _rows(cur, f'SELECT {cols} FROM "{table}" WHERE {where} ORDER BY id', params)
    payload = json.dumps(
        [[_json(value) for value in row] for row in data],
        ensure_ascii=False, separators=(',', ':'), default=str,
    ).encode('utf-8')
    return len(data), hashlib.sha256(payload).hexdigest()


def _language_counts(cur, game_id: int) -> dict[str, int]:
    rows = _rows(
        cur,
        """
        SELECT lower(trim(coalesce(p.language, ''))) AS language, count(*)::bigint
        FROM prints p JOIN cards c ON c.id = p.card_id
        WHERE c.game_id = %s
        GROUP BY 1 ORDER BY 1
        """,
        (game_id,),
    )
    return {str(language): int(count) for language, count in rows}


def _external_ids(cur, table: str, source: str) -> tuple[int, set[str]]:
    values = [str(row[0]) for row in _rows(
        cur, f'SELECT external_id FROM "{table}" WHERE source = %s', (source,)
    )]
    return len(values), set(values)


def _source_coverage(cur, table: str, source: str, expected: set[str]) -> int:
    row_count, actual = _external_ids(cur, table, source)
    if row_count != len(expected) or actual != expected:
        raise RuntimeError(
            f'{table}/{source} mismatch: rows={row_count} unique={len(actual)} '
            f'expected={len(expected)} missing={sorted(expected-actual)[:20]} '
            f'extra={sorted(actual-expected)[:20]}'
        )
    return row_count


def _canonical(cur, game_id: int) -> tuple[int, int, str, str]:
    sets = _select_rows(
        cur, 'sets', SET_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,)
    )
    cards = _select_rows(
        cur, 'cards', CARD_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,)
    )
    return len(sets), len(cards), _hash_rows(sets), _hash_rows(cards)


def _game_id(cur) -> int:
    value = _one(cur, "SELECT id FROM games WHERE slug = 'pokemon'")
    if value is None:
        raise RuntimeError('Pokémon game row is missing')
    return int(value)


def snapshot_before(path: Path) -> dict[str, Any]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if str(_one(cur, 'SHOW transaction_read_only')).lower() != 'on':
                raise RuntimeError('Read-only guard failed')
            revision = str(_one(cur, 'SELECT version_num FROM alembic_version'))
            if revision != BEFORE_REV:
                raise RuntimeError(f'Expected {BEFORE_REV}, got {revision}')
            game_id = _game_id(cur)
            set_count, card_count, set_hash, card_hash = _canonical(cur, game_id)
            en_count, en_hash = _hash_query(
                cur, 'prints', PRINT_COLUMNS,
                "card_id IN (SELECT id FROM cards WHERE game_id = %s) "
                "AND lower(trim(coalesce(language, ''))) = 'en'",
                (game_id,),
            )
            languages = _language_counts(cur, game_id)
        conn.rollback()
    finally:
        conn.close()

    actual = {
        'canonical_sets': set_count,
        'canonical_cards': card_count,
        'sets_sha256': set_hash,
        'cards_sha256': card_hash,
        'en_prints': en_count,
        'language_counts': languages,
    }
    expected = {
        'canonical_sets': CANONICAL_SETS,
        'canonical_cards': CANONICAL_CARDS,
        'sets_sha256': SET_HASH,
        'cards_sha256': CARD_HASH,
        'en_prints': EN_PRINTS,
        'language_counts': {'en': EN_PRINTS},
    }
    if actual != expected:
        raise RuntimeError(f'Production moved before rollout: expected={expected!r} actual={actual!r}')

    report = {
        'status': 'pass', 'mode': 'before-write-read-only', 'database_writes': 0,
        'alembic_version': revision, 'pokemon_game_id': game_id,
        'en_print_identity_sha256': en_hash, **actual,
    }
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def validate_after(baseline_path: Path, report_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    if baseline.get('status') != 'pass' or baseline.get('database_writes') != 0:
        raise RuntimeError('Invalid before-write baseline')

    remote = {lang: _physical_remote(lang) for lang in ('es', 'ja')}
    remote_shape = {
        'es_cards': len(remote['es']['card_ids']), 'es_sets': len(remote['es']['set_ids']),
        'ja_cards': len(remote['ja']['card_ids']), 'ja_sets': len(remote['ja']['set_ids']),
    }
    expected_shape = {
        'es_cards': ES_CARDS, 'es_sets': ES_SETS,
        'ja_cards': JA_CARDS, 'ja_sets': JA_SETS,
    }
    if remote_shape != expected_shape:
        raise RuntimeError(f'TCGdex physical catalog moved: {remote_shape!r} != {expected_shape!r}')

    conn = _connect()
    try:
        with conn.cursor() as cur:
            if str(_one(cur, 'SHOW transaction_read_only')).lower() != 'on':
                raise RuntimeError('Postflight is not read-only')
            revision = str(_one(cur, 'SELECT version_num FROM alembic_version'))
            if revision != AFTER_REV:
                raise RuntimeError(f'Expected {AFTER_REV}, got {revision}')
            game_id = _game_id(cur)

            set_count, card_count, set_hash, card_hash = _canonical(cur, game_id)
            if (set_count, card_count, set_hash, card_hash) != (
                CANONICAL_SETS, CANONICAL_CARDS, SET_HASH, CARD_HASH,
            ):
                raise RuntimeError('Canonical Pokémon Set/Card identity changed')

            en_count, en_hash = _hash_query(
                cur, 'prints', PRINT_COLUMNS,
                "card_id IN (SELECT id FROM cards WHERE game_id = %s) "
                "AND lower(trim(coalesce(language, ''))) = 'en'",
                (game_id,),
            )
            if en_count != EN_PRINTS or en_hash != baseline['en_print_identity_sha256']:
                raise RuntimeError('Existing EN print identities changed during rollout')

            totals = {
                'sets': _count(cur, 'SELECT count(*) FROM sets WHERE game_id = %s', (game_id,)),
                'cards': _count(cur, 'SELECT count(*) FROM cards WHERE game_id = %s', (game_id,)),
                'prints': _count(
                    cur,
                    'SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s',
                    (game_id,),
                ),
            }
            expected_totals = {'sets': FINAL_SETS, 'cards': FINAL_CARDS, 'prints': FINAL_PRINTS}
            if totals != expected_totals:
                raise RuntimeError(f'Unexpected final cardinality: {totals!r} != {expected_totals!r}')

            languages = _language_counts(cur, game_id)
            expected_languages = {'en': EN_PRINTS, 'es': ES_CARDS, 'ja': JA_CARDS}
            if languages != expected_languages:
                raise RuntimeError(f'Unexpected language counts: {languages!r}')

            sources: dict[str, dict[str, int]] = {}
            for lang in ('es', 'ja'):
                source = f'tcgdex:{lang}'
                expected_cards = remote[lang]['card_ids']
                expected_sets = remote[lang]['set_ids']
                sources[lang] = {
                    'print_identifiers': _source_coverage(cur, 'print_identifiers', source, expected_cards),
                    'card_identifiers': _source_coverage(cur, 'card_identifiers', source, expected_cards),
                    'set_identifiers': _source_coverage(cur, 'set_identifiers', source, expected_sets),
                }
                localizations = _count(
                    cur,
                    "SELECT count(*) FROM print_localizations WHERE language=%s AND source='tcgdex'",
                    (lang,),
                )
                if localizations != len(expected_cards):
                    raise RuntimeError(f'{lang} localization count mismatch: {localizations}')
                sources[lang]['localizations'] = localizations

                global_ids = _count(
                    cur,
                    "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id "
                    "WHERE c.game_id=%s AND lower(p.language)=%s AND p.tcgdex_id IS NOT NULL",
                    (game_id, lang),
                )
                if global_ids:
                    raise RuntimeError(f'{lang} owns {global_ids} forbidden global tcgdex print IDs')

                for table in ('print_identifiers', 'card_identifiers', 'set_identifiers'):
                    duplicates = _count(
                        cur,
                        f"SELECT count(*) FROM (SELECT external_id FROM {table} WHERE source=%s "
                        "GROUP BY external_id HAVING count(*)>1) x",
                        (source,),
                    )
                    if duplicates:
                        raise RuntimeError(f'{lang}/{table} has {duplicates} duplicate external IDs')

            es_bad = _count(
                cur,
                "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id "
                "WHERE c.game_id=%s AND lower(p.language)='es' "
                "AND (c.tcgdex_id IS NULL OR s.tcgdex_id IS NULL)",
                (game_id,),
            )
            ja_bad = _count(
                cur,
                "SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id "
                "WHERE c.game_id=%s AND lower(p.language)='ja' "
                "AND (c.tcgdex_id IS NOT NULL OR s.tcgdex_id IS NOT NULL)",
                (game_id,),
            )
            if es_bad or ja_bad:
                raise RuntimeError(f'Physical identity semantics failed: es_bad={es_bad} ja_bad={ja_bad}')

            pocket_leaks: dict[str, list[str]] = {}
            for lang in ('es', 'ja'):
                _, actual_sets = _external_ids(cur, 'set_identifiers', f'tcgdex:{lang}')
                leaks = sorted(actual_sets & remote[lang]['pocket_set_ids'])
                pocket_leaks[lang] = leaks
                if leaks:
                    raise RuntimeError(f'Pokémon TCG Pocket leaked into {lang}: {leaks}')
        conn.rollback()
    finally:
        conn.close()

    report = {
        'status': 'pass', 'mode': 'post-write-strict-read-only', 'database_writes': 0,
        'personal_data_tables_queried': False, 'alembic_version': revision,
        'canonical_sets_unchanged': True, 'canonical_cards_unchanged': True,
        'existing_en_print_identities_unchanged': True,
        'en_print_identity_sha256': en_hash, 'totals': totals,
        'language_counts': languages, 'source_counts': sources,
        'pocket_leaks': pocket_leaks, 'certified_remote': remote_shape,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--snapshot-before', action='store_true')
    mode.add_argument('--validate-after', action='store_true')
    parser.add_argument('--baseline', type=Path, default=BASELINE_PATH)
    parser.add_argument('--report', type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    if args.snapshot_before:
        snapshot_before(args.baseline)
    else:
        validate_after(args.baseline, args.report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
