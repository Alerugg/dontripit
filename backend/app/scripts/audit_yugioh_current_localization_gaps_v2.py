from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from app.scripts import certify_yugioh_current_physical_delta_ephemeral_v2 as cert
from app.scripts import audit_yugioh_current_physical_delta_v2 as delta
from app.scripts.audit_yugioh_multilingual_yaml_yugi_current import iter_cards, mapping, s


def _tcg_release_status(cards_path: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for card in iter_cards(cards_path):
        logical = delta.yaml_logical_identity(card)
        if not logical:
            continue
        regulation = mapping(card.get('limit_regulation'))
        raw = s(regulation.get('tcg'))
        out[logical] = raw or None
    return out


def run(root: Path, cards_path: Path, source_meta_path: Path, output: Path) -> dict[str, Any]:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL or DATABASE_URL required')
    source_meta = json.loads(source_meta_path.read_text(encoding='utf-8'))
    plan = cert.build_plan(root, cards_path, source_meta, target_url)
    release_status = _tcg_release_status(cards_path)

    targets: dict[str, Any] = {}
    for target in cert.base.TARGETS:
        missing_name: list[dict[str, Any]] = []
        true_missing_text: list[dict[str, Any]] = []
        text_conflicts: list[dict[str, Any]] = []
        for item in plan['targets'][target]['materializable']:
            details = item['localization_details']
            status = release_status.get(item['logical_card'])
            sample = {
                'print_key': item['print_key'],
                'logical_card': item['logical_card'],
                'collector': item['collector'],
                'rarity': item['rarity'],
                'family': item['family'],
                'set_name': item['set_name'],
                'card_id': item['card_id'],
                'tcg_release_status': status,
                'tcg_not_yet_released': (status or '').casefold() == 'not yet released',
            }
            if not item.get('card_name'):
                missing_name.append(sample)
            if details.get('localized_text_conflict'):
                conflict = dict(sample)
                conflict['candidate_texts'] = details.get('localized_text_conflict_values') or []
                text_conflicts.append(conflict)
            elif details.get('localized_text') is None:
                true_missing_text.append(sample)

        targets[target] = {
            'materializable': len(plan['targets'][target]['materializable']),
            'missing_name_count': len(missing_name),
            'missing_name_prerelease_count': sum(x['tcg_not_yet_released'] for x in missing_name),
            'true_missing_text_count': len(true_missing_text),
            'true_missing_text_prerelease_count': sum(x['tcg_not_yet_released'] for x in true_missing_text),
            'text_conflict_count': len(text_conflicts),
            'missing_name': missing_name,
            'true_missing_text': true_missing_text,
            'text_conflicts': text_conflicts,
        }

    es = targets['es']
    ja = targets['ja']
    gates = {
        'exact_materializable_counts': es['materializable'] == 3938 and ja['materializable'] == 5350,
        'ja_localization_gaps_zero': ja['missing_name_count'] == 0 and ja['true_missing_text_count'] == 0 and ja['text_conflict_count'] == 0,
        'es_missing_names_exact_64': es['missing_name_count'] == 64,
        'es_all_missing_names_are_tcg_prerelease': es['missing_name_prerelease_count'] == es['missing_name_count'] == 64,
        'es_true_missing_text_exact_74': es['true_missing_text_count'] == 74,
        'es_all_true_missing_text_is_tcg_prerelease': es['true_missing_text_prerelease_count'] == es['true_missing_text_count'] == 74,
        'es_conflicts_exact_1': es['text_conflict_count'] == 1,
        'es_conflict_is_not_prerelease': len(es['text_conflicts']) == 1 and not es['text_conflicts'][0]['tcg_not_yet_released'],
        'es_conflict_values_preserved': len(es['text_conflicts']) == 1 and len(es['text_conflicts'][0].get('candidate_texts') or []) >= 2,
    }

    report = {
        'status': 'pass' if all(gates.values()) else 'fail',
        'mode': 'read-only-current-localization-gap-release-status-audit-v2',
        'source_meta': source_meta,
        'production_writes': 0,
        'targets': targets,
        'gates': gates,
        'policy': {
            'missing_localized_fields_for_tcg_not_yet_released_cards': 'allowed-as-explicit-prerelease-pending; never synthesize or copy English',
            'conflicting_localized_fields': 'retain candidate source values as evidence; do not choose arbitrarily',
            'released_tcg_true_missing_localized_fields': 'blocking',
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({
        'status': report['status'],
        'production_writes': 0,
        'gates': gates,
        'es': {k: v for k, v in es.items() if not isinstance(v, list)},
        'ja': {k: v for k, v in ja.items() if not isinstance(v, list)},
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ygojson-dir', type=Path, required=True)
    ap.add_argument('--yaml-cards', type=Path, required=True)
    ap.add_argument('--yaml-meta', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    return 0 if run(args.ygojson_dir, args.yaml_cards, args.yaml_meta, args.output)['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
