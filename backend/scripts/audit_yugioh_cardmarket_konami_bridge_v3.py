#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def norm_text(value: str | None) -> str:
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', '', text)


def norm_rarity(value: str | None) -> str:
    text = norm_text(value)
    if text.endswith('rare') and text != 'rare':
        text = text[:-4]
    return text


def numkey(value: str):
    raw = str(value or '')
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def pure_rule(counter: Counter, min_support: int) -> str | None:
    total = sum(counter.values())
    if total < min_support or len(counter) != 1:
        return None
    rarity, support = counter.most_common(1)[0]
    return rarity if support == total else None


def main() -> int:
    ap = argparse.ArgumentParser(description='Read-only YGO rarity-aware Cardmarket/Konami bridge V3.')
    ap.add_argument('--min-linked', type=int, default=5)
    ap.add_argument('--min-informative', type=int, default=5)
    ap.add_argument('--min-support', type=int, default=5)
    ap.add_argument('--min-evidence-coverage', type=float, default=0.25)
    ap.add_argument('--min-purity', type=float, default=0.95)
    ap.add_argument('--min-margin', type=int, default=3)
    ap.add_argument('--ordinal-min-support', type=int, default=3)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--sample-limit', type=int, default=100)
    args = ap.parse_args()

    url = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            game_id = int(cur.fetchone()['id'])
            cur.execute('''
                SELECT id AS external_product_id, external_id, expansion_external_id,
                       name, metacard_external_id, website_path
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
            ''', (game_id,))
            products = [dict(r) for r in cur.fetchall()]
            cur.execute('''
                SELECT l.external_product_id, l.print_id, l.link_status, l.confidence, l.mapping_method,
                       p.rarity AS linked_rarity
                FROM external_catalog_print_links l
                JOIN external_catalog_products ecp ON ecp.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
            ''', (game_id,))
            links = [dict(r) for r in cur.fetchall()]
            cur.execute('''
                SELECT pr.print_id, cr.external_id AS konami_pid, cr.name AS konami_product_name,
                       c.name AS card_name, p.collector_number, p.rarity, p.variant, p.language,
                       s.code AS set_code
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE cr.game_id=%s AND cr.source='konami_neuron'
            ''', (game_id,))
            official = [dict(r) for r in cur.fetchall()]

        product_by_id = {int(p['external_product_id']): p for p in products}
        products_by_expansion = defaultdict(list)
        groups = defaultdict(list)
        for p in products:
            expansion = str(p.get('expansion_external_id') or '')
            if expansion:
                products_by_expansion[expansion].append(p)
            group_key = (expansion, str(p.get('metacard_external_id') or ''), norm_text(p.get('name')))
            groups[group_key].append(p)

        exact_prints_by_product = defaultdict(set)
        exact_rarities_by_product = defaultdict(set)
        all_links_by_product = defaultdict(list)
        exact_products = set()
        exact_prints = set()
        exact_prints_by_expansion = defaultdict(set)
        exact_product_by_print = defaultdict(set)
        for row in links:
            pk = int(row['external_product_id'])
            all_links_by_product[pk].append(row)
            if row['confidence'] == 'exact' and row['link_status'] in ('accepted', 'mapped'):
                print_id = int(row['print_id'])
                exact_products.add(pk)
                exact_prints.add(print_id)
                exact_prints_by_product[pk].add(print_id)
                rarity = norm_rarity(row.get('linked_rarity'))
                if rarity:
                    exact_rarities_by_product[pk].add(rarity)
                exact_product_by_print[print_id].add(pk)
                product = product_by_id.get(pk)
                expansion = str(product.get('expansion_external_id') or '') if product else ''
                if expansion:
                    exact_prints_by_expansion[expansion].add(print_id)

        print_to_pids = defaultdict(set)
        pid_names = {}
        official_by_pid_name_rarity = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
        official_rarities_by_pid_name = defaultdict(lambda: defaultdict(set))
        print_meta = {}
        for row in official:
            print_id = int(row['print_id'])
            pid = str(row['konami_pid'])
            name_key = norm_text(row.get('card_name'))
            rarity = norm_rarity(row.get('rarity'))
            print_to_pids[print_id].add(pid)
            pid_names[pid] = str(row.get('konami_product_name') or '')
            if name_key and rarity:
                official_by_pid_name_rarity[pid][name_key][rarity].add(print_id)
                official_rarities_by_pid_name[pid][name_key].add(rarity)
            print_meta[print_id] = {
                'card_name': row.get('card_name'), 'collector_number': row.get('collector_number'),
                'rarity': row.get('rarity'), 'variant': row.get('variant'), 'language': row.get('language'),
                'set_code': row.get('set_code'),
            }

        # Expansion -> Konami release consensus (V2 safety gate).
        bridge_by_expansion = {}
        bridges = []
        rejected_bridge = Counter()
        for expansion, linked_prints in sorted(exact_prints_by_expansion.items()):
            if len(linked_prints) < args.min_linked:
                rejected_bridge['insufficient_linked_prints'] += 1; continue
            informative = [p for p in linked_prints if print_to_pids.get(p)]
            if len(informative) < args.min_informative:
                rejected_bridge['insufficient_informative_prints'] += 1; continue
            coverage = len(informative) / len(linked_prints)
            if coverage < args.min_evidence_coverage:
                rejected_bridge['insufficient_evidence_coverage'] += 1; continue
            support = Counter()
            for print_id in informative:
                for pid in print_to_pids[print_id]:
                    support[pid] += 1
            ranked = support.most_common()
            if not ranked:
                rejected_bridge['no_release_support'] += 1; continue
            winner, winner_support = ranked[0]
            runner = ranked[1][1] if len(ranked) > 1 else 0
            if len(ranked) > 1 and ranked[1][1] == winner_support:
                rejected_bridge['tied_release_support'] += 1; continue
            purity = winner_support / len(informative)
            if winner_support < args.min_support:
                rejected_bridge['insufficient_winner_support'] += 1; continue
            if purity < args.min_purity:
                rejected_bridge['insufficient_release_purity'] += 1; continue
            if winner_support - runner < args.min_margin:
                rejected_bridge['insufficient_runner_up_margin'] += 1; continue
            bridge_by_expansion[expansion] = winner
            bridges.append({'expansion': expansion, 'konami_pid': winner, 'support': winner_support,
                            'informative': len(informative), 'linked': len(linked_prints),
                            'purity': round(purity, 6), 'coverage': round(coverage, 6)})

        # Learn only alternate-version ordinal rules (ordinal >= 2).
        expansion_slot_counts = defaultdict(lambda: defaultdict(Counter))
        group_slot_counts = defaultdict(lambda: defaultdict(Counter))
        exact_group_actual = {}
        for group_key, plist in groups.items():
            ordered = sorted(plist, key=lambda p: numkey(p['external_id']))
            if len(ordered) < 2:
                continue
            k = len(ordered)
            actual = {}
            for ordinal, product in enumerate(ordered, start=1):
                pk = int(product['external_product_id'])
                rarities = exact_rarities_by_product.get(pk, set())
                if len(rarities) == 1:
                    rarity = next(iter(rarities))
                    actual[ordinal] = rarity
                    if ordinal >= 2:
                        slot = (k, ordinal)
                        expansion_slot_counts[group_key[0]][slot][rarity] += 1
                        group_slot_counts[group_key][slot][rarity] += 1
            if actual:
                exact_group_actual[group_key] = actual

        full_rules = {}
        for expansion, slots in expansion_slot_counts.items():
            for slot, counts in slots.items():
                rarity = pure_rule(counts, args.ordinal_min_support)
                if rarity is not None:
                    full_rules[(expansion, slot[0], slot[1])] = rarity

        # True leave-one-group-out page->rarity validation for alternate ordinals.
        loo_predictable = loo_correct = loo_wrong = 0
        loo_wrong_samples = []
        for group_key, actual in exact_group_actual.items():
            expansion = group_key[0]
            k = len(groups[group_key])
            for ordinal, actual_rarity in actual.items():
                if ordinal < 2:
                    continue
                slot = (k, ordinal)
                training = expansion_slot_counts[expansion][slot].copy()
                training.subtract(group_slot_counts[group_key][slot]); training += Counter()
                predicted = pure_rule(training, args.ordinal_min_support)
                if predicted is None:
                    continue
                loo_predictable += 1
                if predicted == actual_rarity:
                    loo_correct += 1
                else:
                    loo_wrong += 1
                    if len(loo_wrong_samples) < args.sample_limit:
                        loo_wrong_samples.append({'group': group_key, 'ordinal': ordinal,
                                                  'actual': actual_rarity, 'predicted': predicted,
                                                  'training_support': sum(training.values())})

        # Group resolver. Existing exact product rarities are fixed; alternate ordinals use only
        # rules proven on other cards; Version 1 is assigned only by unique elimination.
        candidate_products = []
        unresolved = Counter()
        candidate_targets = defaultdict(set)
        for group_key, plist in groups.items():
            expansion, _, name_key = group_key
            pid = bridge_by_expansion.get(expansion)
            if not pid or not name_key:
                continue
            ordered = sorted(plist, key=lambda p: numkey(p['external_id']))
            k = len(ordered)
            canonical_rarities = set(official_rarities_by_pid_name[pid].get(name_key, set()))
            if not canonical_rarities:
                unresolved['no_official_name_in_consensus_release'] += 1; continue
            if len(canonical_rarities) != k:
                unresolved['product_count_rarity_count_mismatch'] += 1; continue

            assignments = {}
            used = set()
            invalid = False
            # Existing exact products fix known page rarity.
            for ordinal, product in enumerate(ordered, start=1):
                pk = int(product['external_product_id'])
                rarities = exact_rarities_by_product.get(pk, set())
                if len(rarities) == 1:
                    rarity = next(iter(rarities))
                    if rarity not in canonical_rarities or rarity in used:
                        invalid = True; break
                    assignments[ordinal] = rarity; used.add(rarity)
                elif len(rarities) > 1:
                    invalid = True; break
            if invalid:
                unresolved['existing_exact_rarity_conflict'] += 1; continue

            # Safe learned rules only for V2+.
            for ordinal, product in enumerate(ordered, start=1):
                if ordinal in assignments or ordinal < 2:
                    continue
                rarity = full_rules.get((expansion, k, ordinal))
                if rarity is None:
                    continue
                if rarity not in canonical_rarities or rarity in used:
                    invalid = True; break
                assignments[ordinal] = rarity; used.add(rarity)
            if invalid:
                unresolved['ordinal_rule_conflict'] += 1; continue

            # Unique elimination only; never predict base rarity directly.
            remaining_ordinals = [o for o in range(1, k + 1) if o not in assignments]
            remaining_rarities = sorted(canonical_rarities - used)
            if len(remaining_ordinals) == 1 and len(remaining_rarities) == 1:
                assignments[remaining_ordinals[0]] = remaining_rarities[0]
                used.add(remaining_rarities[0])
            if len(assignments) != k:
                unresolved['group_not_uniquely_resolved'] += 1; continue

            for ordinal, product in enumerate(ordered, start=1):
                pk = int(product['external_product_id'])
                if pk in exact_products:
                    continue
                if all_links_by_product.get(pk):
                    unresolved['external_product_has_existing_nonexact_or_conflicting_link'] += 1; continue
                rarity = assignments[ordinal]
                target_prints = set(official_by_pid_name_rarity[pid][name_key][rarity])
                if not target_prints:
                    unresolved['assigned_rarity_has_no_official_prints'] += 1; continue
                conflicting = {p for p in target_prints if exact_product_by_print.get(p)}
                if conflicting:
                    unresolved['target_print_already_has_exact_cardmarket_product'] += 1; continue
                row = {'external_product_id': pk, 'idProduct': str(product['external_id']),
                       'name': product['name'], 'expansion': expansion, 'konami_pid': pid,
                       'ordinal': ordinal, 'product_count': k, 'rarity': rarity,
                       'target_print_ids': sorted(target_prints),
                       'target_prints': [print_meta[p] | {'print_id': p} for p in sorted(target_prints)]}
                candidate_products.append(row)
                for print_id in target_prints:
                    candidate_targets[print_id].add(pk)

        duplicate_targets = {p: sorted(v) for p, v in candidate_targets.items() if len(v) > 1}
        safe_products = [r for r in candidate_products
                         if not any(p in duplicate_targets for p in r['target_print_ids'])]
        safe_link_rows = sum(len(r['target_print_ids']) for r in safe_products)

        payload = {
            'mode': 'read_only', 'game': 'yugioh', 'resolver': 'cardmarket_konami_rarity_bridge_v3',
            'summary': {
                'cardmarket_products': len(products), 'existing_exact_products': len(exact_products),
                'existing_exact_prints': len(exact_prints), 'certified_consensus_expansions': len(bridges),
                'alternate_ordinal_rules': len(full_rules),
                'loo_alt_predictable': loo_predictable, 'loo_alt_correct': loo_correct,
                'loo_alt_wrong': loo_wrong,
                'loo_alt_precision': round(loo_correct/loo_predictable, 8) if loo_predictable else None,
                'candidate_external_products_before_target_guard': len(candidate_products),
                'duplicate_target_prints': len(duplicate_targets),
                'safe_candidate_external_products': len(safe_products),
                'safe_candidate_link_rows': safe_link_rows,
                'write_ready': loo_predictable > 0 and loo_wrong == 0 and len(duplicate_targets) == 0,
                'unresolved': dict(sorted(unresolved.items())),
                'rejected_bridge': dict(sorted(rejected_bridge.items())),
            },
            'bridges': bridges, 'safe_candidates': safe_products,
            'duplicate_targets': {str(k): v for k, v in duplicate_targets.items()},
            'loo_wrong_samples': loo_wrong_samples,
            'ordinal_rules': [{'expansion': e, 'product_count': k, 'ordinal': o, 'rarity': r}
                              for (e,k,o),r in sorted(full_rules.items())],
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
        print('YGO_KONAMI_BRIDGE_V3='+json.dumps(payload['summary'], separators=(',',':')))
        conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
