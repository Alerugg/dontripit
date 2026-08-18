from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from psycopg2.extras import Json, execute_values

from app.scripts import certify_yugioh_multilingual_ephemeral_v1 as cert
from app.scripts import rollout_yugioh_multilingual_production_v2 as v2


BATCH_SIZE = 1000
base = v2.base


def _insert_values(cur, sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    execute_values(cur, sql, rows, page_size=BATCH_SIZE)
    return len(rows)


def apply_plan_batched(plan: dict[str, Any], _target_url: str, conn) -> dict[str, Any]:
    """Apply the already-certified plan with bounded batched INSERT round-trips.

    This preserves the V1 identity/conflict rules and runs inside the exact same
    outer atomic transaction used by the production runner. It never commits.
    """
    writes = Counter()
    with conn.cursor() as cur:
        game_id = int(plan['game_id'])

        sets = cert._load_sets(cur, game_id)
        family_specs: dict[tuple[str, str], dict[str, Any]] = {}
        for target in plan['targets'].values():
            for item in target['materializable']:
                family_specs.setdefault((item['family'], item['region']), item)
        missing_sets = [
            (game_id, family, region, item['set_name'], item['set_release_date'])
            for (family, region), item in sorted(family_specs.items())
            if (family, region) not in sets
        ]
        writes['sets'] += _insert_values(
            cur,
            """
            INSERT INTO sets (game_id,code,region,name,release_date)
            VALUES %s
            """,
            missing_sets,
        )
        sets = cert._load_sets(cur, game_id)
        for key in family_specs:
            if key not in sets:
                raise AssertionError(f'Batched set insert missing expected identity {key}')

        cur.execute("SELECT id,external_id FROM catalog_releases WHERE game_id=%s AND source='ygojson'", (game_id,))
        release_ids = {str(external): int(release_id) for release_id, external in cur.fetchall()}
        missing_releases = []
        for external_id, release in sorted(plan['releases'].items()):
            if external_id in release_ids:
                continue
            missing_releases.append(
                (
                    game_id,
                    external_id,
                    release['name'],
                    release['release_date'],
                    Json(cert._json_clean(release['metadata'])),
                )
            )
        writes['catalog_releases'] += _insert_values(
            cur,
            """
            INSERT INTO catalog_releases
              (game_id,source,external_id,name,code,release_type,release_date,language,region,metadata_json)
            VALUES %s
            """,
            [
                (game_id_, 'ygojson', external_id, name, None, 'physical-set', release_date, None, None, metadata)
                for game_id_, external_id, name, release_date, metadata in missing_releases
            ],
        )
        cur.execute("SELECT id,external_id FROM catalog_releases WHERE game_id=%s AND source='ygojson'", (game_id,))
        release_ids = {str(external): int(release_id) for release_id, external in cur.fetchall()}
        missing_release_ids = sorted(set(plan['releases']) - set(release_ids))
        if missing_release_ids:
            raise AssertionError(f'Batched release insert missing ids: {missing_release_ids[:20]}')

        prints = cert._load_prints(cur, game_id)
        pending_prints: list[tuple] = []
        pending_naturals: dict[tuple, str] = {}
        for target in ('es', 'ja'):
            for item in sorted(plan['targets'][target]['materializable'], key=lambda x: x['print_key']):
                set_id = sets[(item['family'], item['region'])]
                natural = (int(item['card_id']), int(set_id), item['collector'], item['language'], False, item['variant'])
                found = prints.get(natural)
                if found is not None:
                    existing_key = found[1]
                    if existing_key not in (None, item['print_key']):
                        raise AssertionError(
                            f'Existing natural Print has conflicting print_key: {natural} {existing_key} != {item["print_key"]}'
                        )
                    continue
                previous_key = pending_naturals.get(natural)
                if previous_key is not None and previous_key != item['print_key']:
                    raise AssertionError(f'Plan contains competing print keys for natural identity {natural}')
                if previous_key is None:
                    pending_naturals[natural] = item['print_key']
                    pending_prints.append(
                        (
                            set_id,
                            item['card_id'],
                            item['collector'],
                            item['language'],
                            item['rarity'],
                            False,
                            item['variant'],
                            item['print_key'],
                            None,
                            None,
                            None,
                            None,
                        )
                    )
        writes['prints'] += _insert_values(
            cur,
            """
            INSERT INTO prints
              (set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key,
               scryfall_id,tcgdex_id,yugioh_id,riftbound_id)
            VALUES %s
            """,
            pending_prints,
        )

        prints = cert._load_prints(cur, game_id)
        print_ids_by_key: dict[str, int] = {}
        for target in ('es', 'ja'):
            for item in plan['targets'][target]['materializable']:
                set_id = sets[(item['family'], item['region'])]
                natural = (int(item['card_id']), int(set_id), item['collector'], item['language'], False, item['variant'])
                found = prints.get(natural)
                if found is None:
                    raise AssertionError(f'Batched print insert missing natural identity {natural}')
                print_id, existing_key = int(found[0]), found[1]
                if existing_key not in (None, item['print_key']):
                    raise AssertionError(
                        f'Batched Print has conflicting print_key: {natural} {existing_key} != {item["print_key"]}'
                    )
                print_ids_by_key[item['print_key']] = print_id

        cur.execute(
            """
            SELECT l.print_id,l.language,l.source,l.external_id,l.card_name,l.set_name,l.details_json
            FROM print_localizations l
            JOIN prints p ON p.id=l.print_id
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND lower(l.language) IN ('es','ja')
            """,
            (game_id,),
        )
        localizations = {
            (int(pid), str(lang)): (source, external_id, card_name, set_name, details)
            for pid, lang, source, external_id, card_name, set_name, details in cur.fetchall()
        }
        missing_localizations: list[tuple] = []
        for target in ('es', 'ja'):
            for item in plan['targets'][target]['materializable']:
                print_id = print_ids_by_key[item['print_key']]
                key = (print_id, item['language'])
                expected_details = cert._json_clean(item['localization_details'])
                existing = localizations.get(key)
                if existing is None:
                    missing_localizations.append(
                        (
                            print_id,
                            item['language'],
                            'ygojson',
                            item['print_key'],
                            item['card_name'],
                            item['set_name'],
                            Json(expected_details),
                        )
                    )
                    localizations[key] = ('ygojson', item['print_key'], item['card_name'], item['set_name'], expected_details)
                else:
                    source, external_id, card_name, set_name, details = existing
                    if (
                        source != 'ygojson'
                        or external_id != item['print_key']
                        or card_name != item['card_name']
                        or set_name != item['set_name']
                        or details != expected_details
                    ):
                        raise AssertionError(f'Existing localization conflicts with exact source plan for {item["print_key"]}')
        writes['print_localizations'] += _insert_values(
            cur,
            """
            INSERT INTO print_localizations
              (print_id,language,source,external_id,card_name,set_name,details_json)
            VALUES %s
            """,
            missing_localizations,
        )

        cur.execute(
            """
            SELECT i.print_id,i.url FROM print_images i
            JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND i.source='ygojson' AND lower(coalesce(p.language,'')) IN ('es','ja')
            """,
            (game_id,),
        )
        existing_images = {(int(pid), str(url)) for pid, url in cur.fetchall()}
        cur.execute(
            """
            SELECT i.print_id FROM print_images i
            JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND i.is_primary=true AND lower(coalesce(p.language,'')) IN ('es','ja')
            """,
            (game_id,),
        )
        primary_prints = {int(row[0]) for row in cur.fetchall()}
        missing_images: list[tuple] = []
        for target in ('es', 'ja'):
            for item in plan['targets'][target]['materializable']:
                print_id = print_ids_by_key[item['print_key']]
                for image_url in item['images']:
                    key = (print_id, image_url)
                    if key in existing_images:
                        continue
                    is_primary = print_id not in primary_prints
                    missing_images.append((print_id, image_url, is_primary, 'ygojson'))
                    existing_images.add(key)
                    if is_primary:
                        primary_prints.add(print_id)
        writes['print_images'] += _insert_values(
            cur,
            "INSERT INTO print_images (print_id,url,is_primary,source) VALUES %s",
            missing_images,
        )

        cur.execute(
            """
            SELECT pr.print_id,cr.external_id,pr.source_print_id
            FROM print_releases pr JOIN catalog_releases cr ON cr.id=pr.release_id
            WHERE cr.game_id=%s AND cr.source='ygojson'
            """,
            (game_id,),
        )
        memberships = {(int(pid), str(external)): source_print for pid, external, source_print in cur.fetchall()}
        missing_memberships: list[tuple] = []
        for target in ('es', 'ja'):
            for item in plan['targets'][target]['materializable']:
                print_id = print_ids_by_key[item['print_key']]
                for membership in item['memberships']:
                    release_external = membership['release_id']
                    key = (print_id, release_external)
                    existing_source = memberships.get(key)
                    if existing_source is None:
                        metadata = {
                            'locale': membership['locale'],
                            'language': membership['language'],
                            'region': membership['region'],
                            'editions': membership['editions'],
                            'image_urls': membership['image_urls'],
                            'economics_imported': False,
                        }
                        missing_memberships.append(
                            (
                                print_id,
                                release_ids[release_external],
                                membership['source_print_id'],
                                'localized-physical',
                                Json(cert._json_clean(metadata)),
                            )
                        )
                        memberships[key] = membership['source_print_id']
                    elif str(existing_source) != membership['source_print_id']:
                        raise AssertionError(
                            f'PrintRelease provenance conflict for print={print_id} release={release_external}'
                        )
        writes['print_releases'] += _insert_values(
            cur,
            """
            INSERT INTO print_releases
              (print_id,release_id,source_print_id,appearance_type,metadata_json)
            VALUES %s
            """,
            missing_memberships,
        )

    return {
        'writes': dict(sorted(writes.items())),
        'total_writes': sum(writes.values()),
        'materialized_print_ids': len(print_ids_by_key),
        'writer': 'batched-v3',
        'batch_size': BATCH_SIZE,
    }


def _call_apply_batched(plan: dict[str, Any], url: str, real_conn) -> dict[str, Any]:
    return apply_plan_batched(plan, url, real_conn)


# base.run() resolves this symbol at runtime for pass1 and pass2.
base._call_apply_in_outer_transaction = _call_apply_batched


def run(**kwargs: Any) -> dict[str, Any]:
    output: Path = kwargs['output']
    apply = bool(kwargs['apply'])
    report = base.run(**kwargs)
    report['runner_version'] = 3
    report['writer_mode'] = 'batched-v3'
    report['commit_confirmed'] = apply and report.get('status') == 'pass'
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return report


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
