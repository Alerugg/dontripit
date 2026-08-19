from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = 'yugioh'
ACCEPTED = ('accepted', 'mapped', 'exact')
METHOD = 'cardmarket_ocg_certified_public_version_contract_v2'
EXPECTED_METHOD_LINKS = 40
EXPECTED_CAPTURE = '2026-08-18 21:09:20.740891+00:00'
SURFACES = {
    '4535': ('SD41', 53, 10),
    '4545': ('SD40', 54, 16),
    '4557': ('SD38', 53, 10),
    '4579': ('SD36', 48, 4),
}


def positive(value):
    try:
        return value is not None and Decimal(str(value)) > 0
    except Exception:
        return False


def meaningful(row):
    return any(positive(row.get(key)) for key in ('price_low', 'price_mid', 'price_market', 'price_last'))


def price_variant(row):
    variant = str(row.get('variant') or '').lower()
    if 'etched' in variant or 'glossy' in variant:
        return None
    return 'foil' if bool(row.get('is_foil')) else 'nonfoil'


def main() -> int:
    url = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE URL required')
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name='dontripit_ygo_ocg_structure_deck_variants_proof_v1',
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            gid = int(cur.fetchone()['id'])
            cur.execute(
                "SELECT max(last_seen_at) ts FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
                (gid,),
            )
            capture = cur.fetchone()['ts']
            if str(capture) != EXPECTED_CAPTURE:
                raise RuntimeError({'capture_drift': {'expected': EXPECTED_CAPTURE, 'actual': str(capture)}})
            cur.execute(
                """SELECT max(mp.as_of) ts FROM external_market_price_snapshots mp
                JOIN external_catalog_products e ON e.id=mp.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
                (gid,),
            )
            asof = cur.fetchone()['ts']

            cur.execute(
                """SELECT l.mapping_method,l.confidence,l.reviewed,e.id external_product_id,e.external_id id_product,
                          e.expansion_external_id,e.last_seen_at,p.id print_id,p.language,p.collector_number,p.variant,p.is_foil,
                          s.code set_code,c.name card_name
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=ANY(%s) AND l.mapping_method=%s AND l.link_status=ANY(%s)
                ORDER BY e.expansion_external_id,e.external_id::bigint""",
                (gid, list(SURFACES), METHOD, list(ACCEPTED)),
            )
            method_links = [dict(row) for row in cur.fetchall()]

            regional = {}
            for exp, (code, physical, method_expected) in SURFACES.items():
                cur.execute(
                    """SELECT count(*) total,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints,
                              count(*) FILTER (WHERE lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s) good,
                              count(*) FILTER (WHERE e.last_seen_at=%s) current,
                              count(*) FILTER (WHERE l.mapping_method=%s AND l.confidence='exact' AND l.reviewed=true) variant_method,
                              count(*) FILTER (WHERE l.mapping_method='cardmarket_ocg_certified_public_code_singleton_v2' AND l.confidence='exact' AND l.reviewed=true) singleton_method
                    FROM external_catalog_print_links l
                    JOIN external_catalog_products e ON e.id=l.external_product_id
                    JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND l.link_status=ANY(%s)""",
                    (code, capture, METHOD, gid, exp, list(ACCEPTED)),
                )
                row = cur.fetchone()
                regional[code] = {
                    'idExpansion': exp,
                    'expected_physical': physical,
                    'expected_variant_method': method_expected,
                    'accepted_links': int(row['total']),
                    'unique_products': int(row['products']),
                    'unique_prints': int(row['prints']),
                    'exact_ja_set_links': int(row['good']),
                    'current_capture_links': int(row['current']),
                    'variant_method_links': int(row['variant_method']),
                    'singleton_method_links': int(row['singleton_method']),
                }

            pids = [int(row['external_product_id']) for row in method_links]
            printids = [int(row['print_id']) for row in method_links]
            external_prices = {}
            if asof and pids:
                cur.execute(
                    """SELECT external_product_id,price_variant,price_low,price_mid,price_market,price_last
                    FROM external_market_price_snapshots
                    WHERE external_product_id=ANY(%s) AND currency='EUR' AND as_of=%s""",
                    (pids, asof),
                )
                for row in cur.fetchall():
                    external_prices[(int(row['external_product_id']), str(row['price_variant']))] = dict(row)
            canonical_prices = {}
            if asof and printids:
                cur.execute(
                    """SELECT ps.entity_id print_id,ps.price_low,ps.price_mid,ps.price_market,ps.price_last,ps.raw_json
                    FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id
                    WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)
                      AND ps.currency='EUR' AND ps.as_of=%s""",
                    (printids, asof),
                )
                for row in cur.fetchall():
                    canonical_prices.setdefault(int(row['print_id']), []).append(dict(row))
            conn.rollback()
    finally:
        conn.close()

    failures = []
    if len(method_links) != EXPECTED_METHOD_LINKS:
        failures.append(f'method_link_count_{len(method_links)}_expected_{EXPECTED_METHOD_LINKS}')
    if len({int(row['external_product_id']) for row in method_links}) != EXPECTED_METHOD_LINKS:
        failures.append('method_products_not_40_unique')
    if len({int(row['print_id']) for row in method_links}) != EXPECTED_METHOD_LINKS:
        failures.append('method_prints_not_40_unique')

    per_method = {}
    for exp, (code, _physical, expected) in SURFACES.items():
        rows = [row for row in method_links if str(row['expansion_external_id']) == exp]
        vals = {
            'links': len(rows),
            'products': len({int(row['external_product_id']) for row in rows}),
            'prints': len({int(row['print_id']) for row in rows}),
            'wrong_language': sum(str(row['language']).lower() != 'ja' for row in rows),
            'wrong_set': sum(str(row['set_code']).upper() != code for row in rows),
            'wrong_method': sum(
                str(row['mapping_method']) != METHOD
                or str(row['confidence']) != 'exact'
                or not bool(row['reviewed'])
                for row in rows
            ),
            'stale': sum(row['last_seen_at'] != capture for row in rows),
        }
        if (vals['links'], vals['products'], vals['prints']) != (expected, expected, expected):
            failures.append(f'{code}_variant_identity_count_drift')
        if vals['wrong_language'] or vals['wrong_set'] or vals['wrong_method'] or vals['stale']:
            failures.append(f'{code}_variant_identity_property_drift')
        per_method[code] = {'idExpansion': exp, **vals}

    for code, row in regional.items():
        expected = row['expected_physical']
        if (
            row['accepted_links'],
            row['unique_products'],
            row['unique_prints'],
            row['exact_ja_set_links'],
            row['current_capture_links'],
        ) != (expected, expected, expected, expected, expected):
            failures.append(f'{code}_regional_surface_not_complete_{expected}')
        if row['variant_method_links'] != row['expected_variant_method']:
            failures.append(f'{code}_variant_method_count_drift')
        if row['singleton_method_links'] + row['variant_method_links'] != expected:
            failures.append(f'{code}_method_partition_not_complete')

    priceable = missing_external = unsupported_finish = canonical_exact = missing_canonical = wrong_product = 0
    unpriced_samples = []
    for row in method_links:
        variant = price_variant(row)
        external = None if variant is None else external_prices.get((int(row['external_product_id']), variant))
        if variant is None:
            unsupported_finish += 1
        elif not external or not meaningful(external):
            missing_external += 1
        else:
            priceable += 1

        current = canonical_prices.get(int(row['print_id']), [])
        exact = [
            item for item in current
            if str((item.get('raw_json') or {}).get('idProduct') or '') == str(row['id_product'])
        ]
        mismatch = [
            item for item in current
            if str((item.get('raw_json') or {}).get('idProduct') or '') not in ('', str(row['id_product']))
        ]
        wrong_product += bool(mismatch)
        if exact and any(meaningful(item) for item in exact):
            canonical_exact += 1
        else:
            missing_canonical += 1
            if len(unpriced_samples) < 20:
                unpriced_samples.append({
                    'set_code': row['set_code'],
                    'collector_number': row['collector_number'],
                    'card_name': row['card_name'],
                    'idProduct': str(row['id_product']),
                    'print_id': int(row['print_id']),
                    'external_current_meaningful': bool(external and meaningful(external)),
                })
    if wrong_product:
        failures.append(f'canonical_wrong_idProduct_{wrong_product}')
    if canonical_exact != priceable:
        failures.append(f'canonical_exact_{canonical_exact}_external_priceable_{priceable}')

    report = {
        'status': 'pass' if not failures else 'fail',
        'production_writes': 0,
        'mapping_method': METHOD,
        'catalog_capture': str(capture),
        'price_guide_as_of': str(asof),
        'accepted_method_links': len(method_links),
        'unique_method_products': len({int(row['external_product_id']) for row in method_links}),
        'unique_method_prints': len({int(row['print_id']) for row in method_links}),
        'method_sets': per_method,
        'regional_surfaces': regional,
        'regional_total_accepted_links': sum(row['accepted_links'] for row in regional.values()),
        'pricing': {
            'externally_priceable_links': priceable,
            'missing_external_current_price': missing_external,
            'unsupported_finish': unsupported_finish,
            'canonical_current_exact_idProduct_prices': canonical_exact,
            'missing_canonical_current_price': missing_canonical,
            'canonical_current_wrong_idProduct': wrong_product,
        },
        'failures': failures,
        'unpriced_samples': unpriced_samples,
    }
    out = Path(
        os.getenv(
            'YGO_OCG_STRUCTURE_DECK_VARIANTS_PROOF_OUTPUT',
            '/tmp/yugioh-ocg-structure-deck-variants-production-v1.json',
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == '__main__':
    raise SystemExit(main())
