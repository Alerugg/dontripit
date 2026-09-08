#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values

MAPPING_METHOD = "ygo_konami_rarity_bridge_v3_exact"


def load_cert(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "read_only" or payload.get("game") != "yugioh":
        raise SystemExit("REFUSED: expected read-only YGO V3 certification")
    if payload.get("resolver") != "cardmarket_konami_rarity_bridge_v3":
        raise SystemExit("REFUSED: wrong resolver certificate")
    summary = payload.get("summary") or {}
    blockers = []
    if not summary.get("write_ready"):
        blockers.append("certificate write_ready=false")
    if int(summary.get("loo_alt_wrong") or 0) != 0:
        blockers.append("leave-one-out has wrong predictions")
    if int(summary.get("duplicate_target_prints") or 0) != 0:
        blockers.append("certificate has duplicate target prints")
    candidates = payload.get("safe_candidates") or []
    if len(candidates) != int(summary.get("safe_candidate_external_products") or 0):
        blockers.append("candidate count mismatch")
    if blockers:
        raise SystemExit("REFUSED: " + "; ".join(blockers))
    return payload


def build_plan(conn, cert: dict) -> dict:
    candidates = cert.get("safe_candidates") or []
    external_ids = sorted({int(r["external_product_id"]) for r in candidates})
    target_ids = sorted({int(pid) for r in candidates for pid in r.get("target_print_ids", [])})
    proposed_by_external = {
        int(r["external_product_id"]): {int(pid) for pid in r.get("target_print_ids", [])}
        for r in candidates
    }
    proposed_pairs = {
        (int(r["external_product_id"]), int(pid))
        for r in candidates for pid in r.get("target_print_ids", [])
    }

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug='yugioh'")
        game_id = int(cur.fetchone()["id"])
        if external_ids:
            cur.execute(
                """
                SELECT id, external_id, game_id, source, product_group
                FROM external_catalog_products WHERE id = ANY(%s)
                """,
                (external_ids,),
            )
            products = {int(r["id"]): dict(r) for r in cur.fetchall()}
        else:
            products = {}
        if target_ids:
            cur.execute(
                """
                SELECT p.id AS print_id, c.game_id
                FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE p.id = ANY(%s)
                """,
                (target_ids,),
            )
            prints = {int(r["print_id"]): int(r["game_id"]) for r in cur.fetchall()}
        else:
            prints = {}
        if external_ids:
            cur.execute(
                """
                SELECT external_product_id, print_id, link_status, confidence, mapping_method
                FROM external_catalog_print_links
                WHERE external_product_id = ANY(%s)
                """,
                (external_ids,),
            )
            links_for_external = [dict(r) for r in cur.fetchall()]
        else:
            links_for_external = []
        if target_ids:
            cur.execute(
                """
                SELECT l.external_product_id, l.print_id, l.link_status, l.confidence, l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products ecp ON ecp.id=l.external_product_id
                WHERE l.print_id = ANY(%s)
                  AND ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                  AND l.link_status IN ('accepted','mapped','exact') AND l.confidence='exact'
                """,
                (target_ids, game_id),
            )
            exact_links_for_target = [dict(r) for r in cur.fetchall()]
        else:
            exact_links_for_target = []

    blockers = []
    for pk in external_ids:
        product = products.get(pk)
        if product is None:
            blockers.append({"type": "missing_external_product", "external_product_id": pk})
            continue
        if int(product["game_id"]) != game_id or product["source"] != "cardmarket" or product["product_group"] != "single":
            blockers.append({"type": "external_product_identity_mismatch", "external_product_id": pk})
    for pid in target_ids:
        if prints.get(pid) != game_id:
            blockers.append({"type": "missing_or_cross_game_print", "print_id": pid})

    existing_same_pairs = set()
    for row in links_for_external:
        pair = (int(row["external_product_id"]), int(row["print_id"]))
        pk = pair[0]
        allowed_targets = proposed_by_external.get(pk, set())
        is_accepted_exact = row["confidence"] == "exact" and row["link_status"] in ("accepted", "mapped", "exact")
        if pair in proposed_pairs and is_accepted_exact:
            existing_same_pairs.add(pair)
            continue
        blockers.append({
            "type": "external_product_has_unexpected_existing_link",
            "external_product_id": pk,
            "print_id": pair[1],
            "allowed_targets": sorted(allowed_targets),
            "status": row["link_status"],
            "confidence": row["confidence"],
            "mapping_method": row["mapping_method"],
        })

    for row in exact_links_for_target:
        pair = (int(row["external_product_id"]), int(row["print_id"]))
        if pair in proposed_pairs:
            continue
        blockers.append({
            "type": "target_already_claimed_by_other_exact_cardmarket_product",
            "external_product_id": pair[0],
            "print_id": pair[1],
            "mapping_method": row["mapping_method"],
        })

    missing_pairs = sorted(proposed_pairs - existing_same_pairs)
    return {
        "game_id": game_id,
        "certified_external_products": len(candidates),
        "certified_pairs": len(proposed_pairs),
        "already_exact_same_pairs": len(existing_same_pairs),
        "candidate_links": len(missing_pairs),
        "missing_pairs": missing_pairs,
        "blockers": blockers,
        "write_ready": not blockers,
    }


def apply_plan(conn, cert: dict, plan: dict) -> dict:
    if not plan["write_ready"]:
        raise SystemExit("REFUSED: preflight blockers present")
    candidate_by_external = {int(r["external_product_id"]): r for r in cert.get("safe_candidates") or []}
    rows = []
    for external_product_id, print_id in plan["missing_pairs"]:
        candidate = candidate_by_external[external_product_id]
        evidence = {
            "source": "cardmarket",
            "resolver": "cardmarket_konami_rarity_bridge_v3",
            "identity_basis": [
                "certified_cardmarket_expansion_to_konami_release_consensus",
                "exact_normalized_card_name_inside_official_release",
                "semantic_rarity_partition",
                "alternate_version_ordinal_leave_one_group_out_zero_wrong",
                "base_version_only_when_uniquely_eliminated_or_single_page",
                "target_not_claimed_by_other_exact_cardmarket_product",
            ],
            "cardmarket_idProduct": candidate["idProduct"],
            "cardmarket_expansion_id": candidate["expansion"],
            "konami_pid": candidate["konami_pid"],
            "ordinal": candidate["ordinal"],
            "product_count": candidate["product_count"],
            "normalized_rarity": candidate["rarity"],
        }
        rows.append((external_product_id, print_id, MAPPING_METHOD, "exact", "accepted", False, Json(evidence)))

    with conn.cursor() as cur:
        if rows:
            execute_values(
                cur,
                """
                INSERT INTO external_catalog_print_links
                  (external_product_id, print_id, mapping_method, confidence, link_status, reviewed, evidence)
                VALUES %s
                ON CONFLICT (external_product_id, print_id) DO NOTHING
                """,
                rows,
                page_size=500,
            )
            affected = int(cur.rowcount or 0)
        else:
            affected = 0

        proposed_external = sorted({int(r["external_product_id"]) for r in cert.get("safe_candidates") or []})
        proposed_targets = sorted({int(pid) for r in cert.get("safe_candidates") or [] for pid in r.get("target_print_ids", [])})
        cur.execute(
            """
            SELECT count(*)
            FROM external_catalog_print_links l
            WHERE (l.external_product_id, l.print_id) IN (
              SELECT x.external_product_id, x.print_id
              FROM unnest(%s::bigint[], %s::bigint[]) AS x(external_product_id, print_id)
            )
              AND l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact')
            """,
            (
                [pair[0] for pair in sorted({(int(r["external_product_id"]), int(pid)) for r in cert.get("safe_candidates") or [] for pid in r.get("target_print_ids", [])})],
                [pair[1] for pair in sorted({(int(r["external_product_id"]), int(pid)) for r in cert.get("safe_candidates") or [] for pid in r.get("target_print_ids", [])})],
            ),
        )
        exact_pairs_after = int(cur.fetchone()[0])

        if proposed_targets:
            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT l.print_id
                  FROM external_catalog_print_links l
                  JOIN external_catalog_products ecp ON ecp.id=l.external_product_id
                  WHERE l.print_id = ANY(%s)
                    AND ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                    AND l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact')
                  GROUP BY l.print_id
                  HAVING count(DISTINCT l.external_product_id) > 1
                ) q
                """,
                (proposed_targets, plan["game_id"]),
            )
            ambiguous_targets_after = int(cur.fetchone()[0])
        else:
            ambiguous_targets_after = 0

    if exact_pairs_after != plan["certified_pairs"]:
        raise AssertionError({"reason": "not all certified pairs exact after apply", "expected": plan["certified_pairs"], "actual": exact_pairs_after})
    if ambiguous_targets_after != 0:
        raise AssertionError({"reason": "ambiguous targets after apply", "count": ambiguous_targets_after})
    return {
        "affected_rows": affected,
        "exact_pairs_after": exact_pairs_after,
        "ambiguous_targets_after": ambiguous_targets_after,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Safely apply certified YGO Cardmarket/Konami V3 exact links.")
    ap.add_argument("certificate", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    cert = load_cert(args.certificate)
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        plan = build_plan(conn, cert)
        result = {
            "mode": "apply" if args.apply else "dry_run",
            "game": "yugioh",
            "mapping_method": MAPPING_METHOD,
            **{k: v for k, v in plan.items() if k != "missing_pairs"},
        }
        if args.apply:
            if not plan["write_ready"]:
                raise SystemExit("REFUSED: blockers present")
            result.update(apply_plan(conn, cert, plan))
            conn.commit()
        else:
            conn.rollback()
        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        print("YGO_KONAMI_BRIDGE_V3_APPLY=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        if args.report:
            args.report.write_text(rendered + "\n", encoding="utf-8")
        return 0 if result.get("write_ready") else 2
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
