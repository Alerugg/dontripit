#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values


MAPPING_METHOD = "ygo_konami_composition_bridge_v4_exact"
ACCEPTED_STATUSES = ("accepted", "mapped", "exact")


def load_cert(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "read_only" or payload.get("game") != "yugioh":
        raise SystemExit("REFUSED: expected read-only YGO V4 product certification")
    if payload.get("resolver") != "cardmarket_konami_bridge_v4_deterministic":
        raise SystemExit("REFUSED: wrong V4 product resolver certificate")
    summary = payload.get("summary") or {}
    blockers: list[str] = []
    if int(summary.get("production_writes", -1)) != 0:
        blockers.append("certificate is not proven read-only")
    if summary.get("certification_ready") is not True:
        blockers.append("certificate certification_ready=false")
    if int(summary.get("masked_backtest_predictable") or 0) < 100:
        blockers.append("insufficient masked backtest coverage")
    if int(summary.get("masked_backtest_wrong") or 0) != 0:
        blockers.append("masked backtest contains wrong predictions")
    if float(summary.get("masked_backtest_precision") or 0.0) != 1.0:
        blockers.append("masked backtest precision is not 1.0")
    candidates = payload.get("safe_candidates") or []
    if len(candidates) != int(summary.get("safe_candidate_external_products") or 0):
        blockers.append("safe candidate count mismatch")
    expected_pairs = sum(len(row.get("target_print_ids") or []) for row in candidates)
    if expected_pairs != int(summary.get("safe_candidate_link_rows") or 0):
        blockers.append("safe candidate link-row count mismatch")
    if not candidates:
        blockers.append("certificate contains no safe candidates")
    if blockers:
        raise SystemExit("REFUSED: " + "; ".join(blockers))
    return payload


def build_plan(conn, cert: dict) -> dict:
    candidates = cert.get("safe_candidates") or []
    proposed_by_external = {
        int(row["external_product_id"]): {int(pid) for pid in row.get("target_print_ids") or []}
        for row in candidates
    }
    proposed_pairs = {
        (int(row["external_product_id"]), int(pid))
        for row in candidates
        for pid in row.get("target_print_ids") or []
    }
    external_ids = sorted(proposed_by_external)
    target_ids = sorted({print_id for _, print_id in proposed_pairs})

    blockers: list[dict] = []
    if len(external_ids) != len(candidates):
        blockers.append({"type": "duplicate_external_product_in_certificate"})
    if len(target_ids) != len(proposed_pairs):
        blockers.append({"type": "duplicate_target_print_in_certificate"})
    for row in candidates:
        targets = row.get("target_print_ids") or []
        if len(targets) != 1:
            blockers.append({
                "type": "non_singleton_target_set_refused",
                "external_product_id": int(row["external_product_id"]),
                "target_count": len(targets),
            })
        if row.get("method") != "single_product_single_rarity":
            blockers.append({
                "type": "non_certified_method_refused",
                "external_product_id": int(row["external_product_id"]),
                "method": row.get("method"),
            })

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM games WHERE slug='yugioh'")
        game_row = cur.fetchone()
        if not game_row:
            raise SystemExit("REFUSED: yugioh game not found")
        game_id = int(game_row["id"])

        if external_ids:
            cur.execute(
                """
                SELECT id, external_id, game_id, source, product_group,
                       expansion_external_id, name
                FROM external_catalog_products
                WHERE id = ANY(%s)
                """,
                (external_ids,),
            )
            products = {int(row["id"]): dict(row) for row in cur.fetchall()}
        else:
            products = {}

        if target_ids:
            cur.execute(
                """
                SELECT p.id AS print_id, c.game_id, p.rarity, p.collector_number,
                       p.set_id, c.id AS card_id
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE p.id = ANY(%s)
                """,
                (target_ids,),
            )
            prints = {int(row["print_id"]): dict(row) for row in cur.fetchall()}
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
            links_for_external = [dict(row) for row in cur.fetchall()]
        else:
            links_for_external = []

        if target_ids:
            cur.execute(
                """
                SELECT l.external_product_id, l.print_id, l.link_status,
                       l.confidence, l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE l.print_id = ANY(%s)
                  AND e.source='cardmarket'
                  AND e.game_id=%s
                  AND e.product_group='single'
                  AND l.confidence='exact'
                  AND l.link_status IN ('accepted','mapped','exact')
                """,
                (target_ids, game_id),
            )
            exact_links_for_target = [dict(row) for row in cur.fetchall()]
        else:
            exact_links_for_target = []

    candidate_by_external = {int(row["external_product_id"]): row for row in candidates}
    for external_product_id in external_ids:
        product = products.get(external_product_id)
        candidate = candidate_by_external[external_product_id]
        if product is None:
            blockers.append({"type": "missing_external_product", "external_product_id": external_product_id})
            continue
        if (
            int(product["game_id"]) != game_id
            or product["source"] != "cardmarket"
            or product["product_group"] != "single"
        ):
            blockers.append({"type": "external_product_identity_mismatch", "external_product_id": external_product_id})
        if str(product.get("external_id")) != str(candidate.get("idProduct")):
            blockers.append({"type": "external_id_changed", "external_product_id": external_product_id})
        if str(product.get("expansion_external_id") or "") != str(candidate.get("expansion") or ""):
            blockers.append({"type": "expansion_changed", "external_product_id": external_product_id})

    for print_id in target_ids:
        print_row = prints.get(print_id)
        if not print_row or int(print_row["game_id"]) != game_id:
            blockers.append({"type": "missing_or_cross_game_print", "print_id": print_id})

    existing_same_pairs: set[tuple[int, int]] = set()
    for row in links_for_external:
        pair = (int(row["external_product_id"]), int(row["print_id"]))
        accepted_exact = row["confidence"] == "exact" and row["link_status"] in ACCEPTED_STATUSES
        if pair in proposed_pairs and accepted_exact:
            existing_same_pairs.add(pair)
            continue
        blockers.append({
            "type": "external_product_has_unexpected_existing_link",
            "external_product_id": pair[0],
            "print_id": pair[1],
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
        raise SystemExit("REFUSED: V4 preflight blockers present")

    candidates = cert.get("safe_candidates") or []
    candidate_by_external = {int(row["external_product_id"]): row for row in candidates}
    rows = []
    for external_product_id, print_id in plan["missing_pairs"]:
        candidate = candidate_by_external[external_product_id]
        exp = candidate.get("expansion_evidence") or {}
        evidence = {
            "source": "cardmarket",
            "resolver": "cardmarket_konami_bridge_v4_deterministic",
            "identity_basis": [
                "fixed_profile_card_name_composition_expansion_certificate",
                "gold_backtest_228_of_228_expansions_zero_wrong",
                "masked_product_backtest_15898_of_15898_zero_wrong",
                "single_cardmarket_product_for_normalized_name_group",
                "single_official_rarity_for_name_inside_certified_release",
                "single_canonical_print_target",
                "target_not_claimed_by_other_exact_cardmarket_product",
            ],
            "cardmarket_idProduct": str(candidate.get("idProduct")),
            "cardmarket_expansion_id": str(candidate.get("expansion")),
            "konami_pid": str(candidate.get("konami_pid")),
            "normalized_rarity": str(candidate.get("rarity")),
            "deterministic_method": str(candidate.get("method")),
            "expansion_profile": {
                "overlap": exp.get("overlap"),
                "cm_coverage": exp.get("cm_coverage"),
                "f1": exp.get("f1"),
                "margin_f1": exp.get("margin_f1"),
            },
        }
        rows.append((
            external_product_id,
            print_id,
            MAPPING_METHOD,
            "exact",
            "accepted",
            False,
            Json(evidence),
        ))

    with conn.cursor() as cur:
        if rows:
            execute_values(
                cur,
                """
                INSERT INTO external_catalog_print_links
                  (external_product_id, print_id, mapping_method, confidence,
                   link_status, reviewed, evidence)
                VALUES %s
                ON CONFLICT (external_product_id, print_id) DO NOTHING
                """,
                rows,
                page_size=500,
            )
            affected_rows = int(cur.rowcount or 0)
        else:
            affected_rows = 0

        proposed_pairs = sorted({
            (int(row["external_product_id"]), int(pid))
            for row in candidates
            for pid in row.get("target_print_ids") or []
        })
        external_array = [pair[0] for pair in proposed_pairs]
        print_array = [pair[1] for pair in proposed_pairs]
        if proposed_pairs:
            cur.execute(
                """
                SELECT count(*)
                FROM external_catalog_print_links l
                WHERE (l.external_product_id, l.print_id) IN (
                  SELECT x.external_product_id, x.print_id
                  FROM unnest(%s::bigint[], %s::bigint[]) AS x(external_product_id, print_id)
                )
                  AND l.confidence='exact'
                  AND l.link_status IN ('accepted','mapped','exact')
                """,
                (external_array, print_array),
            )
            exact_pairs_after = int(cur.fetchone()[0])
        else:
            exact_pairs_after = 0

        target_ids = sorted(set(print_array))
        if target_ids:
            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT l.print_id
                  FROM external_catalog_print_links l
                  JOIN external_catalog_products e ON e.id=l.external_product_id
                  WHERE l.print_id = ANY(%s)
                    AND e.source='cardmarket'
                    AND e.game_id=%s
                    AND e.product_group='single'
                    AND l.confidence='exact'
                    AND l.link_status IN ('accepted','mapped','exact')
                  GROUP BY l.print_id
                  HAVING count(DISTINCT l.external_product_id) > 1
                ) q
                """,
                (target_ids, plan["game_id"]),
            )
            ambiguous_targets_after = int(cur.fetchone()[0])
        else:
            ambiguous_targets_after = 0

    if exact_pairs_after != plan["certified_pairs"]:
        raise AssertionError({
            "reason": "not all certified V4 pairs exact after apply",
            "expected": plan["certified_pairs"],
            "actual": exact_pairs_after,
        })
    if ambiguous_targets_after != 0:
        raise AssertionError({"reason": "ambiguous V4 targets after apply", "count": ambiguous_targets_after})

    return {
        "affected_rows": affected_rows,
        "exact_pairs_after": exact_pairs_after,
        "ambiguous_targets_after": ambiguous_targets_after,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Safely apply certified YGO Cardmarket/Konami V4 exact links.")
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
            **{key: value for key, value in plan.items() if key != "missing_pairs"},
        }
        if args.apply:
            if not plan["write_ready"]:
                raise SystemExit("REFUSED: V4 blockers present")
            result.update(apply_plan(conn, cert, plan))
            conn.commit()
        else:
            conn.rollback()

        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        print("YGO_KONAMI_BRIDGE_V4_APPLY=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
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
