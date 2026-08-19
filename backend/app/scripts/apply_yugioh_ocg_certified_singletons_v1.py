from __future__ import annotations

import argparse
import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

GAME = "yugioh"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
METHOD = "cardmarket_ocg_certified_unique_physical_v3"
CONFIRM = "APPLY_YUGIOH_OCG_CERTIFIED_SINGLETONS_V1"
GLOBAL_AUDIT_RUN = 32265085144
IDENTITY_RUNS = (32265478046, 32265755352, 32265970824)
EXPECTED_TOTAL = 738
SURFACES = {
    "ROTA": {"idExpansion": "5840", "products": 132, "prints": 132, "pairs": 62},
    "SUDA": {"idExpansion": "5929", "products": 132, "prints": 132, "pairs": 58},
    "INFO": {"idExpansion": "5753", "products": 132, "prints": 132, "pairs": 61},
    "LEDE": {"idExpansion": "5608", "products": 132, "prints": 132, "pairs": 62},
    "PHNI": {"idExpansion": "5533", "products": 132, "prints": 132, "pairs": 62},
    "DUNE": {"idExpansion": "5326", "products": 134, "prints": 132, "pairs": 60},
    "CYAC": {"idExpansion": "5242", "products": 126, "prints": 126, "pairs": 63},
    "PHHY": {"idExpansion": "5166", "products": 126, "prints": 126, "pairs": 61},
    "DABL": {"idExpansion": "5107", "products": 127, "prints": 126, "pairs": 63},
    "DIFO": {"idExpansion": "4519", "products": 127, "prints": 126, "pairs": 62},
    "BACH": {"idExpansion": "4524", "products": 127, "prints": 126, "pairs": 62},
    "BODE": {"idExpansion": "4528", "products": 127, "prints": 126, "pairs": 62},
}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_certified_singletons_apply_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _build(cur) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game_id = int(cur.fetchone()["id"])
    cur.execute(
        "SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
        (game_id,),
    )
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Current Cardmarket capture missing")

    cur.execute(
        """SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id
           WHERE c.game_id=%s AND lower(coalesce(p.language,''))=%s""",
        (game_id, LANGUAGE),
    )
    ja_baseline = int(cur.fetchone()["n"])
    if ja_baseline != 36426:
        raise RuntimeError({"yugioh_ja_baseline_drift": ja_baseline})

    cur.execute(
        """SELECT e.metacard_external_id,p.card_id,count(*) accepted_links
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           JOIN prints p ON p.id=l.print_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
           GROUP BY e.metacard_external_id,p.card_id""",
        (game_id, list(ACCEPTED)),
    )
    meta_to_cards: dict[str, set[int]] = defaultdict(set)
    meta_evidence: dict[tuple[str, int], int] = defaultdict(int)
    for row in cur.fetchall():
        meta = str(row.get("metacard_external_id") or "")
        cid = int(row["card_id"])
        meta_to_cards[meta].add(cid)
        meta_evidence[(meta, cid)] += int(row["accepted_links"] or 0)

    cur.execute(
        """SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,
                  e.external_id id_product,e.expansion_external_id,p.language,s.code set_code
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           JOIN prints p ON p.id=l.print_id
           JOIN sets s ON s.id=p.set_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND l.link_status=ANY(%s)""",
        (game_id, list(ACCEPTED)),
    )
    existing_by_product: dict[int, list[dict]] = defaultdict(list)
    existing_by_print: dict[int, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        item = dict(row)
        existing_by_product[int(row["external_product_id"])].append(item)
        existing_by_print[int(row["print_id"])].append(item)

    reports = []
    all_pairs = []
    for set_code, cfg in SURFACES.items():
        expansion_id = cfg["idExpansion"]
        cur.execute(
            """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
               FROM external_catalog_products e
               WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                 AND e.expansion_external_id=%s AND e.last_seen_at=%s
               ORDER BY e.metacard_external_id,e.external_id::bigint""",
            (game_id, expansion_id, capture),
        )
        products = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name,s.id set_id
               FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
               WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                 AND lower(coalesce(p.language,''))=%s
               ORDER BY p.card_id,p.collector_number,p.id""",
            (game_id, set_code, LANGUAGE),
        )
        canonical = [dict(r) for r in cur.fetchall()]
        if len(products) != int(cfg["products"]) or len(canonical) != int(cfg["prints"]):
            raise RuntimeError(
                {
                    "certified_surface_drift": {
                        "set": set_code,
                        "expected_products": cfg["products"],
                        "actual_products": len(products),
                        "expected_prints": cfg["prints"],
                        "actual_prints": len(canonical),
                    }
                }
            )

        canonical_cards = {int(r["card_id"]) for r in canonical}
        prints_by_card: dict[int, list[dict]] = defaultdict(list)
        name_to_cards: dict[str, set[int]] = defaultdict(set)
        for row in canonical:
            cid = int(row["card_id"])
            prints_by_card[cid].append(row)
            name_to_cards[_norm(row["card_name"])].add(cid)
        products_by_meta: dict[str, list[dict]] = defaultdict(list)
        for row in products:
            products_by_meta[str(row.get("metacard_external_id") or "")].append(row)

        pairs = []
        for meta, group in products_by_meta.items():
            if not meta or len(group) != 1:
                continue
            product = group[0]
            global_cards = sorted(meta_to_cards.get(meta, set()))
            name_cards = name_to_cards.get(_norm(product.get("name")), set())
            card_id = None
            resolution_method = None
            if len(global_cards) == 1 and global_cards[0] in canonical_cards:
                card_id = global_cards[0]
                resolution_method = "accepted_global_metacard_to_certified_set_card"
            elif len(global_cards) > 1:
                intersection = sorted(set(global_cards) & canonical_cards & set(name_cards))
                if len(intersection) == 1:
                    card_id = intersection[0]
                    resolution_method = "ambiguous_metacard_resolved_by_certified_set_and_name"
            if card_id is None:
                continue
            card_prints = prints_by_card.get(int(card_id), [])
            if len(card_prints) != 1:
                continue
            print_row = card_prints[0]
            if _norm(product.get("name")) != _norm(print_row.get("card_name")):
                continue

            external_product_id = int(product["external_product_id"])
            print_id = int(print_row["print_id"])
            product_claims = existing_by_product.get(external_product_id, [])
            print_claims = existing_by_print.get(print_id, [])
            same = [r for r in product_claims if int(r["print_id"]) == print_id]
            conflicting_product = [r for r in product_claims if int(r["print_id"]) != print_id]
            conflicting_print = [r for r in print_claims if int(r["external_product_id"]) != external_product_id]
            if conflicting_product or conflicting_print:
                raise RuntimeError(
                    {
                        "accepted_identity_conflict": {
                            "set": set_code,
                            "idProduct": str(product["id_product"]),
                            "print_id": print_id,
                            "product_claims": conflicting_product,
                            "print_claims": conflicting_print,
                        }
                    }
                )
            if len(same) > 1:
                raise RuntimeError({"duplicate_same_pair": {"set": set_code, "idProduct": product["id_product"]}})
            if same:
                existing = same[0]
                if str(existing.get("mapping_method") or "") != METHOD or str(existing.get("confidence") or "") != "exact" or not bool(existing.get("reviewed")):
                    raise RuntimeError({"unexpected_existing_same_pair": {"set": set_code, "idProduct": product["id_product"], "existing": existing}})

            pairs.append(
                {
                    "set_code": set_code,
                    "idExpansion": expansion_id,
                    "external_product_id": external_product_id,
                    "idProduct": str(product["id_product"]),
                    "idMetacard": meta,
                    "print_id": print_id,
                    "card_id": int(card_id),
                    "card_name": str(print_row["card_name"]),
                    "collector_number": str(print_row["collector_number"]),
                    "canonical_rarity": print_row.get("rarity"),
                    "canonical_variant": print_row.get("variant"),
                    "resolution_method": resolution_method,
                    "metacard_evidence_links": int(meta_evidence.get((meta, int(card_id)), 0)),
                    "already_accepted_same_pair": bool(same),
                }
            )

        if len(pairs) != int(cfg["pairs"]):
            raise RuntimeError({"certified_pair_count_drift": {"set": set_code, "expected": cfg["pairs"], "actual": len(pairs)}})
        if len({r["external_product_id"] for r in pairs}) != len(pairs) or len({r["print_id"] for r in pairs}) != len(pairs):
            raise RuntimeError({"certified_pairs_not_one_to_one": set_code})
        existing_count = sum(bool(r["already_accepted_same_pair"]) for r in pairs)
        new_count = len(pairs) - existing_count
        if (existing_count, new_count) not in {(0, int(cfg["pairs"])), (int(cfg["pairs"]), 0)}:
            raise RuntimeError({"partial_surface_blocked": {"set": set_code, "existing": existing_count, "new": new_count}})
        all_pairs.extend(pairs)
        reports.append({"set_code": set_code, "idExpansion": expansion_id, "products": len(products), "canonical_ja_prints": len(canonical), "pairs": len(pairs), "existing_same": existing_count, "new_ready": new_count})

    if len(all_pairs) != EXPECTED_TOTAL:
        raise RuntimeError({"global_pair_count_drift": {"expected": EXPECTED_TOTAL, "actual": len(all_pairs)}})
    if len({r["external_product_id"] for r in all_pairs}) != EXPECTED_TOTAL or len({r["print_id"] for r in all_pairs}) != EXPECTED_TOTAL:
        raise RuntimeError("global certified singleton pairs are not one-to-one")
    existing_total = sum(bool(r["already_accepted_same_pair"]) for r in all_pairs)
    new_total = EXPECTED_TOTAL - existing_total
    if (existing_total, new_total) not in {(0, EXPECTED_TOTAL), (EXPECTED_TOTAL, 0)}:
        raise RuntimeError({"global_partial_state_blocked": {"existing": existing_total, "new": new_total}})
    return {"game_id": game_id, "capture": capture, "ja_baseline": ja_baseline, "pairs": all_pairs, "sets": reports, "existing_total": existing_total, "new_total": new_total}


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            built = _build(cur)
            report = {
                "mode": "apply" if apply else "dry_run",
                "status": "pass",
                "production_writes": 0,
                "game": GAME,
                "language": LANGUAGE,
                "mapping_method": METHOD,
                "cardmarket_capture": str(built["capture"]),
                "ja_baseline": built["ja_baseline"],
                "expected_total": EXPECTED_TOTAL,
                "certified_pairs": len(built["pairs"]),
                "already_accepted_same_pair": built["existing_total"],
                "new_links_ready": built["new_total"],
                "sets": built["sets"],
            }
            if not apply:
                conn.rollback()
                return report

            new_pairs = [r for r in built["pairs"] if not r["already_accepted_same_pair"]]
            for row in new_pairs:
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical_identity",
                    "identity_basis": [
                        "global_OCG_surface_inventory",
                        "first_party_Cardmarket_region_code_image_certification",
                        "one_product_for_metacard_inside_certified_regional_expansion",
                        "accepted_metacard_to_logical_card_bridge",
                        "one_canonical_JA_print_for_resolved_card_in_exact_set",
                        "strict_normalized_name_match",
                        "global_product_and_print_unclaimed",
                        "global_one_to_one",
                    ],
                    "global_audit_workflow_run_id": GLOBAL_AUDIT_RUN,
                    "identity_workflow_run_ids": list(IDENTITY_RUNS),
                    "idExpansion": row["idExpansion"],
                    "canonical_set": row["set_code"],
                    "idProduct": row["idProduct"],
                    "idMetacard": row["idMetacard"],
                    "collector_number": row["collector_number"],
                    "canonical_variant": row["canonical_variant"],
                    "canonical_rarity": row["canonical_rarity"],
                    "resolution_method": row["resolution_method"],
                    "metacard_evidence_links": row["metacard_evidence_links"],
                }
                cur.execute(
                    """INSERT INTO external_catalog_print_links(
                           external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                       ) VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (row["external_product_id"], row["print_id"], METHOD, Json(evidence)),
                )
                if cur.rowcount != 1:
                    raise RuntimeError({"expected_single_insert_failed": {"idProduct": row["idProduct"], "print_id": row["print_id"], "rowcount": cur.rowcount}})
            report["production_writes"] = len(new_pairs)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply exact singleton links for certified Yu-Gi-Oh OCG expansions")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-ocg-certified-singletons-apply-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
