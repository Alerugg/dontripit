from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.scripts import audit_yugioh_multilingual_db_compatibility_v2 as identity
from app.scripts.audit_yugioh_multilingual_db_compatibility_v3 import (
    build_source_with_exact_collector_recovery,
)
from app.scripts.audit_yugioh_multilingual_yaml_yugi_current import iter_cards
from app.scripts import certify_yugioh_multilingual_ephemeral_v1 as v1
from app.scripts.seed_yugioh_multilingual_ephemeral_lean_v1 import BASELINE_PATH


def _s(value: Any) -> str:
    return str(value or '').strip()


def _build_yaml_name_index(cards_path: Path) -> dict[str, dict[str, dict[str, set[str]]]]:
    index: dict[str, dict[str, dict[str, set[str]]]] = {
        lang: {
            'ygoprodeck': defaultdict(set),
            'konami': defaultdict(set),
        }
        for lang in ('es', 'ja')
    }
    for card in iter_cards(cards_path):
        names = card.get('name') if isinstance(card.get('name'), dict) else {}
        password = identity.canonical_ygo_id(card.get('password'))
        konami_id = _s(card.get('konami_id'))
        for lang in ('es', 'ja'):
            name = _s(names.get(lang))
            if not name:
                continue
            if password:
                index[lang]['ygoprodeck'][password].add(name)
            if konami_id:
                index[lang]['konami'][konami_id].add(name)
    return index


def _yaml_candidates_for_source_cards(
    *,
    language: str,
    source_card_ids: list[str],
    source_cards: dict[str, dict[str, str]],
    yaml_index: dict[str, dict[str, dict[str, set[str]]]],
) -> tuple[set[str], list[dict[str, str]]]:
    candidates: set[str] = set()
    evidence: list[dict[str, str]] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    for card_uuid in source_card_ids:
        source = source_cards.get(card_uuid) or {}
        ygo_id = identity.canonical_ygo_id(source.get('ygoprodeck'))
        konami_id = _s(source.get('konami'))
        for namespace, external_id in (('ygoprodeck', ygo_id), ('konami', konami_id)):
            if not external_id:
                continue
            values = yaml_index[language][namespace].get(external_id, set())
            for value in sorted(values):
                candidates.add(value)
                key = (namespace, external_id, value)
                if key not in seen_evidence:
                    seen_evidence.add(key)
                    evidence.append({
                        'namespace': namespace,
                        'external_id': external_id,
                        'localized_name': value,
                    })
    return candidates, evidence


def enrich_plan_with_yaml_names(
    plan: dict[str, Any],
    *,
    root: Path,
    yaml_cards_path: Path,
    yaml_source_meta: dict[str, Any],
) -> dict[str, Any]:
    source_cards, _source_rows, _legacy = build_source_with_exact_collector_recovery(root)
    yaml_index = _build_yaml_name_index(yaml_cards_path)

    conflicts: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, int]] = {}
    for target in ('es', 'ja'):
        data = plan['targets'][target]
        language = data['language']
        counts = {
            'materializable_prints': len(data['materializable']),
            'name_from_ygojson': 0,
            'name_supplemented_yaml_yugi': 0,
            'name_unavailable_exact_sources': 0,
        }
        for item in data['materializable']:
            details = item.setdefault('localization_details', {})
            provenance = details.setdefault('field_provenance', {})
            provenance['physical_identity'] = 'ygojson'
            provenance['release_memberships'] = 'ygojson'
            provenance['images'] = 'ygojson-locale'
            provenance['effect'] = 'ygojson' if details.get('effect') else None
            provenance['pendulum_effect'] = 'ygojson' if details.get('pendulum_effect') else None
            provenance['official_flag'] = 'ygojson' if details.get('official') is not None else None

            if item.get('card_name'):
                counts['name_from_ygojson'] += 1
                item['card_name_source'] = 'ygojson'
                provenance['card_name'] = 'ygojson'
                continue

            source_card_ids = [str(v) for v in details.get('source_card_ids') or []]
            candidates, evidence = _yaml_candidates_for_source_cards(
                language=language,
                source_card_ids=source_card_ids,
                source_cards=source_cards,
                yaml_index=yaml_index,
            )
            if len(candidates) == 1:
                localized_name = next(iter(candidates))
                item['card_name'] = localized_name
                item['card_name_source'] = 'yaml-yugi'
                provenance['card_name'] = 'yaml-yugi'
                details['yaml_yugi_name_evidence'] = evidence
                details['yaml_yugi_snapshot'] = {
                    'sha256': yaml_source_meta.get('sha256'),
                    'head': yaml_source_meta.get('head'),
                    'pushed_at': yaml_source_meta.get('pushed_at'),
                }
                counts['name_supplemented_yaml_yugi'] += 1
            elif not candidates:
                item['card_name_source'] = None
                provenance['card_name'] = None
                details['localized_name_status'] = 'unavailable-from-exact-sources'
                counts['name_unavailable_exact_sources'] += 1
            else:
                conflicts.append({
                    'target': target,
                    'language': language,
                    'print_key': item['print_key'],
                    'logical_card': item['logical_card'],
                    'collector': item['collector'],
                    'source_card_ids': source_card_ids,
                    'candidate_names': sorted(candidates),
                    'evidence': evidence,
                })
        accounted = (
            counts['name_from_ygojson']
            + counts['name_supplemented_yaml_yugi']
            + counts['name_unavailable_exact_sources']
        )
        counts['accounted'] = accounted
        coverage[target] = counts

    gates = dict(plan.get('structural_gates') or {})
    gates.pop('localized_card_names_complete', None)
    gates['yaml_yugi_name_identity_conflicts_zero'] = not conflicts
    gates['localized_name_coverage_accounted'] = all(
        values['accounted'] == values['materializable_prints'] for values in coverage.values()
    )
    gates['no_synthesized_localized_names'] = True
    gates['field_level_name_provenance_recorded'] = all(
        'card_name' in item.get('localization_details', {}).get('field_provenance', {})
        for target in ('es', 'ja')
        for item in plan['targets'][target]['materializable']
    )
    plan['structural_gates'] = gates
    plan['structural_pass'] = all(gates.values())
    plan['localized_name_coverage'] = coverage
    plan['yaml_yugi_name_conflicts'] = conflicts[:100]
    plan['yaml_yugi_source'] = yaml_source_meta
    plan['missing_localized_card_names'] = sum(
        values['name_unavailable_exact_sources'] for values in coverage.values()
    )
    return plan


def run(
    root: Path,
    yaml_cards_path: Path,
    yaml_source_meta_path: Path,
    output: Path,
) -> dict[str, Any]:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL or DATABASE_URL is required')
    baseline = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
    yaml_source_meta = json.loads(yaml_source_meta_path.read_text(encoding='utf-8'))
    plan = v1.build_plan(root, target_url)
    plan = enrich_plan_with_yaml_names(
        plan,
        root=root,
        yaml_cards_path=yaml_cards_path,
        yaml_source_meta=yaml_source_meta,
    )

    summary = {
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
        'localized_name_coverage': plan['localized_name_coverage'],
        'yaml_yugi_name_conflicts': plan['yaml_yugi_name_conflicts'],
        'yaml_yugi_source': plan['yaml_yugi_source'],
    }

    if not plan['structural_pass']:
        report = {
            'status': 'fail',
            'mode': 'ephemeral-only-yugioh-multilingual-certification-v2',
            'production_writes': 0,
            'baseline': baseline,
            'source_freshness': plan['source_freshness'],
            'rollout_freshness_pass': plan['rollout_freshness_pass'],
            'production_rollout_ready': False,
            'plan_summary': summary,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True))
        return report

    pass1 = v1.apply_plan(plan, target_url)
    validation = v1.validate(plan, target_url, baseline)
    pass2 = v1.apply_plan(plan, target_url)
    pass2_zero_writes = pass2['total_writes'] == 0
    status = 'pass' if validation['status'] == 'pass' and pass2_zero_writes else 'fail'
    report = {
        'status': status,
        'mode': 'ephemeral-only-yugioh-multilingual-certification-v2',
        'production_writes': 0,
        'production_source_mode': 'read-only-clone-at-schema-34',
        'baseline': baseline,
        'source_freshness': plan['source_freshness'],
        'rollout_freshness_pass': plan['rollout_freshness_pass'],
        'production_rollout_ready': False,
        'identity_contract': {
            'set': 'family+region (ES=global, JA=jp)',
            'print': 'Card+Set+collector+language+is_foil=false+rarity-derived variant',
            'physical_source': 'historical YGOJSON aggregate v1 only',
            'localized_name': 'YGOJSON first; exact-ID YAML Yugi supplement only; otherwise NULL',
            'field_provenance': 'PrintLocalization.details_json.field_provenance',
            'source_print_uuid': 'PrintRelease.source_print_id only; never Print.yugioh_id',
            'images': 'YGOJSON locale-specific cardInfo/cardImages only',
            'economics': 'all source price fields ignored; economics tables neither copied nor written',
        },
        'plan_summary': summary,
        'pass1': pass1,
        'validation': validation,
        'pass2': pass2,
        'pass2_zero_writes': pass2_zero_writes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'status': status,
        'production_writes': 0,
        'source_freshness': report['source_freshness'],
        'yaml_yugi_source': plan['yaml_yugi_source'],
        'localized_name_coverage': plan['localized_name_coverage'],
        'structural_gates': plan['structural_gates'],
        'pass1': pass1,
        'validation': validation,
        'pass2': pass2,
        'pass2_zero_writes': pass2_zero_writes,
        'production_rollout_ready': False,
    }, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--yaml-yugi-cards', type=Path, required=True)
    parser.add_argument('--yaml-yugi-meta', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.input_dir, args.yaml_yugi_cards, args.yaml_yugi_meta, args.output)
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
