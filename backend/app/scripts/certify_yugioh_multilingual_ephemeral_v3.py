from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.scripts import certify_yugioh_multilingual_ephemeral_v2 as v2


STALE_BASELINE_MINIMUMS = {
    'es': {'canonical_prints': 37233, 'materializable_prints': 37007},
    'ja': {'canonical_prints': 36327, 'materializable_prints': 35591},
    'source_memberships': 74368,
}


def run(
    root: Path,
    yaml_cards_path: Path,
    yaml_source_meta_path: Path,
    source_evidence_path: Path,
    output: Path,
) -> dict[str, Any]:
    source_evidence = json.loads(source_evidence_path.read_text(encoding='utf-8'))
    assert source_evidence.get('status') == 'pass', source_evidence
    assert source_evidence.get('production_writes') == 0, source_evidence
    assert source_evidence.get('upstream_validation_passed') is True, source_evidence
    assert source_evidence.get('partitions_imported') == 8, source_evidence

    report = v2.run(root, yaml_cards_path, yaml_source_meta_path, output)
    plan = report.get('plan_summary') or {}
    source_freshness = report.get('source_freshness') or {}
    validation = report.get('validation') or {}

    gates = {
        'base_certification_pass': report.get('status') == 'pass',
        'source_evidence_pass': source_evidence.get('status') == 'pass',
        'upstream_validation_passed': source_evidence.get('upstream_validation_passed') is True,
        'all_eight_yugipedia_partitions_imported': source_evidence.get('partitions_imported') == 8,
        'freshness_current_enough': source_freshness.get('status') == 'current_enough',
        'freshness_age_within_limit': isinstance(source_freshness.get('age_days'), int)
            and source_freshness['age_days'] <= source_freshness.get('max_rollout_age_days', 7),
        'structural_gates_all_pass': bool(plan.get('structural_gates')) and all(plan['structural_gates'].values()),
        'validation_gates_all_pass': validation.get('status') == 'pass'
            and bool(validation.get('gates')) and all(validation['gates'].values()),
        'pass2_zero_writes': report.get('pass2_zero_writes') is True
            and (report.get('pass2') or {}).get('total_writes') == 0,
        'no_production_writes': report.get('production_writes') == 0,
        'es_no_regression_vs_stale_snapshot':
            (plan.get('targets') or {}).get('es', {}).get('canonical_prints', 0) >= STALE_BASELINE_MINIMUMS['es']['canonical_prints']
            and (plan.get('targets') or {}).get('es', {}).get('materializable_prints', 0) >= STALE_BASELINE_MINIMUMS['es']['materializable_prints'],
        'ja_no_regression_vs_stale_snapshot':
            (plan.get('targets') or {}).get('ja', {}).get('canonical_prints', 0) >= STALE_BASELINE_MINIMUMS['ja']['canonical_prints']
            and (plan.get('targets') or {}).get('ja', {}).get('materializable_prints', 0) >= STALE_BASELINE_MINIMUMS['ja']['materializable_prints'],
        'membership_no_regression_vs_stale_snapshot': plan.get('source_memberships', 0) >= STALE_BASELINE_MINIMUMS['source_memberships'],
        'economics_untouched': (validation.get('lean_invariants') or {}).get('economics_untouched') is True,
    }

    fresh_source_certified = all(gates.values())
    report['mode'] = 'ephemeral-only-yugioh-multilingual-certification-v3-fresh-source'
    report['fresh_source_evidence'] = source_evidence
    report['fresh_source_gates'] = gates
    report['fresh_source_certified'] = fresh_source_certified
    # Rollout remains a separate multi-artifact decision. This certificate never writes production.
    report['production_rollout_ready'] = False
    identity_contract = report.setdefault('identity_contract', {})
    identity_contract['physical_source'] = (
        'fresh shadow-regenerated YGOJSON aggregate from pinned upstream with minimal compatibility patches; '
        'official upstream validation passed'
    )
    identity_contract['freshness_policy'] = 'YGOJSON snapshot cutoff <= 7 days; no stale-release fallback for rollout'
    identity_contract['production_write_policy'] = 'forbidden in certification; final rollout requires separate readiness gate'
    report['status'] = 'pass' if fresh_source_certified else 'fail'

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({
        'status': report['status'],
        'fresh_source_certified': fresh_source_certified,
        'source_freshness': source_freshness,
        'fresh_source_gates': gates,
        'targets': plan.get('targets'),
        'source_memberships': plan.get('source_memberships'),
        'pass1': report.get('pass1'),
        'pass2': report.get('pass2'),
        'production_writes': report.get('production_writes'),
        'production_rollout_ready': False,
    }, ensure_ascii=False, indent=2, default=str, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--yaml-yugi-cards', type=Path, required=True)
    parser.add_argument('--yaml-yugi-meta', type=Path, required=True)
    parser.add_argument('--source-evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.input_dir,
        args.yaml_yugi_cards,
        args.yaml_yugi_meta,
        args.source_evidence,
        args.output,
    )
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
