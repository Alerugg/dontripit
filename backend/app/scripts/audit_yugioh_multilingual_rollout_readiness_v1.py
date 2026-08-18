from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def run(fresh_path: Path, overlay_path: Path, gaps_path: Path, output: Path) -> dict[str, Any]:
    fresh = _load(fresh_path)
    overlay = _load(overlay_path)
    gaps = _load(gaps_path)

    fplan = fresh.get('plan_summary') or {}
    fval = fresh.get('validation') or {}
    oplan = overlay.get('plan_summary') or {}
    oval = overlay.get('validation') or {}
    gap_gates = gaps.get('gates') or {}

    gates = {
        'fresh_historical_certification_pass': fresh.get('status') == 'pass' and fresh.get('fresh_source_certified') is True,
        'fresh_historical_source_current_enough': (fresh.get('source_freshness') or {}).get('status') == 'current_enough',
        'fresh_historical_structural_gates_pass': bool(fplan.get('structural_gates')) and all(fplan['structural_gates'].values()),
        'fresh_historical_validation_pass': fval.get('status') == 'pass' and bool(fval.get('gates')) and all(fval['gates'].values()),
        'fresh_historical_idempotent': fresh.get('pass2_zero_writes') is True and (fresh.get('pass2') or {}).get('total_writes') == 0,
        'fresh_historical_economics_untouched': (fval.get('lean_invariants') or {}).get('economics_untouched') is True,
        'current_overlay_certification_pass': overlay.get('status') == 'pass',
        'current_overlay_exact_9288': (oplan.get('overlay_counts') or {}) == {'es': 3938, 'ja': 5350}
            and oval.get('materialized_unique_prints') == 9288,
        'current_overlay_quarantine_exact_3314': sum((oplan.get('quarantine') or {}).get(t, {}).get('total', 0) for t in ('es','ja')) == 3314,
        'current_overlay_validation_pass': oval.get('status') == 'pass' and bool(oval.get('gates')) and all(oval['gates'].values()),
        'current_overlay_idempotent': overlay.get('pass2_zero_writes') is True and (overlay.get('pass2') or {}).get('total_writes') == 0,
        'current_overlay_economics_untouched': (oval.get('lean_invariants') or {}).get('economics_untouched') is True,
        'localization_gap_policy_pass': gaps.get('status') == 'pass' and bool(gap_gates) and all(gap_gates.values()),
        'ja_current_localization_complete': (gaps.get('targets') or {}).get('ja', {}).get('missing_name_count') == 0
            and (gaps.get('targets') or {}).get('ja', {}).get('true_missing_text_count') == 0,
        'es_true_missing_fields_are_prerelease_only':
            (gaps.get('targets') or {}).get('es', {}).get('missing_name_count')
                == (gaps.get('targets') or {}).get('es', {}).get('missing_name_prerelease_count') == 64
            and (gaps.get('targets') or {}).get('es', {}).get('true_missing_text_count')
                == (gaps.get('targets') or {}).get('es', {}).get('true_missing_text_prerelease_count') == 74,
        'published_es_conflict_preserved_not_synthesized': (gaps.get('targets') or {}).get('es', {}).get('text_conflict_count') == 1
            and gap_gates.get('es_conflict_values_preserved') is True,
        'all_certifications_zero_production_writes': fresh.get('production_writes') == 0
            and overlay.get('production_writes') == 0 and gaps.get('production_writes') == 0,
    }

    ready = all(gates.values())
    report = {
        'status': 'pass' if ready else 'fail',
        'mode': 'yugioh-multilingual-controlled-rollout-readiness-v1',
        'production_writes': 0,
        'controlled_rollout_candidate_ready': ready,
        'automatic_production_write_authorized': False,
        'gates': gates,
        'evidence': {
            'fresh_historical': {
                'source_freshness': fresh.get('source_freshness'),
                'targets': fplan.get('targets'),
                'source_memberships': fplan.get('source_memberships'),
                'pass1': fresh.get('pass1'),
                'pass2': fresh.get('pass2'),
            },
            'current_overlay': {
                'overlay_counts': oplan.get('overlay_counts'),
                'quarantine': oplan.get('quarantine'),
                'materialized_unique_prints': oval.get('materialized_unique_prints'),
                'membership_rows': oval.get('membership_rows'),
            },
            'localization_gaps': {
                'es': {k: v for k, v in (gaps.get('targets') or {}).get('es', {}).items() if not isinstance(v, list)},
                'ja': {k: v for k, v in (gaps.get('targets') or {}).get('ja', {}).items() if not isinstance(v, list)},
            },
        },
        'policy': {
            'certification_environment': 'PostgreSQL 16 production-shaped clone; production READ ONLY',
            'production_execution': 'not performed by this gate',
            'prerelease_localization': 'retain NULL/pending until exact source publishes localization; never synthesize from English',
            'conflicting_localization': 'retain source candidates as evidence; never choose arbitrarily',
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fresh-historical', type=Path, required=True)
    ap.add_argument('--current-overlay', type=Path, required=True)
    ap.add_argument('--localization-gaps', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    report = run(args.fresh_historical, args.current_overlay, args.localization_gaps, args.output)
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
