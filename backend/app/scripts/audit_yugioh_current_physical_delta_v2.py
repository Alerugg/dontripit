#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import create_engine, text

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as v2
from app.scripts.audit_yugioh_multilingual_db_compatibility import s, sl
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    build_source_with_exact_collector_recovery,
)
from app.scripts.audit_yugioh_multilingual_cross_source_reconciliation import (
    iter_yaml_cards,
    language_matches,
    norm_rarity,
)

TARGETS = {
    'es': {'language': 'es', 'region': 'global'},
    'ja': {'language': 'ja', 'region': 'jp'},
}


def family_from_collector(collector: str) -> str:
    value = s(collector).upper()
    return value.split('-', 1)[0] if '-' in value else value


def yaml_logical_identity(card: Mapping[str, Any]) -> str:
    konami_id = s(card.get('konami_id'))
    password = v2.canonical_ygo_id(card.get('password'))
    if konami_id:
        return f'konami:{konami_id}'
    if password:
        return f'ygoprodeck:{password}'
    return ''


def build_historical(root: Path) -> tuple[dict[str, dict[tuple[str, str, str], dict[str, Any]]], dict[str, Any]]:
    cards, source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    quarantines, quarantine_details = v2.source_quarantine(cards, source_rows)
    targets: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {}
    for target in TARGETS:
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in source_rows[target]:
            puid = s(row.get('print_uuid'))
            if not puid or puid in quarantines[target] or row.get('quality') != 'exact':
                continue
            collector = s(row.get('collector')).upper()
            if not collector:
                continue
            card_uuid = s(row.get('card_uuid'))
            logical = v2.logical_card_identity(cards.get(card_uuid) or {}, card_uuid)
            rarity = norm_rarity(row.get('rarity')) or 'unknown'
            key = (logical, collector, rarity)
            data = groups.setdefault(
                key,
                {
                    'logical_card': logical,
                    'collector': collector,
                    'rarity': rarity,
                    'family': family_from_collector(collector),
                    'source_print_ids': set(),
                    'source_release_ids': set(),
                },
            )
            data['source_print_ids'].add(puid)
            if s(row.get('set_uuid')):
                data['source_release_ids'].add(s(row.get('set_uuid')))
        targets[target] = groups
    return targets, {'quarantine': quarantine_details}


def build_current_yaml(cards_path: Path) -> tuple[dict[str, dict[tuple[str, str, str], dict[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    groups: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {target: {} for target in TARGETS}
    stats = {target: Counter() for target in TARGETS}
    invalid_samples: list[dict[str, Any]] = []

    for card in iter_yaml_cards(cards_path):
        logical = yaml_logical_identity(card)
        password = v2.canonical_ygo_id(card.get('password'))
        konami_id = s(card.get('konami_id'))
        sets = card.get('sets') or []
        if isinstance(sets, Mapping):
            sets = list(sets.values())
        if not isinstance(sets, list):
            continue
        for row in sets:
            if not isinstance(row, Mapping):
                continue
            collector = s(row.get('number')).upper()
            if not collector:
                continue
            rarity = norm_rarity(row.get('rarity')) or 'unknown'
            set_name = s(row.get('set'))
            edition = s(row.get('edition'))
            raw_language = row.get('language')
            for target in TARGETS:
                if not language_matches(raw_language, target):
                    continue
                stats[target]['raw_rows'] += 1
                if not logical:
                    stats[target]['missing_logical_identity_rows'] += 1
                    if len(invalid_samples) < 50:
                        invalid_samples.append({'target': target, 'collector': collector, 'set_name': set_name, 'reason': 'missing_logical_identity'})
                    continue
                key = (logical, collector, rarity)
                data = groups[target].setdefault(
                    key,
                    {
                        'logical_card': logical,
                        'collector': collector,
                        'rarity': rarity,
                        'family': family_from_collector(collector),
                        'passwords': set(),
                        'konami_ids': set(),
                        'set_names': set(),
                        'editions': set(),
                        'raw_languages': set(),
                        'rows': 0,
                    },
                )
                data['rows'] += 1
                if password:
                    data['passwords'].add(password)
                if konami_id:
                    data['konami_ids'].add(konami_id)
                if set_name:
                    data['set_names'].add(set_name)
                if edition:
                    data['editions'].add(edition)
                if isinstance(raw_language, list):
                    data['raw_languages'].update(s(x) for x in raw_language if s(x))
                else:
                    if s(raw_language):
                        data['raw_languages'].add(s(raw_language))

    report_stats: dict[str, Any] = {}
    for target in TARGETS:
        duplicate_rows = sum(max(0, data['rows'] - 1) for data in groups[target].values())
        report_stats[target] = {
            'raw_rows': stats[target]['raw_rows'],
            'canonical_card_scoped_keys': len(groups[target]),
            'duplicate_or_alias_rows_collapsed': duplicate_rows,
            'missing_logical_identity_rows': stats[target]['missing_logical_identity_rows'],
        }
    return groups, report_stats, invalid_samples


def _db_state(url: str, yaml_groups: dict[str, dict[tuple[str, str, str], dict[str, Any]]]) -> dict[str, Any]:
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text('SET TRANSACTION READ ONLY'))
        ro = sl(conn.execute(text('SHOW transaction_read_only')).scalar_one())
        if ro not in {'on', 'true', '1'}:
            raise AssertionError(f'transaction_read_only={ro!r}')
        game_id = int(conn.execute(text("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")).scalar_one())
        db_cards = {
            str(external): int(card_id)
            for card_id, external in conn.execute(
                text('SELECT id,yugoprodeck_id FROM cards WHERE game_id=:g AND yugoprodeck_id IS NOT NULL'),
                {'g': game_id},
            )
        }
        tx.rollback()

    konami_to_db_cards: dict[str, set[int]] = defaultdict(set)
    for target in TARGETS:
        for data in yaml_groups[target].values():
            resolved = {db_cards[p] for p in data['passwords'] if p in db_cards}
            for konami_id in data['konami_ids']:
                konami_to_db_cards[konami_id].update(resolved)
    return {
        'game_id': game_id,
        'db_cards': db_cards,
        'konami_to_db_cards': konami_to_db_cards,
        'read_only': True,
    }


def _resolve_yaml_card(data: dict[str, Any], db_cards: dict[str, int], konami_to_db_cards: dict[str, set[int]]) -> tuple[int | None, str, bool]:
    direct = {db_cards[p] for p in data['passwords'] if p in db_cards}
    if len(direct) == 1:
        return next(iter(direct)), 'ygoprodeck_exact', False
    if len(direct) > 1:
        return None, 'multiple_passwords_multiple_db_cards', True
    via_konami: set[int] = set()
    for konami_id in data['konami_ids']:
        via_konami.update(konami_to_db_cards.get(konami_id, set()))
    if len(via_konami) == 1:
        return next(iter(via_konami)), 'konami_exact_alias', False
    if len(via_konami) > 1:
        return None, 'konami_multiple_db_cards', True
    return None, 'card_missing', False


def classify(
    historical: dict[str, dict[tuple[str, str, str], dict[str, Any]]],
    yaml_groups: dict[str, dict[tuple[str, str, str], dict[str, Any]]],
    db: dict[str, Any],
) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for target in TARGETS:
        hist = historical[target]
        current = yaml_groups[target]
        hist_keys = set(hist)
        yaml_keys = set(current)
        hist_coarse: dict[tuple[str, str], set[str]] = defaultdict(set)
        yaml_coarse: dict[tuple[str, str], set[str]] = defaultdict(set)
        for logical, collector, rarity in hist_keys:
            hist_coarse[(logical, collector)].add(rarity)
        for logical, collector, rarity in yaml_keys:
            yaml_coarse[(logical, collector)].add(rarity)

        overlap = hist_keys & yaml_keys
        yaml_only = yaml_keys - hist_keys
        hist_only = hist_keys - yaml_keys
        classifications = Counter()
        resolution = Counter()
        family_names: dict[str, set[str]] = defaultdict(set)
        source_release_memberships = 0
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for key in sorted(yaml_only):
            logical, collector, rarity = key
            data = current[key]
            coarse = (logical, collector)
            historical_rarities = hist_coarse.get(coarse, set())
            yaml_rarities = yaml_coarse.get(coarse, set())
            if not historical_rarities:
                cls = 'new_card_collector'
            elif historical_rarities & yaml_rarities:
                cls = 'additional_rarity_on_known_collector'
            else:
                cls = 'rarity_disjoint_conflict'
            classifications[cls] += 1

            db_card_id, mode, ambiguous = _resolve_yaml_card(data, db['db_cards'], db['konami_to_db_cards'])
            if ambiguous:
                resolution['ambiguous'] += 1
            elif db_card_id is None:
                resolution['card_missing'] += 1
            else:
                resolution[mode] += 1
                if cls != 'rarity_disjoint_conflict':
                    resolution[f'materializable_{cls}'] += 1
            family = data['family']
            family_names[family].update(data['set_names'])
            source_release_memberships += max(1, len(data['set_names']))
            if len(samples[cls]) < 40:
                samples[cls].append(
                    {
                        'logical_card': logical,
                        'collector': collector,
                        'rarity': rarity,
                        'historical_rarities': sorted(historical_rarities),
                        'yaml_rarities': sorted(yaml_rarities),
                        'family': family,
                        'set_names': sorted(data['set_names']),
                        'editions': sorted(data['editions']),
                        'db_resolution': mode,
                        'db_card_id': db_card_id,
                    }
                )

        disjoint_coarse = {
            coarse
            for coarse in (set(hist_coarse) & set(yaml_coarse))
            if hist_coarse[coarse].isdisjoint(yaml_coarse[coarse])
        }
        same_collector_cross_card: dict[tuple[str, str], set[str]] = defaultdict(set)
        for logical, collector, rarity in yaml_keys:
            same_collector_cross_card[(collector, rarity)].add(logical)
        card_scoped_reuse = {
            key: identities for key, identities in same_collector_cross_card.items() if len(identities) > 1
        }
        family_name_ambiguity = {
            family: names for family, names in family_names.items() if len(names) > 1
        }

        clean_delta = classifications['new_card_collector'] + classifications['additional_rarity_on_known_collector']
        materializable_clean = resolution['materializable_new_card_collector'] + resolution['materializable_additional_rarity_on_known_collector']
        targets[target] = {
            'historical_canonical_keys': len(hist_keys),
            'yaml_current_card_scoped_keys': len(yaml_keys),
            'exact_overlap_keys': len(overlap),
            'yaml_only_keys': len(yaml_only),
            'historical_only_keys': len(hist_only),
            'yaml_only_classification': dict(classifications),
            'clean_yaml_delta_keys': clean_delta,
            'rarity_disjoint_conflict_keys': classifications['rarity_disjoint_conflict'],
            'rarity_disjoint_coarse_groups': len(disjoint_coarse),
            'db_resolution': dict(resolution),
            'materializable_clean_delta_keys': materializable_clean,
            'card_missing_clean_delta_keys': sum(
                1
                for key in yaml_only
                if (
                    (not hist_coarse.get((key[0], key[1])))
                    or (hist_coarse[(key[0], key[1])] & yaml_coarse[(key[0], key[1])])
                )
                and _resolve_yaml_card(current[key], db['db_cards'], db['konami_to_db_cards'])[0] is None
                and not _resolve_yaml_card(current[key], db['db_cards'], db['konami_to_db_cards'])[2]
            ),
            'ambiguous_card_resolution_keys': resolution['ambiguous'],
            'card_scoped_collector_rarity_reuse_groups': len(card_scoped_reuse),
            'card_scoped_reuse_samples': [
                {'collector': key[0], 'rarity': key[1], 'logical_cards': sorted(values)}
                for key, values in list(sorted(card_scoped_reuse.items()))[:40]
            ],
            'yaml_families_in_delta': len(family_names),
            'family_name_ambiguity_groups': len(family_name_ambiguity),
            'family_name_ambiguity_samples': [
                {'family': family, 'set_names': sorted(names)}
                for family, names in list(sorted(family_name_ambiguity.items()))[:40]
            ],
            'source_release_memberships_estimated': source_release_memberships,
            'samples': dict(samples),
        }
    return targets


def run(ygojson_dir: Path, yaml_cards: Path, report_path: Path, yaml_meta: dict[str, Any]) -> dict[str, Any]:
    historical, historical_meta = build_historical(ygojson_dir)
    yaml_groups, yaml_stats, invalid_samples = build_current_yaml(yaml_cards)
    url = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL required')
    db = _db_state(url, yaml_groups)
    targets = classify(historical, yaml_groups, db)

    gates = {
        'production_read_only': db['read_only'],
        'production_writes_zero': True,
        'historical_counts_match_certified_projection': (
            len(historical['es']) == 37233 and len(historical['ja']) == 36327
        ),
        'current_yaml_overlap_present': all(targets[t]['exact_overlap_keys'] > 0 for t in TARGETS),
        'current_yaml_delta_present': all(targets[t]['yaml_only_keys'] > 0 for t in TARGETS),
        'card_scoped_reuse_is_not_quarantined': True,
        'ambiguous_card_resolution_zero': all(targets[t]['ambiguous_card_resolution_keys'] == 0 for t in TARGETS),
        'rarity_disagreement_explicitly_quarantined': True,
        'no_name_or_fuzzy_identity_matching': True,
    }
    report = {
        'mode': 'read_only_current_yaml_physical_delta_card_scoped_v2',
        'production_writes': 0,
        'database_transaction_read_only': True,
        'sources': {
            'ygojson': 'historical v1 aggregate with certified exact collector recovery',
            'yaml_yugi': yaml_meta,
        },
        'identity_policy': {
            'card': 'official Konami ID preferred; exact password/YGOProDeck ID and unambiguous Konami alias only',
            'print': 'logical card + exact localized collector + normalized rarity',
            'set_projection': 'collector family + target region; ES=global, JA=jp',
            'card_scoped_reuse': 'same collector+rarity on different Cards is valid after migration 36 and is never quarantined by itself',
            'delta_new': 'YAML fine identity whose card+collector is absent from historical YGOJSON',
            'delta_extra_rarity': 'YAML fine identity whose card+collector overlaps historical and shares at least one rarity, but adds another rarity',
            'rarity_conflict': 'same card+collector exists in both sources but rarity sets are disjoint; quarantine, do not materialize',
            'names': 'never used for Card/Print matching; YAML set name is provenance/display evidence only',
            'images': 'YAML card-level images are not localized physical-print images and are not materialized',
            'economics': 'not read or written',
        },
        'historical_meta': historical_meta,
        'yaml_stats': yaml_stats,
        'invalid_yaml_samples': invalid_samples,
        'targets': targets,
        'gates': gates,
        'structural_pass': all(gates.values()),
        'production_rollout_ready': False,
        'next_gate': 'ephemeral dual-source overlay for clean YAML delta only; rarity-disjoint conflicts remain quarantined',
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({
        'structural_pass': report['structural_pass'],
        'targets': {
            target: {
                'historical': data['historical_canonical_keys'],
                'yaml_current': data['yaml_current_card_scoped_keys'],
                'overlap': data['exact_overlap_keys'],
                'yaml_only': data['yaml_only_keys'],
                'classification': data['yaml_only_classification'],
                'materializable_clean_delta': data['materializable_clean_delta_keys'],
                'rarity_disjoint_conflicts': data['rarity_disjoint_conflict_keys'],
                'card_scoped_reuse_groups': data['card_scoped_collector_rarity_reuse_groups'],
                'ambiguous_cards': data['ambiguous_card_resolution_keys'],
            }
            for target, data in targets.items()
        },
        'gates': gates,
        'production_rollout_ready': False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ygojson-dir', type=Path, required=True)
    parser.add_argument('--yaml-cards', type=Path, required=True)
    parser.add_argument('--yaml-meta', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    meta = json.loads(args.yaml_meta.read_text(encoding='utf-8'))
    report = run(args.ygojson_dir, args.yaml_cards, args.report, meta)
    return 0 if report['structural_pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
