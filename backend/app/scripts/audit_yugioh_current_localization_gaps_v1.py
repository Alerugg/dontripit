from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from app.scripts import certify_yugioh_current_physical_delta_ephemeral_v2 as cert


def run(root: Path, cards_path: Path, source_meta_path: Path, output: Path) -> dict[str, Any]:
    target_url = os.getenv('EPHEMERAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not target_url:
        raise RuntimeError('EPHEMERAL_DATABASE_URL or DATABASE_URL required')
    source_meta = json.loads(source_meta_path.read_text(encoding='utf-8'))
    plan = cert.build_plan(root, cards_path, source_meta, target_url)

    targets: dict[str, Any] = {}
    for target in cert.base.TARGETS:
        missing_name: list[dict[str, Any]] = []
        missing_text: list[dict[str, Any]] = []
        text_conflicts: list[dict[str, Any]] = []
        for item in plan['targets'][target]['materializable']:
            details = item['localization_details']
            sample = {
                'print_key': item['print_key'],
                'logical_card': item['logical_card'],
                'collector': item['collector'],
                'rarity': item['rarity'],
                'family': item['family'],
                'set_name': item['set_name'],
                'card_id': item['card_id'],
            }
            if not item.get('card_name'):
                missing_name.append(sample)
            if details.get('localized_text') is None:
                missing_text.append(sample)
            if details.get('localized_text_conflict'):
                conflict = dict(sample)
                conflict['candidate_texts'] = details.get('localized_text_conflict_values') or []
                text_conflicts.append(conflict)

        targets[target] = {
            'materializable': len(plan['targets'][target]['materializable']),
            'missing_name_count': len(missing_name),
            'missing_text_count': len(missing_text),
            'text_conflict_count': len(text_conflicts),
            'true_missing_text_count': len(missing_text) - len(text_conflicts),
            'missing_name': missing_name,
            'missing_text': missing_text,
            'text_conflicts': text_conflicts,
        }

    report = {
        'status': 'pass',
        'mode': 'read-only-current-localization-gap-audit-v1',
        'source_meta': source_meta,
        'production_writes': 0,
        'targets': targets,
    }
    assert targets['es']['materializable'] == 3938, report
    assert targets['ja']['materializable'] == 5350, report
    assert targets['es']['missing_name_count'] == 64, report
    assert targets['es']['missing_text_count'] == 75, report
    assert targets['ja']['missing_name_count'] == 0, report
    assert targets['ja']['missing_text_count'] == 0, report

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({
        'status': report['status'],
        'production_writes': 0,
        'es': {k: v for k, v in targets['es'].items() if not isinstance(v, list)},
        'ja': {k: v for k, v in targets['ja'].items() if not isinstance(v, list)},
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
