from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import psycopg2
from psycopg2.extras import Json

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2
from app.scripts.audit_yugioh_multilingual_db_compatibility import (
    find_file,
    iter_records,
    mapping,
    s,
    sl,
)
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    PRINT_COLLECTOR_OVERRIDES,
    build_source_with_exact_collector_recovery,
)
from app.scripts.audit_yugioh_ygojson_canonical_print_projection import (
    canonical_print_id,
    canonical_rarity,
    variant_for_rarity,
)
from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import (
    BASELINE_PATH,
    validate_lean_invariants,
)

TARGET_REGION = {'es': 'global', 'ja': 'jp'}
TARGET_LOCALE = {'es': 'sp', 'ja': 'jp'}
EXPECTED_HEAD = '20260815_36'


def _date(value: object) -> date | None:
    raw = s(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items() if str(k).casefold() != 'price'}
    if isinstance(value, list):
        return [_json_clean(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _has_price_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(k).casefold() == 'price' or _has_price_key(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_price_key(v) for v in value)
    return False


def _text_record(card: Mapping[str, Any], language: str) -> dict[str, Any]:
    texts = mapping(card.get('text'))
    raw = texts.get(language)
    if not isinstance(raw, Mapping):
        return {}
    return {
        'name': s(raw.get('name')) or None,
        'effect': s(raw.get('effect')) or None,
        'pendulum_effect': s(raw.get('pendulumEffect') or raw.get('pendulum_effect')) or None,
        'official': raw.get('official') if isinstance(raw.get('official'), bool) else None,
    }


def _localized_set_name(set_obj: Mapping[str, Any], language: str) -> str | None:
    names = mapping(set_obj.get('name'))
    value = names.get(language)
    return s(value) or None


def _release_name(set_obj: Mapping[str, Any]) -> str:
    names = mapping(set_obj.get('name'))
    for language in ('en', 'es', 'ja'):
        value = s(names.get(language))
        if value:
            return value
    values = sorted(s(v) for v in names.values() if s(v))
    return values[0] if values else s(set_obj.get('id') or set_obj.get('uuid'))


def _locale_print_meta(locale_obj: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {'images': set(), 'editions': set()})
    card_info = mapping(locale_obj.get('cardInfo') or locale_obj.get('card_info'))
    for edition, per_print in card_info.items():
        if not isinstance(per_print, Mapping):
            continue
        for print_uuid, info in per_print.items():
            puid = s(print_uuid)
            if not puid:
                continue
            if s(edition):
                out[puid]['editions'].add(s(edition))
            if isinstance(info, Mapping):
                image = s(info.get('image'))
                if image:
                    out[puid]['images'].add(image)
    card_images = mapping(locale_obj.get('cardImages') or locale_obj.get('card_images'))
    for edition, per_print in card_images.items():
        if not isinstance(per_print, Mapping):
            continue
        for print_uuid, image in per_print.items():
            puid = s(print_uuid)
            if not puid:
                continue
            if s(edition):
                out[puid]['editions'].add(s(edition))
            image_url = s(image)
            if image_url:
                out[puid]['images'].add(image_url)
    return {
        puid: {'images': sorted(meta['images']), 'editions': sorted(meta['editions'])}
        for puid, meta in out.items()
    }


def _build_rich_source(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rich_cards: dict[str, dict[str, Any]] = {}
    for card in iter_records(find_file(root, 'cards.json')):
        card_uuid = s(card.get('id') or card.get('uuid'))
        if not card_uuid:
            continue
        rich_cards[card_uuid] = {
            'es': _text_record(card, 'es'),
            'ja': _text_record(card, 'ja'),
        }

    rich_sets: dict[str, dict[str, Any]] = {}
    for set_obj in iter_records(find_file(root, 'sets.json')):
        set_uuid = s(set_obj.get('id') or set_obj.get('uuid'))
        if not set_uuid:
            continue
        names = {str(k): s(v) for k, v in mapping(set_obj.get('name')).items() if s(v)}
        locales = mapping(set_obj.get('locales'))
        target_meta: dict[str, Any] = {}
        for target, spec in v2.TARGETS.items():
            loc = locales.get(spec['locale'])
            if not isinstance(loc, Mapping):
                continue
            target_meta[target] = {
                'language': sl(loc.get('language') or loc.get('lang')) or spec['language'],
                'prefix': s(loc.get('prefix')) or None,
                'date': _date(loc.get('date')),
                'set_name': _localized_set_name(set_obj, spec['language']),
                'print_meta': _locale_print_meta(loc),
            }
        rich_sets[set_uuid] = {
            'names': names,
            'release_name': _release_name(set_obj),
            'targets': target_meta,
        }
    return rich_cards, rich_sets


def _pick_unique(values: list[Any], label: str, conflicts: list[dict[str, Any]], context: dict[str, Any]) -> Any:
    compact = []
    for value in values:
        if value in (None, ''):
            continue
        if value not in compact:
            compact.append(value)
    if len(compact) > 1:
        conflicts.append({**context, 'field': label, 'values': compact[:12]})
    return compact[0] if compact else None


def _resolve_card_id(
    card_uuid: str,
    source_cards: dict[str, dict[str, str]],
    db_cards: dict[str, int],
    konami_to_db_cards: dict[str, set[int]],
) -> tuple[int | None, str, bool]:
    card = source_cards.get(card_uuid) or {}
    ygo_id = v2.canonical_ygo_id(card.get('ygoprodeck'))
    if ygo_id and ygo_id in db_cards:
        return db_cards[ygo_id], 'ygoprodeck_exact', False
    konami_id = s(card.get('konami'))
    if not konami_id:
        return None, 'unresolved', False
    candidates = konami_to_db_cards.get(konami_id, set())
    if len(candidates) == 1:
        return next(iter(candidates)), 'konami_exact_alias', False
    if len(candidates) > 1:
        return None, 'konami_ambiguous', True
    return None, 'unresolved', False


def build_plan(root: Path, target_url: str) -> dict[str, Any]:
    source_cards, source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    quarantines, quarantine_details = v2.source_quarantine(source_cards, source_rows)
    freshness = v2.ygojson_freshness(root)
    rich_cards, rich_sets = _build_rich_source(root)

    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_cert_plan')
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version LIMIT 1')
            revision = str(cur.fetchone()[0])
            if revision != EXPECTED_HEAD:
                raise RuntimeError(f'Expected schema {EXPECTED_HEAD}, got {revision}')
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()[0])
            cur.execute('SELECT id,yugoprodeck_id FROM cards WHERE game_id=%s AND yugoprodeck_id IS NOT NULL', (game_id,))
            db_cards = {str(external): int(card_id) for card_id, external in cur.fetchall()}
            conn.rollback()
    finally:
        conn.close()

    konami_to_db_cards: dict[str, set[int]] = defaultdict(set)
    for card in source_cards.values():
        konami_id = s(card.get('konami'))
        ygo_id = v2.canonical_ygo_id(card.get('ygoprodeck'))
        db_card_id = db_cards.get(ygo_id) if ygo_id else None
        if konami_id and db_card_id is not None:
            konami_to_db_cards[konami_id].add(db_card_id)

    plan_targets: dict[str, Any] = {}
    releases: dict[str, dict[str, Any]] = {}
    family_candidates: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    localization_conflicts: list[dict[str, Any]] = []
    membership_alias_conflicts: list[dict[str, Any]] = []
    card_resolution_ambiguities: list[dict[str, Any]] = []

    for target, spec in v2.TARGETS.items():
        exact_rows: list[dict[str, Any]] = []
        quarantine = quarantines[target]
        for row in source_rows[target]:
            puid = s(row.get('print_uuid'))
            if not puid or puid in quarantine or row.get('quality') != 'exact':
                continue
            collector = s(row.get('collector')).upper()
            family = s(row.get('family')).upper()
            card_uuid = s(row.get('card_uuid'))
            set_uuid = s(row.get('set_uuid'))
            if not collector or not family or not card_uuid or not set_uuid:
                continue
            logical = v2.logical_card_identity(source_cards.get(card_uuid) or {}, card_uuid)
            rarity = canonical_rarity(row.get('rarity'))
            exact_rows.append({
                'print_uuid': puid,
                'set_uuid': set_uuid,
                'card_uuid': card_uuid,
                'logical_card': logical,
                'collector': collector,
                'family': family,
                'rarity': rarity,
            })
            set_meta = rich_sets.get(set_uuid, {}).get('targets', {}).get(target, {})
            set_name = s(set_meta.get('set_name'))
            release_date = set_meta.get('date')
            family_candidates[(target, family)].append(
                ((release_date.isoformat() if isinstance(release_date, date) else '9999-12-31'), set_name, set_uuid)
            )

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in exact_rows:
            groups[(row['logical_card'], row['collector'], row['rarity'])].append(row)

        materializable: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []
        resolution_modes = Counter()
        for identity, rows in sorted(groups.items()):
            families = {row['family'] for row in rows}
            if len(families) != 1:
                raise AssertionError(f'Canonical print spans multiple families: {identity} -> {families}')
            family = next(iter(families))
            card_uuids = sorted({row['card_uuid'] for row in rows})
            resolved_ids: set[int] = set()
            modes: set[str] = set()
            ambiguous = False
            for card_uuid in card_uuids:
                db_card_id, mode, is_ambiguous = _resolve_card_id(
                    card_uuid, source_cards, db_cards, konami_to_db_cards
                )
                modes.add(mode)
                ambiguous = ambiguous or is_ambiguous
                if db_card_id is not None:
                    resolved_ids.add(db_card_id)
            if ambiguous or len(resolved_ids) > 1:
                card_resolution_ambiguities.append({
                    'target': target,
                    'identity': identity,
                    'card_uuids': card_uuids,
                    'resolved_ids': sorted(resolved_ids),
                })
                continue
            if not resolved_ids:
                retained.append({
                    'logical_card': identity[0], 'collector': identity[1], 'rarity': identity[2],
                    'card_uuids': card_uuids,
                })
                continue
            db_card_id = next(iter(resolved_ids))
            for mode in modes:
                resolution_modes[mode] += 1

            memberships: dict[str, set[str]] = defaultdict(set)
            membership_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                memberships[row['set_uuid']].add(row['print_uuid'])
                membership_rows[(row['set_uuid'], row['print_uuid'])].append(row)
            bad_memberships = {set_uuid: ids for set_uuid, ids in memberships.items() if len(ids) != 1}
            if bad_memberships:
                membership_alias_conflicts.append({
                    'target': target, 'identity': identity,
                    'conflicts': {k: sorted(v) for k, v in bad_memberships.items()},
                })
                continue

            context = {'target': target, 'logical_card': identity[0], 'collector': identity[1], 'rarity': identity[2]}
            text_rows = [rich_cards.get(card_uuid, {}).get(spec['language'], {}) for card_uuid in card_uuids]
            card_name = _pick_unique([row.get('name') for row in text_rows], 'card_name', localization_conflicts, context)
            effect = _pick_unique([row.get('effect') for row in text_rows], 'effect', localization_conflicts, context)
            pendulum_effect = _pick_unique(
                [row.get('pendulum_effect') for row in text_rows], 'pendulum_effect', localization_conflicts, context
            )
            official = _pick_unique([row.get('official') for row in text_rows], 'official', localization_conflicts, context)

            release_names: list[str] = []
            membership_plan = []
            all_images: set[str] = set()
            for set_uuid, source_ids in sorted(memberships.items()):
                source_print_id = next(iter(source_ids))
                set_info = rich_sets.get(set_uuid, {})
                target_set_info = set_info.get('targets', {}).get(target, {})
                localized_release_name = s(target_set_info.get('set_name')) or None
                if localized_release_name:
                    release_names.append(localized_release_name)
                print_meta = target_set_info.get('print_meta', {}).get(source_print_id, {})
                image_urls = sorted(set(print_meta.get('images') or []))
                editions = sorted(set(print_meta.get('editions') or []))
                all_images.update(image_urls)
                membership_plan.append({
                    'release_id': set_uuid,
                    'source_print_id': source_print_id,
                    'editions': editions,
                    'image_urls': image_urls,
                    'locale': TARGET_LOCALE[target],
                    'language': spec['language'],
                    'region': TARGET_REGION[target],
                })
                release = releases.setdefault(set_uuid, {
                    'external_id': set_uuid,
                    'name': set_info.get('release_name') or set_uuid,
                    'names': set_info.get('names') or {},
                    'target_locales': {},
                    'release_dates': [],
                })
                release['target_locales'][target] = {
                    'language': target_set_info.get('language'),
                    'prefix': target_set_info.get('prefix'),
                    'date': target_set_info.get('date').isoformat() if isinstance(target_set_info.get('date'), date) else None,
                    'set_name': localized_release_name,
                }
                if isinstance(target_set_info.get('date'), date):
                    release['release_dates'].append(target_set_info['date'].isoformat())

            candidates = family_candidates[(target, family)]
            named_candidates = sorted(c for c in candidates if c[1])
            canonical_set_name = named_candidates[0][1] if named_candidates else family
            dated = sorted(c for c in candidates if c[0] != '9999-12-31')
            canonical_set_date = dated[0][0] if dated else None
            print_key = canonical_print_id(target, identity[0], identity[1], identity[2])
            source_print_ids = sorted({row['print_uuid'] for row in rows})
            source_release_ids = sorted(memberships)
            details = {
                'effect': effect,
                'pendulum_effect': pendulum_effect,
                'official': official,
                'source_card_ids': card_uuids,
                'source_print_ids': source_print_ids,
                'source_release_ids': source_release_ids,
                'release_names': sorted(set(release_names)),
                'image_scope': 'ygojson-locale-specific-print-images-only',
            }
            materializable.append({
                'target': target,
                'language': spec['language'],
                'region': TARGET_REGION[target],
                'logical_card': identity[0],
                'collector': identity[1],
                'rarity': identity[2],
                'variant': variant_for_rarity(identity[2]),
                'print_key': print_key,
                'card_id': db_card_id,
                'family': family,
                'set_name': canonical_set_name,
                'set_release_date': canonical_set_date,
                'card_name': card_name,
                'localization_details': details,
                'images': sorted(all_images),
                'memberships': membership_plan,
            })

        plan_targets[target] = {
            'language': spec['language'],
            'region': TARGET_REGION[target],
            'canonical_prints': len(groups),
            'materializable': materializable,
            'retained_missing_card': retained,
            'quarantine': quarantine_details[target],
            'resolution_modes': dict(sorted(resolution_modes.items())),
        }

    for release in releases.values():
        dates = sorted(set(release.pop('release_dates', [])))
        release['release_date'] = dates[0] if dates else None
        release['metadata'] = {
            'names': release.pop('names'),
            'target_locales': release.pop('target_locales'),
            'source': 'YGOJSON aggregate v1',
            'economics_imported': False,
        }

    missing_card_names = sum(
        1 for target in plan_targets.values() for item in target['materializable'] if not item['card_name']
    )
    source_image_relations = sum(
        len(item['images']) for target in plan_targets.values() for item in target['materializable']
    )
    source_memberships = sum(
        len(item['memberships']) for target in plan_targets.values() for item in target['materializable']
    )
    structural_gates = {
        'no_card_resolution_ambiguities': not card_resolution_ambiguities,
        'no_membership_alias_conflicts': not membership_alias_conflicts,
        'no_localization_conflicts': not localization_conflicts,
        'localized_card_names_complete': missing_card_names == 0,
        'all_exact_collector_overrides_present': len(PRINT_COLLECTOR_OVERRIDES) == 8,
        'source_metadata_has_no_price_keys': not _has_price_key({'targets': plan_targets, 'releases': releases}),
    }
    return {
        'game_id': game_id,
        'source_freshness': freshness,
        'rollout_freshness_pass': freshness.get('status') == 'current_enough',
        'targets': plan_targets,
        'releases': releases,
        'source_memberships': source_memberships,
        'source_image_relations': source_image_relations,
        'missing_localized_card_names': missing_card_names,
        'card_resolution_ambiguities': card_resolution_ambiguities[:80],
        'membership_alias_conflicts': membership_alias_conflicts[:80],
        'localization_conflicts': localization_conflicts[:80],
        'structural_gates': structural_gates,
        'structural_pass': all(structural_gates.values()),
    }


def _load_sets(cur, game_id: int) -> dict[tuple[str, str], int]:
    cur.execute('SELECT id,upper(code),lower(coalesce(region,\'global\')) FROM sets WHERE game_id=%s', (game_id,))
    return {(str(code), str(region)): int(set_id) for set_id, code, region in cur.fetchall()}


def _load_prints(cur, game_id: int) -> dict[tuple[int, int, str, str, bool, str], tuple[int, str | None]]:
    cur.execute(
        """
        SELECT p.id,p.card_id,p.set_id,upper(p.collector_number),lower(coalesce(p.language,'')),
               p.is_foil,coalesce(p.variant,''),p.print_key
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
        """,
        (game_id,),
    )
    return {
        (int(card_id), int(set_id), str(collector), str(language), bool(is_foil), str(variant)): (int(pid), print_key)
        for pid, card_id, set_id, collector, language, is_foil, variant, print_key in cur.fetchall()
    }


def apply_plan(plan: dict[str, Any], target_url: str) -> dict[str, Any]:
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_cert_writer')
    conn.set_session(readonly=False, autocommit=False)
    writes = Counter()
    try:
        with conn.cursor() as cur:
            game_id = int(plan['game_id'])
            sets = _load_sets(cur, game_id)
            family_specs: dict[tuple[str, str], dict[str, Any]] = {}
            for target in plan['targets'].values():
                for item in target['materializable']:
                    key = (item['family'], item['region'])
                    family_specs.setdefault(key, item)
            for (family, region), item in sorted(family_specs.items()):
                if (family, region) in sets:
                    continue
                cur.execute(
                    """
                    INSERT INTO sets (game_id,code,region,name,release_date)
                    VALUES (%s,%s,%s,%s,%s) RETURNING id
                    """,
                    (game_id, family, region, item['set_name'], item['set_release_date']),
                )
                sets[(family, region)] = int(cur.fetchone()[0])
                writes['sets'] += 1

            cur.execute("SELECT id,external_id FROM catalog_releases WHERE game_id=%s AND source='ygojson'", (game_id,))
            release_ids = {str(external): int(release_id) for release_id, external in cur.fetchall()}
            for external_id, release in sorted(plan['releases'].items()):
                if external_id in release_ids:
                    continue
                cur.execute(
                    """
                    INSERT INTO catalog_releases
                      (game_id,source,external_id,name,code,release_type,release_date,language,region,metadata_json)
                    VALUES (%s,'ygojson',%s,%s,NULL,'physical-set',%s,NULL,NULL,%s)
                    RETURNING id
                    """,
                    (
                        game_id, external_id, release['name'], release['release_date'],
                        Json(_json_clean(release['metadata'])),
                    ),
                )
                release_ids[external_id] = int(cur.fetchone()[0])
                writes['catalog_releases'] += 1

            prints = _load_prints(cur, game_id)
            print_ids_by_key: dict[str, int] = {}
            for target in ('es', 'ja'):
                for item in sorted(plan['targets'][target]['materializable'], key=lambda x: x['print_key']):
                    set_id = sets[(item['family'], item['region'])]
                    natural = (
                        int(item['card_id']), set_id, item['collector'], item['language'], False, item['variant']
                    )
                    found = prints.get(natural)
                    if found is None:
                        cur.execute(
                            """
                            INSERT INTO prints
                              (set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key,
                               scryfall_id,tcgdex_id,yugioh_id,riftbound_id)
                            VALUES (%s,%s,%s,%s,%s,false,%s,%s,NULL,NULL,NULL,NULL)
                            RETURNING id
                            """,
                            (
                                set_id, item['card_id'], item['collector'], item['language'],
                                item['rarity'], item['variant'], item['print_key'],
                            ),
                        )
                        print_id = int(cur.fetchone()[0])
                        prints[natural] = (print_id, item['print_key'])
                        writes['prints'] += 1
                    else:
                        print_id = int(found[0])
                        existing_key = found[1]
                        if existing_key not in (None, item['print_key']):
                            raise AssertionError(
                                f'Existing natural Print has conflicting print_key: {natural} {existing_key} != {item["print_key"]}'
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
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    print_id = print_ids_by_key[item['print_key']]
                    key = (print_id, item['language'])
                    expected_details = _json_clean(item['localization_details'])
                    existing = localizations.get(key)
                    if existing is None:
                        cur.execute(
                            """
                            INSERT INTO print_localizations
                              (print_id,language,source,external_id,card_name,set_name,details_json)
                            VALUES (%s,%s,'ygojson',%s,%s,%s,%s)
                            """,
                            (
                                print_id, item['language'], item['print_key'], item['card_name'],
                                item['set_name'], Json(expected_details),
                            ),
                        )
                        localizations[key] = ('ygojson', item['print_key'], item['card_name'], item['set_name'], expected_details)
                        writes['print_localizations'] += 1
                    else:
                        source, external_id, card_name, set_name, details = existing
                        if (
                            source != 'ygojson' or external_id != item['print_key'] or card_name != item['card_name']
                            or set_name != item['set_name'] or details != expected_details
                        ):
                            raise AssertionError(f'Existing localization conflicts with exact source plan for {item["print_key"]}')

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
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    print_id = print_ids_by_key[item['print_key']]
                    for image_url in item['images']:
                        key = (print_id, image_url)
                        if key in existing_images:
                            continue
                        is_primary = print_id not in primary_prints
                        cur.execute(
                            "INSERT INTO print_images (print_id,url,is_primary,source) VALUES (%s,%s,%s,'ygojson')",
                            (print_id, image_url, is_primary),
                        )
                        existing_images.add(key)
                        if is_primary:
                            primary_prints.add(print_id)
                        writes['print_images'] += 1

            cur.execute(
                """
                SELECT pr.print_id,cr.external_id,pr.source_print_id
                FROM print_releases pr JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE cr.game_id=%s AND cr.source='ygojson'
                """,
                (game_id,),
            )
            memberships = {(int(pid), str(external)): source_print for pid, external, source_print in cur.fetchall()}
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
                            cur.execute(
                                """
                                INSERT INTO print_releases
                                  (print_id,release_id,source_print_id,appearance_type,metadata_json)
                                VALUES (%s,%s,%s,'localized-physical',%s)
                                """,
                                (
                                    print_id, release_ids[release_external], membership['source_print_id'],
                                    Json(_json_clean(metadata)),
                                ),
                            )
                            memberships[key] = membership['source_print_id']
                            writes['print_releases'] += 1
                        elif str(existing_source) != membership['source_print_id']:
                            raise AssertionError(
                                f'PrintRelease provenance conflict for print={print_id} release={release_external}'
                            )

            conn.commit()
            return {
                'writes': dict(sorted(writes.items())),
                'total_writes': sum(writes.values()),
                'materialized_print_ids': len(print_ids_by_key),
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate(plan: dict[str, Any], target_url: str, baseline: dict[str, Any]) -> dict[str, Any]:
    lean = validate_lean_invariants(target_url, baseline)
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name='dontripit_ygo_cert_validate')
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id = int(plan['game_id'])
            sets = _load_sets(cur, game_id)
            prints = _load_prints(cur, game_id)
            print_ids_by_key: dict[str, int] = {}
            missing_prints = []
            foreign_id_violations = 0
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    set_id = sets.get((item['family'], item['region']))
                    if set_id is None:
                        missing_prints.append(item['print_key'])
                        continue
                    natural = (
                        int(item['card_id']), int(set_id), item['collector'], item['language'], False, item['variant']
                    )
                    found = prints.get(natural)
                    if found is None:
                        missing_prints.append(item['print_key'])
                        continue
                    print_ids_by_key[item['print_key']] = int(found[0])

            ids = sorted(set(print_ids_by_key.values()))
            if ids:
                cur.execute(
                    """
                    SELECT count(*) FROM prints
                    WHERE id=ANY(%s) AND (scryfall_id IS NOT NULL OR tcgdex_id IS NOT NULL OR yugioh_id IS NOT NULL OR riftbound_id IS NOT NULL)
                    """,
                    (ids,),
                )
                foreign_id_violations = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT p.card_id,p.set_id,upper(p.collector_number),lower(coalesce(p.language,'')),p.is_foil,coalesce(p.variant,''),count(*)
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                GROUP BY p.card_id,p.set_id,upper(p.collector_number),lower(coalesce(p.language,'')),p.is_foil,coalesce(p.variant,'')
                HAVING count(*)>1
                """,
                (game_id,),
            )
            natural_duplicates = len(cur.fetchall())

            cur.execute(
                """
                SELECT l.print_id,l.language,l.source,l.external_id,l.card_name,l.set_name,l.details_json
                FROM print_localizations l WHERE l.print_id=ANY(%s)
                """,
                (ids or [-1],),
            )
            localizations = {
                (int(pid), str(lang)): (source, external_id, card_name, set_name, details)
                for pid, lang, source, external_id, card_name, set_name, details in cur.fetchall()
            }
            localization_mismatches = []
            stored_metadata_with_price_keys = 0
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    print_id = print_ids_by_key.get(item['print_key'])
                    if print_id is None:
                        continue
                    got = localizations.get((print_id, item['language']))
                    expected = (
                        'ygojson', item['print_key'], item['card_name'], item['set_name'],
                        _json_clean(item['localization_details']),
                    )
                    if got != expected:
                        localization_mismatches.append(item['print_key'])
                    if got and _has_price_key(got[4]):
                        stored_metadata_with_price_keys += 1

            cur.execute(
                "SELECT print_id,url FROM print_images WHERE print_id=ANY(%s) AND source='ygojson'",
                (ids or [-1],),
            )
            images = {(int(pid), str(url)) for pid, url in cur.fetchall()}
            missing_image_relations = 0
            prints_with_source_image = 0
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    print_id = print_ids_by_key.get(item['print_key'])
                    if print_id is None:
                        continue
                    expected_urls = set(item['images'])
                    if expected_urls:
                        prints_with_source_image += 1
                    missing_image_relations += sum((print_id, url) not in images for url in expected_urls)

            cur.execute(
                "SELECT id,external_id,metadata_json FROM catalog_releases WHERE game_id=%s AND source='ygojson'",
                (game_id,),
            )
            release_rows = {str(external): (int(rid), metadata) for rid, external, metadata in cur.fetchall()}
            release_metadata_price_keys = sum(_has_price_key(metadata) for _rid, metadata in release_rows.values())
            cur.execute(
                """
                SELECT pr.print_id,cr.external_id,pr.source_print_id,pr.metadata_json
                FROM print_releases pr JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE cr.game_id=%s AND cr.source='ygojson' AND pr.print_id=ANY(%s)
                """,
                (game_id, ids or [-1]),
            )
            membership_rows = {
                (int(pid), str(external)): (str(source_print) if source_print is not None else None, metadata)
                for pid, external, source_print, metadata in cur.fetchall()
            }
            missing_memberships = 0
            provenance_mismatches = 0
            membership_metadata_price_keys = 0
            for target in ('es', 'ja'):
                for item in plan['targets'][target]['materializable']:
                    print_id = print_ids_by_key.get(item['print_key'])
                    if print_id is None:
                        continue
                    for membership in item['memberships']:
                        got = membership_rows.get((print_id, membership['release_id']))
                        if got is None:
                            missing_memberships += 1
                            continue
                        if got[0] != membership['source_print_id']:
                            provenance_mismatches += 1
                        if _has_price_key(got[1]):
                            membership_metadata_price_keys += 1

            target_counts = {}
            for target in ('es', 'ja'):
                language = plan['targets'][target]['language']
                target_expected = plan['targets'][target]['materializable']
                target_counts[target] = {
                    'canonical_prints': plan['targets'][target]['canonical_prints'],
                    'materializable_prints': len(target_expected),
                    'retained_missing_card': len(plan['targets'][target]['retained_missing_card']),
                    'materialized_found': sum(item['print_key'] in print_ids_by_key for item in target_expected),
                    'language': language,
                }

            conn.rollback()
            gates = {
                'all_planned_prints_materialized': not missing_prints,
                'no_natural_duplicates': natural_duplicates == 0,
                'localized_foreign_global_ids_null': foreign_id_violations == 0,
                'localizations_exact': not localization_mismatches,
                'all_source_image_relations_materialized': missing_image_relations == 0,
                'all_print_release_memberships_materialized': missing_memberships == 0,
                'print_release_source_ids_exact': provenance_mismatches == 0,
                'stored_metadata_has_no_price_keys': (
                    stored_metadata_with_price_keys == 0
                    and release_metadata_price_keys == 0
                    and membership_metadata_price_keys == 0
                ),
                'preexisting_catalog_unchanged': lean['status'] == 'pass',
            }
            return {
                'status': 'pass' if all(gates.values()) else 'fail',
                'gates': gates,
                'target_counts': target_counts,
                'missing_prints': missing_prints[:80],
                'natural_duplicates': natural_duplicates,
                'foreign_id_violations': foreign_id_violations,
                'localization_mismatches': localization_mismatches[:80],
                'source_image_relations_expected': plan['source_image_relations'],
                'missing_source_image_relations': missing_image_relations,
                'prints_with_source_image': prints_with_source_image,
                'source_memberships_expected': plan['source_memberships'],
                'missing_memberships': missing_memberships,
                'provenance_mismatches': provenance_mismatches,
                'metadata_price_key_violations': {
                    'localizations': stored_metadata_with_price_keys,
                    'releases': release_metadata_price_keys,
                    'memberships': membership_metadata_price_keys,
                },
                'lean_invariants': lean,
            }
    finally:
        conn.close()


def run(root: Path, output: Path) -> dict[str, Any]:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL or DATABASE_URL is required')
    baseline = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
    plan = build_plan(root, target_url)
    if not plan['structural_pass']:
        report = {
            'status': 'fail',
            'mode': 'ephemeral-only-yugioh-multilingual-certification-v1',
            'production_writes': 0,
            'baseline': baseline,
            'plan_summary': {
                'structural_gates': plan['structural_gates'],
                'targets': {
                    target: {
                        'canonical_prints': data['canonical_prints'],
                        'materializable_prints': len(data['materializable']),
                        'retained_missing_card': len(data['retained_missing_card']),
                    }
                    for target, data in plan['targets'].items()
                },
            },
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True))
        return report

    pass1 = apply_plan(plan, target_url)
    validation = validate(plan, target_url, baseline)
    pass2 = apply_plan(plan, target_url)
    pass2_zero_writes = pass2['total_writes'] == 0
    status = 'pass' if validation['status'] == 'pass' and pass2_zero_writes else 'fail'
    report = {
        'status': status,
        'mode': 'ephemeral-only-yugioh-multilingual-certification-v1',
        'production_writes': 0,
        'production_source_mode': 'read-only-clone-at-schema-34',
        'baseline': baseline,
        'source_freshness': plan['source_freshness'],
        'rollout_freshness_pass': plan['rollout_freshness_pass'],
        'production_rollout_ready': False,
        'identity_contract': {
            'set': 'family+region (ES=global, JA=jp)',
            'print': 'Card+Set+collector+language+is_foil=false+rarity-derived variant',
            'canonical_print_key': 'ygo-localized-v1 deterministic key',
            'source_print_uuid': 'PrintRelease.source_print_id provenance only; never Print.yugioh_id',
            'localized_display': 'PrintLocalization from YGOJSON target-language text',
            'images': 'locale-specific cardInfo/cardImages only; generic English card art excluded',
            'economics': 'YGOJSON price fields ignored; no economics tables copied or written',
        },
        'plan_summary': {
            'structural_gates': plan['structural_gates'],
            'targets': {
                target: {
                    'canonical_prints': data['canonical_prints'],
                    'materializable_prints': len(data['materializable']),
                    'retained_missing_card': len(data['retained_missing_card']),
                    'quarantine': data['quarantine'],
                    'resolution_modes': data['resolution_modes'],
                }
                for target, data in plan['targets'].items()
            },
            'source_memberships': plan['source_memberships'],
            'source_image_relations': plan['source_image_relations'],
            'missing_localized_card_names': plan['missing_localized_card_names'],
        },
        'pass1': pass1,
        'validation': validation,
        'pass2': pass2,
        'pass2_zero_writes': pass2_zero_writes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({
        'status': status,
        'production_writes': 0,
        'source_freshness': report['source_freshness'],
        'plan_summary': report['plan_summary'],
        'pass1': pass1,
        'validation': validation,
        'pass2': pass2,
        'pass2_zero_writes': pass2_zero_writes,
    }, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.input_dir, args.output)
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
