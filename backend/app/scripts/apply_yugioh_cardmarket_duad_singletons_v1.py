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
EXPANSION_ID = "6129"
EXPANSION_CODE = "DUAD-JP"
SET_CODE = "DUAD"
LANGUAGE = "ja"
EXPECTED_SINGLETONS = 38
ACCEPTED = ("accepted", "mapped", "exact")
METHOD = "cardmarket_ocg_certified_unique_physical_v2"
CONFIRM = "APPLY_YUGIOH_CARDMARKET_DUAD_SINGLETONS_V1"
IDENTITY_AUDIT_RUN = 32198593288


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
        application_name="dontripit_ygo_duad_singletons_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _build(cur):
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
        """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                  e.expansion_external_id,e.last_seen_at
           FROM external_catalog_products e
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.expansion_external_id=%s AND e.last_seen_at=%s
           ORDER BY e.metacard_external_id,e.external_id::bigint""",
        (game_id, EXPANSION_ID, capture),
    )
    products = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                  c.name card_name,s.code set_code
           FROM prints p
           JOIN cards c ON c.id=p.card_id
           JOIN sets s ON s.id=p.set_id
           WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
             AND lower(coalesce(p.language,''))=%s
           ORDER BY p.card_id,p.collector_number,p.id""",
        (game_id, SET_CODE, LANGUAGE),
    )
    canonical_prints = [dict(r) for r in cur.fetchall()]
    canonical_cards = {int(r["card_id"]) for r in canonical_prints}

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
    global_meta_rows = [dict(r) for r in cur.fetchall()]

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
    accepted_links = [dict(r) for r in cur.fetchall()]

    products_by_meta: dict[str, list[dict]] = defaultdict(list)
    for row in products:
        products_by_meta[str(row.get("metacard_external_id") or "")].append(row)

    prints_by_card: dict[int, list[dict]] = defaultdict(list)
    canonical_name_to_cards: dict[str, set[int]] = defaultdict(set)
    for row in canonical_prints:
        card_id = int(row["card_id"])
        prints_by_card[card_id].append(row)
        canonical_name_to_cards[_norm(row["card_name"])].add(card_id)

    meta_to_cards: dict[str, set[int]] = defaultdict(set)
    meta_evidence: dict[tuple[str, int], int] = defaultdict(int)
    for row in global_meta_rows:
        meta = str(row.get("metacard_external_id") or "")
        if not meta:
            continue
        card_id = int(row["card_id"])
        meta_to_cards[meta].add(card_id)
        meta_evidence[(meta, card_id)] += int(row["accepted_links"] or 0)

    existing_by_product: dict[int, list[dict]] = defaultdict(list)
    existing_by_print: dict[int, list[dict]] = defaultdict(list)
    for row in accepted_links:
        existing_by_product[int(row["external_product_id"])].append(row)
        existing_by_print[int(row["print_id"])].append(row)

    safe_pairs: list[dict] = []
    rejected: list[dict] = []
    for meta, group in products_by_meta.items():
        if not meta or len(group) != 1:
            continue
        product = group[0]
        names = {str(r.get("name") or "") for r in group}
        if len(names) != 1:
            rejected.append({"idMetacard": meta, "reason": "singleton_product_name_drift"})
            continue

        global_cards = sorted(meta_to_cards.get(meta, set()))
        normalized_name_cards = canonical_name_to_cards.get(_norm(product.get("name")), set())
        card_id = None
        resolution_method = None

        if len(global_cards) == 1 and global_cards[0] in canonical_cards:
            card_id = global_cards[0]
            resolution_method = "accepted_global_metacard_to_duad_card"
        elif len(global_cards) == 1 and global_cards[0] not in canonical_cards:
            continue
        elif len(global_cards) > 1:
            intersection = sorted(set(global_cards) & canonical_cards & set(normalized_name_cards))
            if len(intersection) == 1:
                card_id = intersection[0]
                resolution_method = "ambiguous_global_metacard_resolved_by_strict_normalized_name"
            else:
                continue
        elif len(normalized_name_cards) == 1:
            card_id = next(iter(normalized_name_cards))
            resolution_method = "strict_normalized_name_to_unique_duad_card"
        else:
            continue

        card_prints = prints_by_card.get(int(card_id), [])
        if len(card_prints) != 1:
            continue
        print_row = card_prints[0]
        if _norm(product.get("name")) != _norm(print_row.get("card_name")):
            rejected.append(
                {
                    "idMetacard": meta,
                    "idProduct": str(product["id_product"]),
                    "print_id": int(print_row["print_id"]),
                    "reason": "strict_normalized_name_mismatch",
                }
            )
            continue

        external_product_id = int(product["external_product_id"])
        print_id = int(print_row["print_id"])
        product_claims = existing_by_product.get(external_product_id, [])
        print_claims = existing_by_print.get(print_id, [])
        same_existing = [
            r for r in product_claims
            if int(r["print_id"]) == print_id
        ]
        conflicting_product = [
            r for r in product_claims
            if int(r["print_id"]) != print_id
        ]
        conflicting_print = [
            r for r in print_claims
            if int(r["external_product_id"]) != external_product_id
        ]
        if conflicting_product or conflicting_print:
            raise RuntimeError(
                {
                    "accepted_identity_conflict": {
                        "idProduct": str(product["id_product"]),
                        "print_id": print_id,
                        "product_claims": conflicting_product,
                        "print_claims": conflicting_print,
                    }
                }
            )
        if len(same_existing) > 1:
            raise RuntimeError({"duplicate_same_pair_links": str(product["id_product"])})

        safe_pairs.append(
            {
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
                "already_accepted_same_pair": bool(same_existing),
                "existing_mapping_method": same_existing[0]["mapping_method"] if same_existing else None,
            }
        )

    if len(products) != 121:
        raise RuntimeError({"DUAD_products_drift": len(products)})
    if len(canonical_prints) != 117:
        raise RuntimeError({"DUAD_canonical_prints_drift": len(canonical_prints)})
    if len(safe_pairs) != EXPECTED_SINGLETONS:
        raise RuntimeError(
            {
                "DUAD_singleton_pair_count_drift": {
                    "expected": EXPECTED_SINGLETONS,
                    "actual": len(safe_pairs),
                    "rejected": rejected,
                }
            }
        )
    if len({r["external_product_id"] for r in safe_pairs}) != EXPECTED_SINGLETONS:
        raise RuntimeError("DUAD singleton products are not one-to-one")
    if len({r["print_id"] for r in safe_pairs}) != EXPECTED_SINGLETONS:
        raise RuntimeError("DUAD singleton prints are not one-to-one")

    existing_same = [r for r in safe_pairs if r["already_accepted_same_pair"]]
    new_pairs = [r for r in safe_pairs if not r["already_accepted_same_pair"]]
    return {
        "game_id": game_id,
        "capture": capture,
        "products": len(products),
        "canonical_prints": len(canonical_prints),
        "safe_pairs": safe_pairs,
        "existing_same": existing_same,
        "new_pairs": new_pairs,
    }


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
                "cardmarket_capture": str(built["capture"]),
                "idExpansion": EXPANSION_ID,
                "expansion_code": EXPANSION_CODE,
                "canonical_set": SET_CODE,
                "language": LANGUAGE,
                "mapping_method": METHOD,
                "expected_singletons": EXPECTED_SINGLETONS,
                "certified_pairs": len(built["safe_pairs"]),
                "already_accepted_same_pair": len(built["existing_same"]),
                "new_links_ready": len(built["new_pairs"]),
                "pairs": built["safe_pairs"],
            }
            if not apply:
                conn.rollback()
                return report

            for row in built["new_pairs"]:
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical_identity",
                    "identity_basis": [
                        "DUAD-JP_Cardmarket_expansion_6129_certified_read_only",
                        "one_product_for_metacard_inside_certified_regional_expansion",
                        "one_canonical_JA_DUAD_print_for_resolved_card",
                        "strict_normalized_name_match",
                        "global_product_and_print_unclaimed",
                        "global_one_to_one",
                    ],
                    "identity_audit_workflow_run_id": IDENTITY_AUDIT_RUN,
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "canonical_set": SET_CODE,
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
                       )
                       VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (row["external_product_id"], row["print_id"], METHOD, Json(evidence)),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        {
                            "expected_single_insert_failed": {
                                "idProduct": row["idProduct"],
                                "print_id": row["print_id"],
                                "rowcount": cur.rowcount,
                            }
                        }
                    )

            report["production_writes"] = len(built["new_pairs"])
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply certified DUAD-JP singleton Cardmarket mappings")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/yugioh-cardmarket-duad-singletons-v1.json"),
    )
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
