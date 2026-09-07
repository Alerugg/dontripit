#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def numkey(value: str):
    raw = str(value or '')
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def main() -> int:
    ap = argparse.ArgumentParser(description='Read-only backtest of YGO Cardmarket product ordinal vs canonical rarity.')
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--min-support', type=int, default=3)
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
            cur.execute(
                '''
                SELECT ecp.id AS external_product_id, ecp.external_id, ecp.expansion_external_id,
                       ecp.name, ecp.metacard_external_id,
                       l.print_id, p.rarity, p.variant, p.collector_number, s.code AS set_code
                FROM external_catalog_products ecp
                LEFT JOIN external_catalog_print_links l
                  ON l.external_product_id=ecp.id
                 AND l.confidence='exact' AND l.link_status IN ('accepted','mapped')
                LEFT JOIN prints p ON p.id=l.print_id
                LEFT JOIN sets s ON s.id=p.set_id
                WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                ORDER BY ecp.expansion_external_id, lower(ecp.name), ecp.external_id, l.print_id
                ''',
                (game_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        groups = defaultdict(lambda: defaultdict(lambda: {'links': []}))
        for r in rows:
            key = (str(r.get('expansion_external_id') or ''), str(r.get('metacard_external_id') or ''), str(r.get('name') or '').casefold())
            pk = int(r['external_product_id'])
            p = groups[key][pk]
            p['external_product_id'] = pk
            p['idProduct'] = str(r['external_id'])
            if r.get('print_id') is not None:
                p['links'].append({
                    'print_id': int(r['print_id']),
                    'rarity': str(r.get('rarity') or ''),
                    'variant': str(r.get('variant') or ''),
                    'collector_number': str(r.get('collector_number') or ''),
                    'set_code': str(r.get('set_code') or ''),
                })

        observations = []
        global_counts = defaultdict(Counter)
        expansion_counts = defaultdict(lambda: defaultdict(Counter))
        expansion_variant_counts = defaultdict(lambda: defaultdict(Counter))
        for (expansion, metacard, name), pmap in groups.items():
            products = sorted(pmap.values(), key=lambda p: numkey(p['idProduct']))
            if len(products) < 2:
                continue
            k = len(products)
            for ordinal, p in enumerate(products, start=1):
                rarities = {x['rarity'] for x in p['links'] if x['rarity']}
                variants = {x['variant'] for x in p['links'] if x['variant']}
                if len(rarities) == 1:
                    rarity = next(iter(rarities))
                    global_counts[(k, ordinal)][rarity] += 1
                    expansion_counts[expansion][(k, ordinal)][rarity] += 1
                    if len(variants) == 1:
                        expansion_variant_counts[expansion][(k, ordinal)][next(iter(variants))] += 1
                    observations.append({'expansion': expansion, 'product_count': k, 'ordinal': ordinal, 'idProduct': p['idProduct'], 'rarity': rarity, 'variant': next(iter(variants)) if len(variants)==1 else None})

        def summarize(counter):
            total = sum(counter.values())
            if not total:
                return None
            top = counter.most_common()
            winner, support = top[0]
            return {'winner': winner, 'support': support, 'total': total, 'purity': round(support/total, 8), 'top': top[:10]}

        global_summary = {f'{k}:{o}': summarize(c) for (k,o),c in sorted(global_counts.items())}
        expansion_rules = {}
        perfect_rules = []
        for expansion, byslot in sorted(expansion_counts.items()):
            slots = {}
            for (k,o), c in sorted(byslot.items()):
                s = summarize(c)
                slots[f'{k}:{o}'] = s
                if s and s['support'] >= args.min_support and s['purity'] == 1.0:
                    perfect_rules.append({'expansion': expansion, 'product_count': k, 'ordinal': o, **s})
            expansion_rules[expansion] = slots

        # Leave-one-group-out style safety proxy: a slot is considered predictable only
        # when the same expansion/k/ordinal has >= min_support observations all agreeing.
        predictable = correct = wrong = 0
        wrong_samples = []
        slot_rules = {}
        for expansion, byslot in expansion_counts.items():
            for slot, c in byslot.items():
                total = sum(c.values())
                winner, support = c.most_common(1)[0]
                if total >= args.min_support and support == total:
                    slot_rules[(expansion, slot[0], slot[1])] = winner
        for obs in observations:
            rule = slot_rules.get((obs['expansion'], obs['product_count'], obs['ordinal']))
            if rule is None:
                continue
            predictable += 1
            if rule == obs['rarity']:
                correct += 1
            else:
                wrong += 1
                if len(wrong_samples) < 50:
                    wrong_samples.append({**obs, 'predicted': rule})

        payload = {
            'mode': 'read_only',
            'game': 'yugioh',
            'summary': {
                'mapped_multi_product_observations': len(observations),
                'global_slots': len(global_counts),
                'expansion_slots': sum(len(v) for v in expansion_counts.values()),
                'perfect_expansion_slot_rules': len(perfect_rules),
                'predictable_existing_observations': predictable,
                'correct_existing_observations': correct,
                'wrong_existing_observations': wrong,
                'apparent_precision': round(correct/predictable, 8) if predictable else None,
            },
            'global_rules': global_summary,
            'perfect_expansion_slot_rules': perfect_rules,
            'expansion_rules': expansion_rules,
            'wrong_samples': wrong_samples,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
        print('YGO_VERSION_ORDINAL_BACKTEST='+json.dumps(payload['summary'], separators=(',',':')))
        conn.rollback()
    finally:
        conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
