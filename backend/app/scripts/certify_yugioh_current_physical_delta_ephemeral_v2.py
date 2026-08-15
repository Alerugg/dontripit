from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from app.scripts import certify_yugioh_current_physical_delta_ephemeral_v1 as base
from app.scripts import audit_yugioh_current_physical_delta_v2 as delta
from app.scripts.audit_yugioh_multilingual_cross_source_reconciliation import norm_rarity
from app.scripts.audit_yugioh_multilingual_yaml_yugi_current import iter_cards, mapping, s

CONFLICT_KEY = '__yaml_yugi_text_conflict__'
ORIGINAL_BUILD_PLAN = base.build_plan


def _raw_index(cards_path: Path) -> dict[str, dict[tuple[str, str, str], dict[str, Any]]]:
    out: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {
        target: {} for target in base.TARGETS
    }
    name_conflicts: list[dict[str, Any]] = []
    for card in iter_cards(cards_path):
        logical = delta.yaml_logical_identity(card)
        if not logical:
            continue
        names = mapping(card.get('name'))
        sets = mapping(card.get('sets'))
        for target, spec in base.TARGETS.items():
            rows = sets.get(target) or []
            if isinstance(rows, Mapping):
                rows = [rows]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                collector = s(row.get('set_number')).upper()
                if not collector or any(ch in collector for ch in ('?', '*')):
                    continue
                rarities = row.get('rarities') or []
                if not isinstance(rarities, list):
                    rarities = [rarities]
                if not rarities:
                    rarities = [None]
                for raw_rarity in rarities:
                    rarity = norm_rarity(raw_rarity) or 'unknown'
                    key = (logical, collector, rarity)
                    payload = out[target].setdefault(
                        key,
                        {'names': set(), 'texts': [], 'set_names': set(), 'rarity_raw': set()},
                    )
                    name = s(names.get(spec['language']))
                    if name:
                        payload['names'].add(name)
                    text_payload = base._text_payload(card, spec['language'])
                    if text_payload is not None and text_payload not in payload['texts']:
                        payload['texts'].append(text_payload)
                    set_name = s(row.get('set_name'))
                    if set_name:
                        payload['set_names'].add(set_name)
                    if s(raw_rarity):
                        payload['rarity_raw'].add(s(raw_rarity))

    for target, groups in out.items():
        for key, payload in groups.items():
            if len(payload['names']) > 1:
                name_conflicts.append(
                    {'target': target, 'key': key, 'names': sorted(payload['names'])}
                )
            if len(payload['texts']) > 1:
                payload['texts'] = [{CONFLICT_KEY: list(payload['texts'])}]

    if name_conflicts:
        raise AssertionError(
            'Localized name conflicts: '
            + json.dumps(name_conflicts[:20], ensure_ascii=False, default=str)
        )
    return out


def build_plan(root: Path, cards_path: Path, source_meta: dict[str, Any], target_url: str) -> dict[str, Any]:
    plan = ORIGINAL_BUILD_PLAN(root, cards_path, source_meta, target_url)
    conflicts = Counter()
    conflict_samples: list[dict[str, Any]] = []

    for target in base.TARGETS:
        text_coverage = 0
        for item in plan['targets'][target]['materializable']:
            details = item['localization_details']
            localized_text = details.get('localized_text')
            if isinstance(localized_text, dict) and CONFLICT_KEY in localized_text:
                values = localized_text[CONFLICT_KEY]
                details['localized_text'] = None
                details['localized_text_conflict'] = True
                details['localized_text_conflict_values'] = values
                details.setdefault('field_provenance', {})['localized_text'] = None
                conflicts[target] += 1
                if len(conflict_samples) < 40:
                    conflict_samples.append(
                        {
                            'target': target,
                            'print_key': item['print_key'],
                            'logical_card': item['logical_card'],
                            'collector': item['collector'],
                            'rarity': item['rarity'],
                            'candidate_texts': values,
                        }
                    )
            else:
                details['localized_text_conflict'] = False
                details['localized_text_conflict_values'] = []
                if localized_text is not None:
                    text_coverage += 1
        plan['localized_text_coverage'][target] = text_coverage

    plan['localized_text_conflicts'] = {target: conflicts[target] for target in base.TARGETS}
    plan['localized_text_conflict_samples'] = conflict_samples
    plan['gates']['localized_text_conflicts_preserved_without_synthesis'] = all(
        item['localization_details'].get('localized_text') is None
        for target in base.TARGETS
        for item in plan['targets'][target]['materializable']
        if item['localization_details'].get('localized_text_conflict')
    )
    plan['gates']['localized_text_conflict_evidence_retained'] = all(
        bool(item['localization_details'].get('localized_text_conflict_values'))
        for target in base.TARGETS
        for item in plan['targets'][target]['materializable']
        if item['localization_details'].get('localized_text_conflict')
    )
    plan['structural_pass'] = all(plan['gates'].values())
    return plan


# The base writer/validator remain unchanged. Only source-field conflict handling changes.
base._raw_index = _raw_index
base.build_plan = build_plan


if __name__ == '__main__':
    raise SystemExit(base.main())
