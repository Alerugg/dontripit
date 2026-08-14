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

EXPECTED_BEFORE_ALEMBIC = '20260810_32'
EXPECTED_AFTER_ALEMBIC = '20260814_34'
EXPECTED_CANONICAL_SETS = 203
EXPECTED_CANONICAL_CARDS = 21065
EXPECTED_EN_PRINTS = 33757
EXPECTED_ES_CARDS = 14046
EXPECTED_JA_CARDS = 8159
EXPECTED_ES_SETS = 102
EXPECTED_JA_SETS = 70
EXPECTED_FINAL_PRINTS = EXPECTED_EN_PRINTS + EXPECTED_ES_CARDS + EXPECTED_JA_CARDS
EXPECTED_FINAL_CARDS = EXPECTED_CANONICAL_CARDS + EXPECTED_JA_CARDS
EXPECTED_FINAL_SETS = EXPECTED_CANONICAL_SETS + EXPECTED_JA_SETS
EXPECTED_SET_HASH = '8ca65b393e8754f89bc9944ca79c8705589d6524137e8c2729646f816dc5d553'
EXPECTED_CARD_HASH = 'f749f6a5249083f862d543f174ffbf15f7e3c2dc402a6a41cd59c714937e0ce2'

PRINT_IDENTITY_COLUMNS = (
    'id',
    'set_id',
    'card_id',
    'collector_number',
    'language',
    'rarity',
    'is_foil',
    'variant',
    'print_key',
    'scryfall_id',
    'tcgdex_id',
    'yugioh_id',
    'riftbound_id',
    'created_at',
)


def _database_url() -> str:
    value = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not value:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    if value.startswith('postgresql+psycopg2://'):
        value = 'postgresql://' + value[len('postgresql+psycopg2://'):]
    elif value.startswith('postgres://'):
        value = 'postgresql://' + value[len('postgres://'):]
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _hash_query(cur, table: str, columns: tuple[str, ...], where: str, params: tuple = ()) -> tuple[int, str]:
    quoted = ', '.join(f'"{column}"' for column in columns)
    cur.execute(f'SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY id', params)
    rows = [[_json_value(value) for value in row] for row in cur.fetchall()]
    payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8')
    return len(rows), hashlib.sha256(payload).hexdigest()


def _scalar(cur, query: str, params: tuple = ()) -> int:
    cur.execute(query, params)
    return int(cur.fetchone()[0] or 0)


def _external_ids(cur, table: str, source: str) -> tuple[int, set[str]]:
    cur.execute(f'SELECT external_id FROM "{table}" WHERE source = %s', (source,))
    rows = [str(row[0]) for row in cur.fetchall()]
    return len(rows), set(rows)


def _connect_readonly():
    conn = psycopg2.connect(
        _database_url(),
        connect_timeout=20,
        application_name='dontripit_multilingual_production_release_gate_readonly',
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def snapshot_before(path: Path = BASELINE_PATH) -> dict[str, Any]:
    conn = _connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only')
            read_only = str(cur.fetchone()[0]).lower()
            if read_only != 'on':
                raise RuntimeError(f'Read-only guard failed: {read_only!r}')

            cur.execute('SELECT version_num FROM alembic_version')
            alembic = str(cur.fetchone()[0])
            if alembic != EXPECTED_BEFORE_ALEMBIC:
                raise RuntimeError(f'Expected Alembic {EXPECTED_BEFORE_ALEMBIC}, got {alembic}')

            cur.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            game = cur.fetchone()
            if game is None:
                raise RuntimeError('Pokémon game row missing')
            game_id = int(game[0])

            sets = _select_rows(cur, 'sets', SET_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,))
            cards = _select_rows(cur, 'cards', CARD_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,))
            set_hash = _hash_rows(sets)
            card_hash = _hash_rows(cards)

            en_count, en_hash = _hash_query(
                cur,
                'prints',
                PRINT_IDENTITY_COLUMNS,
                "card_id IN (SELECT id FROM cards WHERE game_id = %s) AND lower(trim(coalesce(language, ''))) = 'en'",
                (game_id,),
            )
            language_counts = dict(
                (str(language), int(count))
                for language, count in cur.execute(
                    """
                    SELECT lower(trim(coalesce(p.language, ''))) AS language, count(*)::bigint
                    FROM prints p
                    JOIN cards c ON c.id = p.card_id
                    WHERE c.game_id = %s
                    GROUP BY 1 ORDER BY 1
                    """,
                    (game_id,),
                ).fetchall()
            )

        conn.rollback()
    finally:
        conn.close()

    expected = {
        'canonical_sets': EXPECTED_CANONICAL_SETS,
        'canonical_cards': EXPECTED_CANONICAL_CARDS,
        'set_hash': EXPECTED_SET_HASH,
        'card_hash': EXPECTED_CARD_HASH,
        'en_print_count': EXPECTED_EN_PRINTS,
        'language_counts': {'en': EXPECTED_EN_PRINTS},
    }
    actual = {
        'canonical_sets': len(sets),
        'canonical_cards': len(cards),
        'set_hash': set_hash,
        'card_hash': card_hash,
        'en_print_count': en_count,
        'language_counts': language_counts,
    }
    if actual != expected:
        raise RuntimeError(f'Production baseline changed before write: expected={expected!r} actual={actual!r}')

    report = {
        'status': 'pass',
        'mode': 'before-write-read-only-snapshot',
        'database_writes': 0,
        'alembic_version': alembic,
        'pokemon_game_id': game_id,
        **actual,
        'en_print_identity_sha256': en_hash,
    }
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def _assert_exact_source_coverage(cur, *, table: str, source: str, expected: set[str]) -> int:
    row_count, actual = _external_ids(cur, table, source)
    if row_count != len(expected) or actual != expected:
        missing = sorted(expected - actual)[:25]
        extra = sorted(actual - expected)[:25]
        raise RuntimeError(
            f'{table}/{source} coverage mismatch: expected={len(expected)} rows={row_count} '
            f'unique={len(actual)} missing={missing} extra={extra}'
        )
    return row_count


def validate_after(baseline_path: Path = BASELINE_PATH, report_path: Path = REPORT_PATH) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    if baseline.get('status') != 'pass' or baseline.get('database_writes') != 0:
        raise RuntimeError('Invalid before-write baseline evidence')

    remote = {language: _physical_remote(language) for language in ('es', 'ja')}
    if len(remote['es']['card_ids']) != EXPECTED_ES_CARDS or len(remote['es']['set_ids']) != EXPECTED_ES_SETS:
        raise RuntimeError(
            f'ES remote catalog moved after certification: cards={len(remote["es"]["card_ids"])} '
            f'sets={len(remote["es"]["set_ids"])}'
        )
    if len(remote['ja']['card_ids']) != EXPECTED_JA_CARDS or len(remote['ja']['set_ids']) != EXPECTED_JA_SETS:
        raise RuntimeError(
            f'JA remote catalog moved after certification: cards={len(remote["ja"]["card_ids"])} '
            f'sets={len(remote["ja"]["set_ids"])}'
        )

    conn = _connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only')
            if str(cur.fetchone()[0]).lower() != 'on':
                raise RuntimeError('Postflight is not read-only')

            cur.execute('SELECT version_num FROM alembic_version')
            alembic = str(cur.fetchone()[0])
            if alembic != EXPECTED_AFTER_ALEMBIC:
                raise RuntimeError(f'Expected Alembic {EXPECTED_AFTER_ALEMBIC}, got {alembic}')

            cur.execute("SELECT id FROM games WHERE slug = 'pokemon'")
            game_id = int(cur.fetchone()[0])

            canonical_sets = _select_rows(cur, 'sets', SET_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,))
            canonical_cards = _select_rows(cur, 'cards', CARD_COLUMNS, 'game_id = %s AND tcgdex_id IS NOT NULL', (game_id,))
            set_hash = _hash_rows(canonical_sets)
            card_hash = _hash_rows(canonical_cards)
            if len(canonical_sets) != EXPECTED_CANONICAL_SETS or set_hash != EXPECTED_SET_HASH:
                raise RuntimeError('Canonical Pokémon Set identity changed during rollout')
            if len(canonical_cards) != EXPECTED_CANONICAL_CARDS or card_hash != EXPECTED_CARD_HASH:
                raise RuntimeError('Canonical Pokémon Card identity changed during rollout')

            en_count, en_hash = _hash_query(
                cur,
                'prints',
                PRINT_IDENTITY_COLUMNS,
                "card_id IN (SELECT id FROM cards WHERE game_id = %s) AND lower(trim(coalesce(language, ''))) = 'en'",
                (game_id,),
            )
            if en_count != EXPECTED_EN_PRINTS or en_hash != baseline['en_print_identity_sha256']:
                raise RuntimeError(
                    f'Existing EN print identities changed: count={en_count} hash={en_hash} '
                    f'expected_count={EXPECTED_EN_PRINTS} expected_hash={baseline["en_print_identity_sha256"]}'
                )

            total_sets = _scalar(cur, 'SELECT count(*) FROM sets WHERE game_id = %s', (game_id,))
            total_cards = _scalar(cur, 'SELECT count(*) FROM cards WHERE game_id = %s', (game_id,))
            total_prints = _scalar(
                cur,
                'SELECT count(*) FROM prints p JOIN cards c ON c.id = p.card_id WHERE c.game_id = %s',
                (game_id,),
            )
            if (total_sets, total_cards, total_prints) != (
                EXPECTED_FINAL_SETS,
                EXPECTED_FINAL_CARDS,
                EXPECTED_FINAL_PRINTS,
            ):
                raise RuntimeError(
                    f'Unexpected final Pokémon cardinality: sets={total_sets} cards={total_cards} prints={total_prints}'
                )

            language_counts = dict(
                (str(language), int(count))
                for language, count in cur.execute(
                    """
                    SELECT lower(trim(coalesce(p.language, ''))) AS language, count(*)::bigint
                    FROM prints p JOIN cards c ON c.id = p.card_id
                    WHERE c.game_id = %s GROUP BY 1 ORDER BY 1
                    """,
                    (game_id,),
                ).fetchall()
            )
            expected_languages = {'en': EXPECTED_EN_PRINTS, 'es': EXPECTED_ES_CARDS, 'ja': EXPECTED_JA_CARDS}
            if language_counts != expected_languages:
                raise RuntimeError(f'Unexpected final Pokémon language counts: {language_counts!r}')

            source_counts: dict[str, dict[str, int]] = {}
            for language in ('es', 'ja'):
                source = f'tcgdex:{language}'
                expected_cards = remote[language]['card_ids']
                expected_sets = remote[language]['set_ids']
                source_counts[language] = {
                    'print_identifiers': _assert_exact_source_coverage(
                        cur, table='print_identifiers', source=source, expected=expected_cards
                    ),
                    'card_identifiers': _assert_exact_source_coverage(
                        cur, table='card_identifiers', source=source, expected=expected_cards
                    ),
                    'set_identifiers': _assert_exact_source_coverage(
                        cur, table='set_identifiers', source=source, expected=expected_sets
                    ),
                }

                localization_count = _scalar(
                    cur,
                    "SELECT count(*) FROM print_localizations WHERE language = %s AND source = 'tcgdex'",
                    (language,),
                )
                if localization_count != len(expected_cards):
                    raise RuntimeError(
                        f'{language} localization cardinality mismatch: {localization_count} != {len(expected_cards)}'
                    )
                source_counts[language]['localizations'] = localization_count

                non_en_global = _scalar(
                    cur,
                    """
                    SELECT count(*) FROM prints p
                    JOIN cards c ON c.id = p.card_id
                    WHERE c.game_id = %s AND lower(p.language) = %s AND p.tcgdex_id IS NOT NULL
                    """,
                    (game_id, language),
                )
                if non_en_global:
                    raise RuntimeError(f'{language} prints leaked into global tcgdex_id identity: {non_en_global}')

                duplicate_print_identifiers = _scalar(
                    cur,
                    """
                    SELECT count(*) FROM (
                      SELECT external_id FROM print_identifiers WHERE source = %s
                      GROUP BY external_id HAVING count(*) > 1
                    ) x
                    """,
                    (source,),
                )
                duplicate_card_identifiers = _scalar(
                    cur,
                    """
                    SELECT count(*) FROM (
                      SELECT external_id FROM card_identifiers WHERE source = %s
                      GROUP BY external_id HAVING count(*) > 1
                    ) x
                    """,
                    (source,),
                )
                duplicate_set_identifiers = _scalar(
                    cur,
                    """
                    SELECT count(*) FROM (
                      SELECT external_id FROM set_identifiers WHERE source = %s
                      GROUP BY external_id HAVING count(*) > 1
                    ) x
                    """,
                    (source,),
                )
                duplicate_localizations = _scalar(
                    cur,
                    """
                    SELECT count(*) FROM (
                      SELECT print_id, language FROM print_localizations WHERE language = %s
                      GROUP BY print_id, language HAVING count(*) > 1
                    ) x
                    """,
                    (language,),
                )
                if any((duplicate_print_identifiers, duplicate_card_identifiers, duplicate_set_identifiers, duplicate_localizations)):
                    raise RuntimeError(
                        f'{language} duplicate identities detected: prints={duplicate_print_identifiers} '
                        f'cards={duplicate_card_identifiers} sets={duplicate_set_identifiers} '
                        f'localizations={duplicate_localizations}'
                    )

            es_outside_canonical = _scalar(
                cur,
                """
                SELECT count(*) FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN sets s ON s.id = p.set_id
                WHERE lower(p.language) = 'es' AND (c.tcgdex_id IS NULL OR s.tcgdex_id IS NULL)
                """,
            )
            if es_outside_canonical:
                raise RuntimeError(f'ES created {es_outside_canonical} prints outside canonical EN identity')

            ja_global_identity = _scalar(
                cur,
                """
                SELECT count(*) FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN sets s ON s.id = p.set_id
                WHERE lower(p.language) = 'ja' AND (c.tcgdex_id IS NOT NULL OR s.tcgdex_id IS NOT NULL)
                """,
            )
            if ja_global_identity:
                raise RuntimeError(f'JA leaked {ja_global_identity} regional prints into global identity')

            pocket_leaks: dict[str, list[str]] = {}
            for language in ('es', 'ja'):
                _, actual_sets = _external_ids(cur, 'set_identifiers', f'tcgdex:{language}')
                leaks = sorted(actual_sets & remote[language]['pocket_set_ids'])
                pocket_leaks[language] = leaks
                if leaks:
                    raise RuntimeError(f'Pokémon TCG Pocket leaked into {language}: {leaks}')

        conn.rollback()
    finally:
        conn.close()

    report = {
        'status': 'pass',
        'mode': 'post-write-strict-read-only-certification',
        'database_writes': 0,
        'personal_data_tables_queried': False,
        'alembic_version': alembic,
        'canonical_sets_unchanged': True,
        'canonical_cards_unchanged': True,
        'existing_en_print_identities_unchanged': True,
        'en_print_identity_sha256': en_hash,
        'pokemon_sets': total_sets,
        'pokemon_cards': total_cards,
        'pokemon_prints': total_prints,
        'language_counts': language_counts,
        'source_counts': source_counts,
        'pocket_leaks': pocket_leaks,
        'certified_remote': {
            'es_cards': len(remote['es']['card_ids']),
            'es_sets': len(remote['es']['set_ids']),
            'ja_cards': len(remote['ja']['card_ids']),
            'ja_sets': len(remote['ja']['set_ids']),
        },
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
