from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_ALIN_POTE_UNIQUE_V1"
METHOD = "cardmarket_ocg_certified_unique_physical_v2"
SURFACES = {
    "alin_jp": {
        "idExpansion": "6025",
        "expansion_code": "ALIN-JP",
        "set_code": "ALIN",
        "expected": 55,
        "identity_audit_run": 32192524841,
        "surface_audit_run": 32192725166,
    },
    "pote_jp": {
        "idExpansion": "5044",
        "expansion_code": "POTE-JP",
        "set_code": "POTE",
        "expected": 63,
        "identity_audit_run": 32192524841,
        "surface_audit_run": 32192725166,
    },
}
EXPECTED_TOTAL = sum(cfg["expected"] for cfg in SURFACES.values())


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_alin_pote_unique_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_shared_state(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("Yu-Gi-Oh game row missing")
    game_id = int(game["id"])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Current Cardmarket capture missing")

    cur.execute(
        """SELECT e.metacard_external_id,p.card_id,count(*) evidence_links
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           JOIN prints p ON p.id=l.print_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
           GROUP BY e.metacard_external_id,p.card_id""",
        (game_id, list(ACCEPTED)),
    )
    meta_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """SELECT l.external_product_id,l.print_id,e.external_id id_product,e.expansion_external_id
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND l.link_status=ANY(%s)""",
        (game_id, list(ACCEPTED)),
    )
    accepted = [dict(r) for r in cur.fetchall()]
    return game_id, capture, meta_rows, accepted


def _build_surface(cur, *, game_id: int, capture, meta_rows: list[dict], accepted: list[dict], key: str, cfg: dict):
    cur.execute(
        """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                  e.expansion_external_id,e.last_seen_at
           FROM external_catalog_products e
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.expansion_external_id=%s AND e.last_seen_at=%s
           ORDER BY e.external_id::bigint""",
        (game_id, cfg["idExpansion"], capture),
    )
    products = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                  c.name card_name,s.code set_code
           FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
           WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'
             AND upper(coalesce(s.code,''))=%s
           ORDER BY p.id""",
        (game_id, cfg["set_code"]),
    )
    prints = [dict(r) for r in cur.fetchall()]

    meta_cards = defaultdict(set)
    meta_evidence = defaultdict(int)
    for row in meta_rows:
        meta = str(row["metacard_external_id"])
        card_id = int(row["card_id"])
        meta_cards[meta].add(card_id)
        meta_evidence[(meta, card_id)] += int(row["evidence_links"] or 0)

    products_by_meta = defaultdict(list)
    for row in products:
        if row.get("metacard_external_id") is not None:
            products_by_meta[str(row["metacard_external_id"])].append(row)
    prints_by_card = defaultdict(list)
    for row in prints:
        prints_by_card[int(row["card_id"])].append(row)

    claimed_products = {int(r["external_product_id"]) for r in accepted}
    claimed_prints = {int(r["print_id"]) for r in accepted}
    existing_surface = [r for r in accepted if str(r.get("expansion_external_id") or "") == cfg["idExpansion"]]
    if existing_surface:
        raise RuntimeError({"surface_already_has_accepted_links": key, "count": len(existing_surface)})

    rejected = Counter()
    proposal = []
    for product in products:
        external_product_id = int(product["external_product_id"])
        if external_product_id in claimed_products:
            rejected["product_already_claimed"] += 1
            continue
        meta = str(product.get("metacard_external_id") or "")
        cards = meta_cards.get(meta, set())
        if len(cards) != 1:
            rejected["metacard_not_one_canonical_card"] += 1
            continue
        card_id = next(iter(cards))
        if len(products_by_meta.get(meta, [])) != 1:
            rejected["regional_variant_ambiguity"] += 1
            continue
        canonical = [r for r in prints_by_card.get(card_id, []) if int(r["print_id"]) not in claimed_prints]
        if len(canonical) != 1:
            rejected["canonical_variant_ambiguity"] += 1
            continue
        print_row = canonical[0]
        if norm(product["name"]) != norm(print_row["card_name"]):
            rejected["name_mismatch"] += 1
            continue
        proposal.append(
            {
                "surface": key,
                "external_product_id": external_product_id,
                "idProduct": str(product["id_product"]),
                "idMetacard": meta,
                "print_id": int(print_row["print_id"]),
                "card_id": card_id,
                "card_name": print_row["card_name"],
                "collector_number": print_row["collector_number"],
                "canonical_variant": print_row["variant"],
                "canonical_rarity": print_row["rarity"],
                "metacard_evidence_links": meta_evidence[(meta, card_id)],
            }
        )

    if len(products) != len(prints):
        raise RuntimeError({"surface_not_balanced": key, "products": len(products), "prints": len(prints)})
    if len(proposal) != int(cfg["expected"]):
        raise RuntimeError(
            {
                "proposal_count_drift": key,
                "expected": cfg["expected"],
                "actual": len(proposal),
                "products": len(products),
                "prints": len(prints),
                "rejected": dict(rejected),
            }
        )
    return {
        "key": key,
        "products": len(products),
        "prints": len(prints),
        "rejected": dict(rejected),
        "proposal": proposal,
    }


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id, capture, meta_rows, accepted = _load_shared_state(cur)
            built = [
                _build_surface(cur, game_id=game_id, capture=capture, meta_rows=meta_rows, accepted=accepted, key=key, cfg=cfg)
                for key, cfg in SURFACES.items()
            ]
            proposal = [row for surface in built for row in surface["proposal"]]
            product_ids = [int(r["external_product_id"]) for r in proposal]
            print_ids = [int(r["print_id"]) for r in proposal]
            if len(proposal) != EXPECTED_TOTAL:
                raise RuntimeError(f"total proposal drifted: expected={EXPECTED_TOTAL} actual={len(proposal)}")
            if len(set(product_ids)) != EXPECTED_TOTAL or len(set(print_ids)) != EXPECTED_TOTAL:
                raise RuntimeError("combined ALIN/POTE proposal is not globally one-to-one")

            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "game": GAME,
                "cardmarket_capture": str(capture),
                "mapping_method": METHOD,
                "expected_total": EXPECTED_TOTAL,
                "proposed_exact_links": len(proposal),
                "surfaces": [
                    {
                        "key": surface["key"],
                        "products": surface["products"],
                        "prints": surface["prints"],
                        "rejected": surface["rejected"],
                        "proposed": len(surface["proposal"]),
                    }
                    for surface in built
                ],
                "proposal": proposal,
            }
            if not apply:
                conn.rollback()
                return report

            for row in proposal:
                cfg = SURFACES[row["surface"]]
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical_identity",
                    "identity_basis": [
                        "first_party_cardmarket_expansion_code_certified_by_multiple_image_anchors",
                        "cardmarket_metacard_to_one_canonical_card",
                        "one_product_for_metacard_inside_certified_regional_expansion",
                        "one_unclaimed_JA_print_for_card_inside_exact_canonical_set",
                        "normalized_name_match",
                        "global_one_to_one",
                    ],
                    "identity_audit_workflow_run_id": cfg["identity_audit_run"],
                    "surface_audit_workflow_run_id": cfg["surface_audit_run"],
                    "idExpansion": cfg["idExpansion"],
                    "expansion_code": cfg["expansion_code"],
                    "canonical_set": cfg["set_code"],
                    "idProduct": row["idProduct"],
                    "idMetacard": row["idMetacard"],
                    "collector_number": row["collector_number"],
                    "canonical_variant": row["canonical_variant"],
                    "canonical_rarity": row["canonical_rarity"],
                    "metacard_evidence_links": row["metacard_evidence_links"],
                }
                cur.execute(
                    """INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                       VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO UPDATE SET
                         mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,
                         evidence=EXCLUDED.evidence,updated_at=now()""",
                    (row["external_product_id"], row["print_id"], METHOD, Json(evidence)),
                )

            post = {}
            for key, cfg in SURFACES.items():
                cur.execute(
                    """SELECT count(*) n FROM external_catalog_print_links l
                       JOIN external_catalog_products e ON e.id=l.external_product_id
                       JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                       WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                         AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                         AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s""",
                    (game_id, cfg["idExpansion"], list(ACCEPTED), cfg["set_code"]),
                )
                count = int(cur.fetchone()["n"])
                if count != cfg["expected"]:
                    raise RuntimeError({"post_apply_count_failed": key, "expected": cfg["expected"], "actual": count})
                post[key] = count
            report["accepted_after"] = post
            report["production_writes"] = len(proposal)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically apply certified unique ALIN-JP and POTE-JP Cardmarket mappings")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-alin-pote-unique-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
