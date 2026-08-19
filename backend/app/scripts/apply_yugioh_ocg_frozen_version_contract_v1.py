from __future__ import annotations

import argparse
import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from app.scripts.yugioh_ocg_frozen_version_contract_v1 import EVIDENCE, contract_sha256
from app.scripts.yugioh_ocg_frozen_version_manifest_v1 import (
    CONTRACT_SHA256,
    EXPECTED_BY_SET,
    EXPECTED_PAIRS,
    PAIR_MANIFEST_SHA256,
    manifest_sha256,
    pairs as frozen_pairs,
)

GAME = 'yugioh'
ACCEPTED = ('accepted', 'mapped', 'exact')
METHOD = 'cardmarket_ocg_certified_public_version_contract_v1'
CONFIRM = 'APPLY_YUGIOH_OCG_FROZEN_VERSION_CONTRACT_V1'
EXPECTED_JA = 36426
SURFACES = {
    'DOCS': {'idExpansion': '4680', 'products': 108, 'prints': 108, 'before': 69, 'new': 19, 'after': 88},
    'LTGY': {'idExpansion': '4725', 'products': 86, 'prints': 86, 'before': 75, 'new': 11, 'after': 86},
    'CSOC': {'idExpansion': '4809', 'products': 87, 'prints': 87, 'before': 74, 'new': 11, 'after': 85},
}


def norm(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def connect(*, readonly: bool):
    url = os.getenv('DATABASE_URL_UNPOOLED') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL_UNPOOLED or DATABASE_URL is required')
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name='dontripit_ygo_ocg_frozen_version_contract_apply_v1',
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _guard_frozen_sources() -> list[dict]:
    actual_contract = contract_sha256()
    if actual_contract != CONTRACT_SHA256:
        raise RuntimeError({'frozen_contract_sha256_drift': {'expected': CONTRACT_SHA256, 'actual': actual_contract}})
    actual_manifest = manifest_sha256()
    if actual_manifest != PAIR_MANIFEST_SHA256:
        raise RuntimeError({'frozen_pair_manifest_sha256_drift': {'expected': PAIR_MANIFEST_SHA256, 'actual': actual_manifest}})
    rows = frozen_pairs()
    if len(rows) != EXPECTED_PAIRS:
        raise RuntimeError({'frozen_pair_count_drift': len(rows)})
    if Counter(row['set_code'] for row in rows) != Counter(EXPECTED_BY_SET):
        raise RuntimeError({'frozen_pair_set_distribution_drift': Counter(row['set_code'] for row in rows)})
    if len({int(row['external_product_id']) for row in rows}) != EXPECTED_PAIRS:
        raise RuntimeError('frozen internal Cardmarket product IDs are not unique')
    if len({str(row['idProduct']) for row in rows}) != EXPECTED_PAIRS:
        raise RuntimeError('frozen Cardmarket idProducts are not unique')
    if len({int(row['print_id']) for row in rows}) != EXPECTED_PAIRS:
        raise RuntimeError('frozen canonical print IDs are not unique')
    return rows


def _load_live(cur, rows: list[dict]) -> dict:
    cur.execute('SELECT id FROM games WHERE slug=%s LIMIT 1', (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError('Yu-Gi-Oh game row missing')
    gid = int(game['id'])

    cur.execute(
        "SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
        (gid,),
    )
    capture = cur.fetchone()['capture']
    if capture is None:
        raise RuntimeError('current Cardmarket capture missing')

    cur.execute(
        """SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'""",
        (gid,),
    )
    ja = int(cur.fetchone()['n'])
    if ja != EXPECTED_JA:
        raise RuntimeError({'ja_baseline_drift': {'expected': EXPECTED_JA, 'actual': ja}})

    external_ids = [int(row['external_product_id']) for row in rows]
    cur.execute(
        """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                  e.expansion_external_id,e.last_seen_at
        FROM external_catalog_products e
        WHERE e.id=ANY(%s) AND e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'""",
        (external_ids, gid),
    )
    products = {int(r['external_product_id']): dict(r) for r in cur.fetchall()}
    if len(products) != EXPECTED_PAIRS:
        raise RuntimeError({'current_frozen_product_rows_missing': EXPECTED_PAIRS - len(products)})

    print_ids = [int(row['print_id']) for row in rows]
    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                  c.name card_name,s.code set_code
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE p.id=ANY(%s) AND c.game_id=%s""",
        (print_ids, gid),
    )
    prints = {int(r['print_id']): dict(r) for r in cur.fetchall()}
    if len(prints) != EXPECTED_PAIRS:
        raise RuntimeError({'current_frozen_print_rows_missing': EXPECTED_PAIRS - len(prints)})

    cur.execute(
        """SELECT e.external_id id_product,l.external_product_id,l.print_id,l.mapping_method,
                  l.confidence,l.reviewed,l.link_status
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s)""",
        (gid, list(ACCEPTED)),
    )
    accepted = [dict(r) for r in cur.fetchall()]
    by_product: dict[int, list[dict]] = defaultdict(list)
    by_print: dict[int, list[dict]] = defaultdict(list)
    for row in accepted:
        by_product[int(row['external_product_id'])].append(row)
        by_print[int(row['print_id'])].append(row)

    return {
        'gid': gid,
        'capture': capture,
        'ja': ja,
        'products': products,
        'prints': prints,
        'accepted': accepted,
        'by_product': by_product,
        'by_print': by_print,
    }


def _validate_surfaces(cur, live: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for code, cfg in SURFACES.items():
        cur.execute(
            """SELECT count(*) n FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s""",
            (live['gid'], cfg['idExpansion'], live['capture']),
        )
        products = int(cur.fetchone()['n'])
        cur.execute(
            """SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
              AND lower(coalesce(p.language,''))='ja'""",
            (live['gid'], code),
        )
        prints = int(cur.fetchone()['n'])
        if (products, prints) != (cfg['products'], cfg['prints']):
            raise RuntimeError({'regional_surface_drift': code, 'products': products, 'prints': prints, 'expected': cfg})

        cur.execute(
            """SELECT count(*) n,count(DISTINCT l.external_product_id) products,count(DISTINCT l.print_id) prints
            FROM external_catalog_print_links l
            JOIN external_catalog_products e ON e.id=l.external_product_id
            JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
              AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",
            (live['gid'], cfg['idExpansion'], list(ACCEPTED), code),
        )
        x = cur.fetchone()
        accepted = int(x['n'])
        unique_products = int(x['products'])
        unique_prints = int(x['prints'])
        if accepted not in (cfg['before'], cfg['after']):
            raise RuntimeError({'unexpected_regional_accepted_surface': code, 'actual': accepted, 'expected': [cfg['before'], cfg['after']]})
        if (accepted, unique_products, unique_prints) != (accepted, accepted, accepted):
            raise RuntimeError({'regional_surface_not_one_to_one': code, 'links': accepted, 'products': unique_products, 'prints': unique_prints})
        out[code] = {
            'products': products,
            'canonical_ja_prints': prints,
            'accepted_links': accepted,
            'unique_accepted_products': unique_products,
            'unique_accepted_prints': unique_prints,
        }
    return out


def derive(cur) -> dict:
    rows = _guard_frozen_sources()
    live = _load_live(cur, rows)
    surfaces = _validate_surfaces(cur, live)

    # Verify each frozen metacard group still constitutes the complete current
    # product surface for the exact logical card and exact canonical JA set.
    frozen_by_meta: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        frozen_by_meta[(row['set_code'], str(row['idMetacard']))].append(row)

    for (code, meta), group in frozen_by_meta.items():
        cfg = SURFACES[code]
        expected_products = sorted(str(row['idProduct']) for row in group)
        cur.execute(
            """SELECT e.external_id id_product FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.metacard_external_id=%s
            ORDER BY e.external_id::bigint""",
            (live['gid'], cfg['idExpansion'], live['capture'], meta),
        )
        actual_products = [str(r['id_product']) for r in cur.fetchall()]
        if actual_products != sorted(expected_products, key=int):
            raise RuntimeError({'complete_metacard_product_surface_drift': code, 'idMetacard': meta, 'expected': expected_products, 'actual': actual_products})

        card_ids = {int(row['card_id']) for row in group}
        if len(card_ids) != 1:
            raise RuntimeError({'frozen_group_spans_multiple_cards': code, 'idMetacard': meta, 'card_ids': sorted(card_ids)})
        card_id = next(iter(card_ids))
        expected_prints = sorted(int(row['print_id']) for row in group)
        cur.execute(
            """SELECT p.id print_id FROM prints p JOIN sets s ON s.id=p.set_id
            WHERE p.card_id=%s AND upper(coalesce(s.code,''))=%s AND lower(coalesce(p.language,''))='ja'
            ORDER BY p.id""",
            (card_id, code),
        )
        actual_prints = sorted(int(r['print_id']) for r in cur.fetchall())
        if actual_prints != expected_prints:
            raise RuntimeError({'complete_canonical_card_print_surface_drift': code, 'idMetacard': meta, 'card_id': card_id, 'expected': expected_prints, 'actual': actual_prints})

    proposal: list[dict] = []
    existing: list[dict] = []
    per_set = {code: {'pairs': 0, 'existing_same': 0, 'new_ready': 0} for code in SURFACES}

    for frozen in rows:
        code = str(frozen['set_code'])
        product = live['products'][int(frozen['external_product_id'])]
        pr = live['prints'][int(frozen['print_id'])]
        checks = {
            'idProduct': str(product['id_product']) == str(frozen['idProduct']),
            'idExpansion': str(product['expansion_external_id']) == str(frozen['idExpansion']),
            'idMetacard': str(product['metacard_external_id']) == str(frozen['idMetacard']),
            'current_capture': product['last_seen_at'] == live['capture'],
            'print_card_id': int(pr['card_id']) == int(frozen['card_id']),
            'print_language': str(pr['language'] or '').lower() == 'ja',
            'print_set': str(pr['set_code'] or '').upper() == code,
            'collector': str(pr['collector_number'] or '') == str(frozen['collector_number']),
            'rarity': str(pr['rarity'] or '').casefold() == str(frozen['canonical_rarity']).casefold(),
            'variant': str(pr['variant'] or '') == str(frozen['canonical_variant']),
            'contract_rarity': str(pr['rarity'] or '').casefold() == str(frozen['contract_rarity']).casefold(),
            'logical_name': norm(product['name']) == norm(pr['card_name']),
        }
        failed = [key for key, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError({
                'frozen_pair_live_identity_drift': {
                    'set_code': code,
                    'idProduct': str(frozen['idProduct']),
                    'print_id': int(frozen['print_id']),
                    'failed': failed,
                    'product_name': str(product['name']),
                    'card_name': str(pr['card_name']),
                }
            })

        eid = int(frozen['external_product_id'])
        pid = int(frozen['print_id'])
        pclaims = live['by_product'].get(eid, [])
        rclaims = live['by_print'].get(pid, [])
        same = [
            row
            for row in pclaims
            if int(row['print_id']) == pid
            and str(row.get('mapping_method') or '') == METHOD
            and str(row.get('confidence') or '') == 'exact'
            and bool(row.get('reviewed'))
            and str(row.get('link_status') or '') in ACCEPTED
        ]
        competing_product = [row for row in pclaims if int(row['print_id']) != pid]
        competing_print = [row for row in rclaims if int(row['external_product_id']) != eid]
        if competing_product or competing_print:
            raise RuntimeError({
                'accepted_identity_conflict': {
                    'set_code': code,
                    'idProduct': str(frozen['idProduct']),
                    'print_id': pid,
                    'product_claims': pclaims,
                    'print_claims': rclaims,
                }
            })
        if pclaims or rclaims:
            if len(same) != 1 or len(pclaims) != 1 or len(rclaims) != 1:
                raise RuntimeError({
                    'unexpected_existing_same_pair': {
                        'set_code': code,
                        'idProduct': str(frozen['idProduct']),
                        'print_id': pid,
                        'product_claims': pclaims,
                        'print_claims': rclaims,
                    }
                })
            existing.append(frozen)
            per_set[code]['existing_same'] += 1
        else:
            proposal.append(frozen)
            per_set[code]['new_ready'] += 1
        per_set[code]['pairs'] += 1

    if len(existing) + len(proposal) != EXPECTED_PAIRS:
        raise RuntimeError({'global_manifest_coverage_drift': {'existing': len(existing), 'new': len(proposal)}})
    if (len(existing), len(proposal)) not in ((0, EXPECTED_PAIRS), (EXPECTED_PAIRS, 0)):
        raise RuntimeError({'global_partial_state_blocked': {'existing': len(existing), 'new': len(proposal)}})

    for code, expected in EXPECTED_BY_SET.items():
        r = per_set[code]
        if r['pairs'] != expected:
            raise RuntimeError({'set_pair_count_drift': code, 'actual': r, 'expected': expected})
        if (r['existing_same'], r['new_ready']) not in ((0, expected), (expected, 0)):
            raise RuntimeError({'set_partial_state_blocked': code, 'actual': r})

    # Regional accepted surfaces must move in lock-step with the frozen batch.
    if proposal:
        for code, cfg in SURFACES.items():
            if surfaces[code]['accepted_links'] != cfg['before']:
                raise RuntimeError({'preapply_regional_baseline_drift': code, 'actual': surfaces[code]['accepted_links'], 'expected': cfg['before']})
    else:
        for code, cfg in SURFACES.items():
            if surfaces[code]['accepted_links'] != cfg['after']:
                raise RuntimeError({'postapply_regional_baseline_drift': code, 'actual': surfaces[code]['accepted_links'], 'expected': cfg['after']})

    return {
        'gid': live['gid'],
        'capture': live['capture'],
        'ja': live['ja'],
        'surfaces': surfaces,
        'proposal': proposal,
        'existing': existing,
        'per_set': per_set,
    }


def run(*, apply: bool = False, confirm: str = '') -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn = connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state = derive(cur)
            report = {
                'mode': 'apply' if apply else 'dry_run',
                'status': 'pass',
                'production_writes': 0,
                'mapping_method': METHOD,
                'cardmarket_capture': str(state['capture']),
                'ja_baseline': state['ja'],
                'contract_sha256': CONTRACT_SHA256,
                'pair_manifest_sha256': PAIR_MANIFEST_SHA256,
                'expected_pairs': EXPECTED_PAIRS,
                'certified_pairs': EXPECTED_PAIRS,
                'already_accepted_same_pair': len(state['existing']),
                'new_links_ready': len(state['proposal']),
                'sets': [
                    {
                        'set_code': code,
                        'idExpansion': SURFACES[code]['idExpansion'],
                        **state['per_set'][code],
                        'regional_accepted_before_or_after': state['surfaces'][code]['accepted_links'],
                    }
                    for code in ('DOCS', 'LTGY', 'CSOC')
                ],
            }
            if not apply:
                conn.rollback()
                return report

            writes = 0
            for row in state['proposal']:
                code = str(row['set_code'])
                evidence = {
                    'source': 'cardmarket_first_party_public_version_pages+current_cardmarket_product_catalog+yugioh_canonical_physical_identity',
                    'identity_basis': [
                        'first_party_cardmarket_regional_expansion_identity',
                        'frozen_first_party_public_version_rarity_contract',
                        'complete_current_metacard_product_surface',
                        'accepted_metacard_to_logical_card_bridge',
                        'complete_exact_set_JA_physical_print_surface',
                        'strict_normalized_product_to_logical_name_equality',
                        'frozen_product_ordinal_to_rarity_contract',
                        'global_product_and_print_unclaimed',
                        'global_one_to_one',
                    ],
                    'contract_sha256': CONTRACT_SHA256,
                    'pair_manifest_sha256': PAIR_MANIFEST_SHA256,
                    'idExpansion': str(row['idExpansion']),
                    'canonical_set': code,
                    'idProduct': str(row['idProduct']),
                    'idMetacard': str(row['idMetacard']),
                    'collector_number': str(row['collector_number']),
                    'canonical_variant': str(row['canonical_variant']),
                    'canonical_rarity': str(row['canonical_rarity']),
                    'product_ordinal': int(row['product_ordinal']),
                    'contract_rarity': str(row['contract_rarity']),
                    'first_party_evidence_pages': [item['url'] for item in EVIDENCE[code]['pages']],
                }
                cur.execute(
                    """INSERT INTO external_catalog_print_links(
                        external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                    ) VALUES(%s,%s,%s,'exact','accepted',true,%s)
                    ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (int(row['external_product_id']), int(row['print_id']), METHOD, Json(evidence)),
                )
                if cur.rowcount != 1:
                    raise RuntimeError({'atomic_insert_lost_race': {'idProduct': row['idProduct'], 'print_id': row['print_id']}})
                writes += 1

            if writes != len(state['proposal']):
                raise RuntimeError({'write_count_drift': {'writes': writes, 'proposal': len(state['proposal'])}})
            report['production_writes'] = writes
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Apply only the frozen 41 first-party OCG version-contract pairs')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--report', type=Path, default=Path('/tmp/yugioh-ocg-frozen-version-contract-apply-v1.json'))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    args.report.write_text(rendered, encoding='utf-8')
    print(rendered, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
