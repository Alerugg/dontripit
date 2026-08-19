from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from psycopg2.extras import Json, RealDictCursor

from app.scripts import apply_yugioh_ocg_full_bijection_cohort_v1 as v1

# V1 froze a proposal hash that accidentally included metacard_evidence_links.
# Those evidence counts are mutable by design: once the audited links are
# accepted, the same exact identities contribute additional accepted evidence.
# Freeze identity geometry only so the guard remains valid before and after
# installation while every V1 live identity/competition/conflict guard remains.
STABLE_IDENTITY_SHA256='9a2b8ed9c43c0110fd8818a4b91239b26a565addaf5a88f4d898f6d6d41311ac'
IDENTITY_FIELDS=(
    'set_code','idExpansion','external_product_id','idProduct','idMetacard',
    'print_id','card_id','card_name','collector_number','canonical_rarity','canonical_variant',
)
METHOD=v1.METHOD
CONFIRM=v1.CONFIRM
EXPECTED_TOTAL=v1.EXPECTED_TOTAL


def _derive_v1_without_mutable_hash_false_positive(cur):
    """Run every V1 guard but neutralize only its mutable evidence-count hash."""
    original=v1.FROZEN_PROPOSAL_SHA256
    try:
        try:
            state=v1.derive(cur)
        except RuntimeError as exc:
            payload=exc.args[0] if exc.args else None
            if not isinstance(payload,dict) or set(payload)!={'frozen_proposal_hash_drift'}:
                raise
            observed=str(payload['frozen_proposal_hash_drift'])
            if len(observed)!=64:
                raise
            v1.FROZEN_PROPOSAL_SHA256=observed
            state=v1.derive(cur)
    finally:
        v1.FROZEN_PROPOSAL_SHA256=original
    return state


def derive(cur):
    state=_derive_v1_without_mutable_hash_false_positive(cur)
    rows=state['proposal']+state['existing']
    identity=[{k:r.get(k) for k in IDENTITY_FIELDS} for r in rows]
    raw=json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    stable=hashlib.sha256(raw).hexdigest()
    if stable!=STABLE_IDENTITY_SHA256:
        raise RuntimeError({'stable_identity_hash_drift':stable})
    if len(rows)!=EXPECTED_TOTAL:
        raise RuntimeError({'stable_identity_count_drift':len(rows)})
    state['stable_identity_sha256']=stable
    return state


def run(apply: bool=False, confirm: str=''):
    if apply and confirm!=CONFIRM:
        raise RuntimeError(f'--apply requires --confirm {CONFIRM}')
    conn=v1.connect(not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state=derive(cur)
            report={
                'mode':'apply' if apply else 'dry_run',
                'status':'pass',
                'production_writes':0,
                'mapping_method':METHOD,
                'cardmarket_capture':str(state['capture']),
                'ja_baseline':state['ja'],
                'stable_identity_sha256':state['stable_identity_sha256'],
                'certified_pairs':EXPECTED_TOTAL,
                'already_accepted_same_pair':len(state['existing']),
                'new_links_ready':len(state['proposal']),
                'sets':state['sets'],
            }
            if not apply:
                conn.rollback()
                return report
            writes=0
            for x in state['proposal']:
                evidence={
                    'source':'fresh_global_OCG_inventory+accepted_metacard_bridge+canonical_JA_full_bijection',
                    'identity_basis':[
                        'pinned_current_cardmarket_capture',
                        'complete_expansion_product_count_equals_exact_JA_physical_count',
                        'canonical_physical_equals_logical_cardinality',
                        'unique_accepted_metacard_to_logical_card',
                        'resolved_logical_card_set_equals_full_canonical_set',
                        'strict_normalized_name_match',
                        'no_competing_full_bijection_expansion',
                        'global_product_and_print_unclaimed',
                        'stable_audited_identity_hash',
                    ],
                    'stable_identity_sha256':STABLE_IDENTITY_SHA256,
                    'preinstall_proposal_sha256':v1.FROZEN_PROPOSAL_SHA256,
                    'idExpansion':x['idExpansion'],
                    'canonical_set':x['set_code'],
                    'idProduct':x['idProduct'],
                    'idMetacard':x['idMetacard'],
                    'collector_number':x['collector_number'],
                    'canonical_variant':x['canonical_variant'],
                    'canonical_rarity':x['canonical_rarity'],
                    'metacard_evidence_links_at_write':x['metacard_evidence_links'],
                }
                cur.execute(
                    """INSERT INTO external_catalog_print_links(
                           external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                       VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (x['external_product_id'],x['print_id'],METHOD,Json(evidence)),
                )
                if cur.rowcount!=1:
                    raise RuntimeError({'insert_failed':x['idProduct']})
                writes+=1
            report['production_writes']=writes
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confirm',default='')
    parser.add_argument('--report',type=Path,default=Path('/tmp/yugioh-ocg-full-bijection-cohort-apply-v2.json'))
    args=parser.parse_args()
    payload=run(args.apply,args.confirm)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n'
    args.report.write_text(text,encoding='utf-8')
    print(text,end='')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
