from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_values

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)

REPORT_PATH = Path('/tmp/tcgdex-multilingual-bulk-report.json')
EXPECTED_ES_CARDS = 14046
EXPECTED_JA_CARDS = 8159
EXPECTED_ES_SETS = 102
EXPECTED_JA_SETS = 70
EXPECTED_CANONICAL_SETS = 203
EXPECTED_CANONICAL_CARDS = 21065
EXPECTED_PRODUCTION_EN_PRINTS = 33757
EXPECTED_REVISION = '20260814_34'
BATCH = 1000


def _db_url() -> str:
    value = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not value:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    if value.startswith('postgresql+psycopg2://'):
        return 'postgresql://' + value[len('postgresql+psycopg2://'):]
    if value.startswith('postgres://'):
        return 'postgresql://' + value[len('postgres://'):]
    return value


def _bulk(cur, sql: str, rows: list[tuple[Any, ...]], *, page_size: int = BATCH) -> None:
    if rows:
        execute_values(cur, sql, rows, page_size=page_size)


def _one(cur, sql: str, params: tuple = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _rows(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return list(cur.fetchall())


def _load_language(connector: PhysicalMultilingualTcgdexPokemonConnector, language: str) -> list[dict]:
    started = time.perf_counter()
    loaded = connector.load(None, fixture=False, limit=None, set_id=None, lang=language)
    normalized = [connector.normalize(payload, lang=language) for _path, payload, _checksum in loaded]
    elapsed = round(time.perf_counter() - started, 2)
    print(json.dumps({'phase': 'remote-load', 'language': language, 'rows': len(normalized), 'seconds': elapsed}))
    return normalized


def _physical_shape(rows: list[dict]) -> tuple[int, int, set[str], set[str]]:
    card_ids = {
        str((row.get('card') or {}).get('id') or '').strip()
        for row in rows
        if str((row.get('card') or {}).get('id') or '').strip()
    }
    set_ids = {
        str((row.get('set') or {}).get('tcgdex_id') or '').strip()
        for row in rows
        if str((row.get('set') or {}).get('tcgdex_id') or '').strip()
    }
    return len(rows), len(set_ids), card_ids, set_ids


def _validate_remote(es: list[dict], ja: list[dict]) -> dict:
    es_rows, es_sets, es_cards, es_set_ids = _physical_shape(es)
    ja_rows, ja_sets, ja_cards, ja_set_ids = _physical_shape(ja)
    actual = {
        'es_rows': es_rows,
        'es_unique_cards': len(es_cards),
        'es_sets': es_sets,
        'ja_rows': ja_rows,
        'ja_unique_cards': len(ja_cards),
        'ja_sets': ja_sets,
    }
    expected = {
        'es_rows': EXPECTED_ES_CARDS,
        'es_unique_cards': EXPECTED_ES_CARDS,
        'es_sets': EXPECTED_ES_SETS,
        'ja_rows': EXPECTED_JA_CARDS,
        'ja_unique_cards': EXPECTED_JA_CARDS,
        'ja_sets': EXPECTED_JA_SETS,
    }
    if actual != expected:
        raise RuntimeError(f'Certified TCGdex physical shape moved: expected={expected!r} actual={actual!r}')
    return {**actual, 'es_set_ids': sorted(es_set_ids), 'ja_set_ids': sorted(ja_set_ids)}


def _release_date(value: Any):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _preflight(cur) -> dict:
    revision = str(_one(cur, 'SELECT version_num FROM alembic_version'))
    if revision != EXPECTED_REVISION:
        raise RuntimeError(f'Expected Alembic {EXPECTED_REVISION}, got {revision}')

    game_id = _one(cur, "SELECT id FROM games WHERE slug='pokemon'")
    if game_id is None:
        raise RuntimeError('Pokémon game row missing')
    game_id = int(game_id)

    canonical_sets = int(_one(cur, 'SELECT count(*) FROM sets WHERE game_id=%s AND tcgdex_id IS NOT NULL', (game_id,)) or 0)
    canonical_cards = int(_one(cur, 'SELECT count(*) FROM cards WHERE game_id=%s AND tcgdex_id IS NOT NULL', (game_id,)) or 0)
    if canonical_sets != EXPECTED_CANONICAL_SETS or canonical_cards != EXPECTED_CANONICAL_CARDS:
        raise RuntimeError(f'Canonical baseline changed: sets={canonical_sets} cards={canonical_cards}')

    language_counts = {
        str(lang): int(count)
        for lang, count in _rows(
            cur,
            """
            SELECT lower(trim(coalesce(p.language,''))), count(*)::bigint
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s GROUP BY 1 ORDER BY 1
            """,
            (game_id,),
        )
    }
    if any(language_counts.get(lang, 0) for lang in ('es', 'ja')):
        raise RuntimeError(f'Existing ES/JA data makes bulk first-write unsafe: {language_counts!r}')
    en_prints = language_counts.get('en', 0)
    if en_prints not in (0, EXPECTED_PRODUCTION_EN_PRINTS):
        raise RuntimeError(f'Unexpected EN print baseline: {en_prints}')

    new_table_counts = {}
    for table in ('set_identifiers', 'card_identifiers', 'print_localizations'):
        count = int(_one(cur, f'SELECT count(*) FROM {table}') or 0)
        new_table_counts[table] = count
        if count:
            raise RuntimeError(f'{table} must be empty before first bulk materialization; got {count}')
    for source in ('tcgdex:es', 'tcgdex:ja'):
        count = int(_one(cur, 'SELECT count(*) FROM print_identifiers WHERE source=%s', (source,)) or 0)
        if count:
            raise RuntimeError(f'Existing {source} print identifiers: {count}')
    regional_sets = int(_one(cur, "SELECT count(*) FROM sets WHERE game_id=%s AND code LIKE 'ja-%%'", (game_id,)) or 0)
    regional_cards = int(_one(cur, "SELECT count(*) FROM cards WHERE game_id=%s AND card_key LIKE 'tcgdex:ja:%%'", (game_id,)) or 0)
    if regional_sets or regional_cards:
        raise RuntimeError(f'Existing JA regional entities: sets={regional_sets} cards={regional_cards}')

    return {
        'game_id': game_id,
        'canonical_sets': canonical_sets,
        'canonical_cards': canonical_cards,
        'en_prints': en_prints,
        'language_counts': language_counts,
        'multilingual_table_counts': new_table_counts,
    }


def _canonical_maps(cur, game_id: int) -> tuple[dict[str, int], dict[str, int]]:
    set_map = {str(external): int(row_id) for row_id, external in _rows(
        cur, 'SELECT id, tcgdex_id FROM sets WHERE game_id=%s AND tcgdex_id IS NOT NULL', (game_id,)
    )}
    card_map = {str(external): int(row_id) for row_id, external in _rows(
        cur, 'SELECT id, tcgdex_id FROM cards WHERE game_id=%s AND tcgdex_id IS NOT NULL', (game_id,)
    )}
    return set_map, card_map


def _insert_es(cur, connector, rows: list[dict], game_id: int) -> dict:
    set_map, card_map = _canonical_maps(cur, game_id)
    set_aliases: dict[str, int] = {}
    card_aliases: dict[str, int] = {}
    print_rows = []
    by_external: dict[str, tuple[int, int, str, dict, str | None]] = {}

    for payload in rows:
        set_data = payload.get('set') or {}
        card = payload.get('card') or {}
        set_external = str(set_data.get('tcgdex_id') or '').strip()
        external = str(card.get('id') or '').strip()
        collector = str(card.get('collector_number') or '').strip()
        if not set_external or not external or not collector:
            raise RuntimeError(f'Incomplete ES physical row: {set_external=} {external=} {collector=}')
        set_id = set_map.get(set_external)
        card_id = card_map.get(external)
        if set_id is None or card_id is None:
            raise RuntimeError(f'ES exact canonical identity missing: set={set_external} card={external}')
        set_aliases[set_external] = set_id
        card_aliases[external] = card_id
        image = connector._primary_image_url_from_base(card.get('image'))
        by_external[external] = (set_id, card_id, collector, payload, image)
        print_rows.append((set_id, card_id, collector, 'es', 'unknown', False, 'default', None))

    _bulk(cur, """
        INSERT INTO prints (set_id,card_id,collector_number,language,rarity,is_foil,variant,tcgdex_id)
        VALUES %s
    """, print_rows)
    _bulk(cur, 'INSERT INTO set_identifiers (set_id,source,external_id) VALUES %s', [
        (set_id, 'tcgdex:es', external) for external, set_id in sorted(set_aliases.items())
    ])
    _bulk(cur, 'INSERT INTO card_identifiers (card_id,source,external_id) VALUES %s', [
        (card_id, 'tcgdex:es', external) for external, card_id in sorted(card_aliases.items())
    ])

    print_map = {str(external): int(print_id) for print_id, external in _rows(cur, """
        SELECT p.id, c.tcgdex_id
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND p.language='es'
    """, (game_id,))}
    if set(print_map) != set(by_external):
        raise RuntimeError(f'ES print materialization mismatch: expected={len(by_external)} actual={len(print_map)}')

    _bulk(cur, 'INSERT INTO print_identifiers (print_id,source,external_id) VALUES %s', [
        (print_map[external], 'tcgdex:es', external) for external in sorted(by_external)
    ])
    localization_rows = []
    image_rows = []
    for external, (_set_id, _card_id, _collector, payload, image) in by_external.items():
        localization = payload.get('localization') or {}
        localization_rows.append((
            print_map[external], 'es', 'tcgdex', external,
            localization.get('card_name'), localization.get('set_name'), Json(localization.get('details') or {}),
        ))
        if image:
            image_rows.append((print_map[external], image, True, 'tcgdex:es'))
    _bulk(cur, """
        INSERT INTO print_localizations
          (print_id,language,source,external_id,card_name,set_name,details_json)
        VALUES %s
    """, localization_rows)
    _bulk(cur, 'INSERT INTO print_images (print_id,url,is_primary,source) VALUES %s', image_rows)
    return {'sets': len(set_aliases), 'cards': len(card_aliases), 'prints': len(print_map), 'images': len(image_rows)}


def _insert_ja(cur, connector, rows: list[dict], game_id: int) -> dict:
    sets: dict[str, tuple[str, str, Any]] = {}
    cards: dict[str, tuple[str, str]] = {}
    prepared: dict[str, tuple[str, str, str, dict, str | None]] = {}
    for payload in rows:
        set_data = payload.get('set') or {}
        card = payload.get('card') or {}
        set_external = str(set_data.get('tcgdex_id') or '').strip()
        external = str(card.get('id') or '').strip()
        collector = str(card.get('collector_number') or '').strip()
        card_name = str(card.get('name') or '').strip()
        if not set_external or not external or not collector or not card_name:
            raise RuntimeError(f'Incomplete JA physical row: set={set_external} card={external} collector={collector}')
        set_code = connector._regional_set_code('ja', set_external)
        card_key = connector._regional_card_key('ja', external)
        sets[set_external] = (set_code, str(set_data.get('name') or set_external), _release_date(set_data.get('released_at')))
        cards[external] = (card_key, card_name)
        prepared[external] = (set_external, card_key, collector, payload, connector._primary_image_url_from_base(card.get('image')))

    _bulk(cur, 'INSERT INTO sets (game_id,code,tcgdex_id,name,release_date) VALUES %s', [
        (game_id, code, None, name, released) for _external, (code, name, released) in sorted(sets.items())
    ])
    set_id_by_code = {str(code): int(row_id) for row_id, code in _rows(
        cur, "SELECT id,code FROM sets WHERE game_id=%s AND code LIKE 'ja-%%'", (game_id,)
    )}
    set_map = {external: set_id_by_code[code] for external, (code, _name, _released) in sets.items()}
    if len(set_map) != EXPECTED_JA_SETS:
        raise RuntimeError(f'JA set materialization mismatch: {len(set_map)}')
    _bulk(cur, 'INSERT INTO set_identifiers (set_id,source,external_id) VALUES %s', [
        (set_map[external], 'tcgdex:ja', external) for external in sorted(set_map)
    ])

    _bulk(cur, 'INSERT INTO cards (game_id,name,card_key,tcgdex_id) VALUES %s', [
        (game_id, name, card_key, None) for _external, (card_key, name) in sorted(cards.items())
    ])
    card_id_by_key = {str(key): int(row_id) for row_id, key in _rows(
        cur, "SELECT id,card_key FROM cards WHERE game_id=%s AND card_key LIKE 'tcgdex:ja:%%'", (game_id,)
    )}
    card_map = {external: card_id_by_key[key] for external, (key, _name) in cards.items()}
    if len(card_map) != EXPECTED_JA_CARDS:
        raise RuntimeError(f'JA card materialization mismatch: {len(card_map)}')
    _bulk(cur, 'INSERT INTO card_identifiers (card_id,source,external_id) VALUES %s', [
        (card_map[external], 'tcgdex:ja', external) for external in sorted(card_map)
    ])

    print_rows = []
    for external, (set_external, _key, collector, _payload, _image) in prepared.items():
        print_rows.append((set_map[set_external], card_map[external], collector, 'ja', 'unknown', False, 'default', None))
    _bulk(cur, """
        INSERT INTO prints (set_id,card_id,collector_number,language,rarity,is_foil,variant,tcgdex_id)
        VALUES %s
    """, print_rows)

    print_map = {str(external): int(print_id) for print_id, external in _rows(cur, """
        SELECT p.id, substring(c.card_key from 11)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND p.language='ja' AND c.card_key LIKE 'tcgdex:ja:%%'
    """, (game_id,))}
    if set(print_map) != set(prepared):
        raise RuntimeError(f'JA print materialization mismatch: expected={len(prepared)} actual={len(print_map)}')

    _bulk(cur, 'INSERT INTO print_identifiers (print_id,source,external_id) VALUES %s', [
        (print_map[external], 'tcgdex:ja', external) for external in sorted(prepared)
    ])
    localization_rows = []
    image_rows = []
    for external, (_set_external, _key, _collector, payload, image) in prepared.items():
        localization = payload.get('localization') or {}
        localization_rows.append((
            print_map[external], 'ja', 'tcgdex', external,
            localization.get('card_name'), localization.get('set_name'), Json(localization.get('details') or {}),
        ))
        if image:
            image_rows.append((print_map[external], image, True, 'tcgdex:ja'))
    _bulk(cur, """
        INSERT INTO print_localizations
          (print_id,language,source,external_id,card_name,set_name,details_json)
        VALUES %s
    """, localization_rows)
    _bulk(cur, 'INSERT INTO print_images (print_id,url,is_primary,source) VALUES %s', image_rows)
    return {'sets': len(set_map), 'cards': len(card_map), 'prints': len(print_map), 'images': len(image_rows)}


def _validate_transaction(cur, baseline: dict) -> dict:
    game_id = baseline['game_id']
    totals = {
        'sets': int(_one(cur, 'SELECT count(*) FROM sets WHERE game_id=%s', (game_id,)) or 0),
        'cards': int(_one(cur, 'SELECT count(*) FROM cards WHERE game_id=%s', (game_id,)) or 0),
        'prints': int(_one(cur, 'SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s', (game_id,)) or 0),
    }
    expected = {
        'sets': EXPECTED_CANONICAL_SETS + EXPECTED_JA_SETS,
        'cards': EXPECTED_CANONICAL_CARDS + EXPECTED_JA_CARDS,
        'prints': baseline['en_prints'] + EXPECTED_ES_CARDS + EXPECTED_JA_CARDS,
    }
    if totals != expected:
        raise RuntimeError(f'Final transaction totals mismatch: expected={expected!r} actual={totals!r}')
    languages = {
        str(lang): int(count)
        for lang, count in _rows(cur, """
            SELECT lower(trim(coalesce(p.language,''))), count(*)::bigint
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s GROUP BY 1 ORDER BY 1
        """, (game_id,))
    }
    expected_languages = {'es': EXPECTED_ES_CARDS, 'ja': EXPECTED_JA_CARDS}
    if baseline['en_prints']:
        expected_languages['en'] = baseline['en_prints']
    if languages != dict(sorted(expected_languages.items())):
        raise RuntimeError(f'Final language counts mismatch: expected={expected_languages!r} actual={languages!r}')

    checks = {
        'es_print_identifiers': int(_one(cur, "SELECT count(*) FROM print_identifiers WHERE source='tcgdex:es'") or 0),
        'ja_print_identifiers': int(_one(cur, "SELECT count(*) FROM print_identifiers WHERE source='tcgdex:ja'") or 0),
        'es_card_identifiers': int(_one(cur, "SELECT count(*) FROM card_identifiers WHERE source='tcgdex:es'") or 0),
        'ja_card_identifiers': int(_one(cur, "SELECT count(*) FROM card_identifiers WHERE source='tcgdex:ja'") or 0),
        'es_set_identifiers': int(_one(cur, "SELECT count(*) FROM set_identifiers WHERE source='tcgdex:es'") or 0),
        'ja_set_identifiers': int(_one(cur, "SELECT count(*) FROM set_identifiers WHERE source='tcgdex:ja'") or 0),
        'es_localizations': int(_one(cur, "SELECT count(*) FROM print_localizations WHERE language='es' AND source='tcgdex'") or 0),
        'ja_localizations': int(_one(cur, "SELECT count(*) FROM print_localizations WHERE language='ja' AND source='tcgdex'") or 0),
        'non_en_global_tcgdex_ids': int(_one(cur, "SELECT count(*) FROM prints WHERE language IN ('es','ja') AND tcgdex_id IS NOT NULL") or 0),
    }
    expected_checks = {
        'es_print_identifiers': EXPECTED_ES_CARDS,
        'ja_print_identifiers': EXPECTED_JA_CARDS,
        'es_card_identifiers': EXPECTED_ES_CARDS,
        'ja_card_identifiers': EXPECTED_JA_CARDS,
        'es_set_identifiers': EXPECTED_ES_SETS,
        'ja_set_identifiers': EXPECTED_JA_SETS,
        'es_localizations': EXPECTED_ES_CARDS,
        'ja_localizations': EXPECTED_JA_CARDS,
        'non_en_global_tcgdex_ids': 0,
    }
    if checks != expected_checks:
        raise RuntimeError(f'Final identity checks mismatch: expected={expected_checks!r} actual={checks!r}')
    return {'totals': totals, 'language_counts': languages, 'identity_checks': checks}


def run(*, dry_run: bool = False, report_path: Path = REPORT_PATH) -> dict:
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    remote_started = time.perf_counter()
    es = _load_language(connector, 'es')
    ja = _load_language(connector, 'ja')
    remote = _validate_remote(es, ja)
    remote_seconds = round(time.perf_counter() - remote_started, 2)
    if dry_run:
        report = {'status': 'pass', 'mode': 'remote-dry-run', 'database_writes': 0, 'remote_seconds': remote_seconds, 'remote': remote}
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2, sort_keys=True))
        return report

    connection = psycopg2.connect(_db_url(), connect_timeout=20, application_name='dontripit_multilingual_bulk_backfill')
    connection.set_session(readonly=False, autocommit=False)
    transaction_started = time.perf_counter()
    try:
        with connection.cursor() as cur:
            baseline = _preflight(cur)
            es_written = _insert_es(cur, connector, es, baseline['game_id'])
            ja_written = _insert_ja(cur, connector, ja, baseline['game_id'])
            final = _validate_transaction(cur, baseline)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    transaction_seconds = round(time.perf_counter() - transaction_started, 2)

    report = {
        'status': 'pass',
        'mode': 'single-transaction-bulk-es-ja',
        'database_writes': EXPECTED_ES_CARDS + EXPECTED_JA_CARDS,
        'remote_seconds': remote_seconds,
        'database_transaction_seconds': transaction_seconds,
        'baseline': baseline,
        'remote': {k: v for k, v in remote.items() if not k.endswith('_set_ids')},
        'es_written': es_written,
        'ja_written': ja_written,
        'final': final,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--report', type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    run(dry_run=args.dry_run, report_path=args.report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
