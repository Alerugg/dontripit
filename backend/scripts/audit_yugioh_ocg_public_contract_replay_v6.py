from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
METHODS = (
    "cardmarket_ocg_certified_public_super_secret_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v2",
)
EXPECTED = {
    "cardmarket_ocg_certified_public_super_secret_contract_v1": 20,
    "cardmarket_ocg_certified_public_version_contract_v1": 41,
    "cardmarket_ocg_certified_public_version_contract_v2": 40,
}


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def main() -> int:
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_public_contract_replay_v6",
    )
    conn.set_session(readonly=True, autocommit=False)
    failures: list[dict[str, Any]] = []
    derived_pairs: list[dict[str, Any]] = []

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game missing")
            gid = int(game["id"])

            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products "
                "WHERE source='cardmarket' AND game_id=%s AND product_group='single'",
                (gid,),
            )
            capture = cur.fetchone()["capture"]

            cur.execute(
                """
                SELECT l.mapping_method,l.external_product_id,l.print_id,l.evidence,
                       e.external_id id_product,e.metacard_external_id,e.expansion_external_id,
                       p.card_id,c.name card_name,s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=ANY(%s)
                ORDER BY l.mapping_method,s.code,e.metacard_external_id,e.external_id::bigint
                """,
                (gid, list(ACCEPTED), list(METHODS)),
            )
            historical = [dict(row) for row in cur.fetchall()]

            by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in historical:
                by_method[str(row["mapping_method"])].append(row)

            method_reports: dict[str, Any] = {}
            for method in METHODS:
                items = by_method.get(method, [])
                if len(items) != EXPECTED[method]:
                    failures.append({
                        "reason": "historical_count_drift",
                        "method": method,
                        "expected": EXPECTED[method],
                        "actual": len(items),
                    })

                groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
                for row in items:
                    groups[(
                        str(row.get("set_code") or "").upper(),
                        str(row.get("expansion_external_id") or ""),
                        str(row.get("metacard_external_id") or ""),
                        int(row["card_id"]),
                    )].append(row)

                method_derived = 0
                for (set_code, expansion_id, metacard_id, card_id), target in sorted(groups.items()):
                    cur.execute(
                        """
                        SELECT e.id external_product_id,e.external_id id_product,e.name
                        FROM external_catalog_products e
                        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                          AND e.expansion_external_id=%s AND e.metacard_external_id=%s
                          AND e.last_seen_at=%s
                        ORDER BY e.external_id::bigint
                        """,
                        (gid, expansion_id, metacard_id, capture),
                    )
                    products = [dict(row) for row in cur.fetchall()]

                    cur.execute(
                        """
                        SELECT p.id print_id,p.collector_number,p.rarity,p.variant
                        FROM prints p
                        JOIN cards c ON c.id=p.card_id
                        JOIN sets s ON s.id=p.set_id
                        WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                          AND lower(coalesce(p.language,''))='ja' AND p.card_id=%s
                        ORDER BY p.collector_number,p.id
                        """,
                        (gid, set_code, card_id),
                    )
                    prints = [dict(row) for row in cur.fetchall()]

                    if len(products) != len(target) or len(prints) != len(target):
                        failures.append({
                            "reason": "current_group_arity_drift",
                            "method": method,
                            "set_code": set_code,
                            "idExpansion": expansion_id,
                            "idMetacard": metacard_id,
                            "products": len(products),
                            "prints": len(prints),
                            "historical_rows": len(target),
                        })
                        continue

                    specs: dict[int, str] = {}
                    historical_by_ordinal: dict[int, tuple[str, int]] = {}
                    for row in target:
                        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
                        ordinal_raw = evidence.get("product_ordinal")
                        rarity_raw = evidence.get("contract_rarity") or evidence.get("canonical_rarity")
                        try:
                            ordinal = int(ordinal_raw)
                        except (TypeError, ValueError):
                            failures.append({
                                "reason": "missing_or_invalid_contract_ordinal",
                                "method": method,
                                "idProduct": str(row.get("id_product") or ""),
                            })
                            continue
                        rarity = _norm(rarity_raw)
                        if not rarity:
                            failures.append({
                                "reason": "missing_contract_rarity",
                                "method": method,
                                "idProduct": str(row.get("id_product") or ""),
                            })
                            continue
                        if ordinal in specs and specs[ordinal] != rarity:
                            failures.append({
                                "reason": "contract_ordinal_collision",
                                "method": method,
                                "ordinal": ordinal,
                            })
                            continue
                        specs[ordinal] = rarity
                        historical_by_ordinal[ordinal] = (
                            str(row.get("id_product") or ""),
                            int(row["print_id"]),
                        )

                    products_by_ordinal = {idx + 1: product for idx, product in enumerate(products)}
                    prints_by_rarity: dict[str, list[dict[str, Any]]] = defaultdict(list)
                    for physical in prints:
                        prints_by_rarity[_norm(physical.get("rarity"))].append(physical)

                    if set(specs) != set(products_by_ordinal):
                        failures.append({
                            "reason": "contract_ordinal_surface_drift",
                            "method": method,
                            "set_code": set_code,
                            "idMetacard": metacard_id,
                            "contract_ordinals": sorted(specs),
                            "current_ordinals": sorted(products_by_ordinal),
                        })
                        continue

                    group_pairs: list[dict[str, Any]] = []
                    for ordinal in sorted(specs):
                        rarity = specs[ordinal]
                        candidates = prints_by_rarity.get(rarity, [])
                        if len(candidates) != 1:
                            failures.append({
                                "reason": "rarity_does_not_resolve_unique_physical_print",
                                "method": method,
                                "set_code": set_code,
                                "idMetacard": metacard_id,
                                "ordinal": ordinal,
                                "contract_rarity": rarity,
                                "candidate_count": len(candidates),
                            })
                            continue

                        product = products_by_ordinal[ordinal]
                        physical = candidates[0]
                        derived = {
                            "method": method,
                            "set_code": set_code,
                            "idExpansion": expansion_id,
                            "idMetacard": metacard_id,
                            "card_id": card_id,
                            "card_name": str(target[0].get("card_name") or ""),
                            "ordinal": ordinal,
                            "contract_rarity": rarity,
                            "idProduct": str(product["id_product"]),
                            "external_product_id": int(product["external_product_id"]),
                            "print_id": int(physical["print_id"]),
                            "collector_number": str(physical.get("collector_number") or ""),
                            "physical_rarity": _norm(physical.get("rarity")),
                            "historical_pair_used_for_derivation": False,
                        }
                        group_pairs.append(derived)

                        expected_pair = historical_by_ordinal.get(ordinal)
                        actual_pair = (derived["idProduct"], derived["print_id"])
                        if expected_pair != actual_pair:
                            failures.append({
                                "reason": "independent_replay_differs_from_historical_pair",
                                "method": method,
                                "set_code": set_code,
                                "idMetacard": metacard_id,
                                "ordinal": ordinal,
                                "derived": actual_pair,
                                "historical": expected_pair,
                            })

                    if len(group_pairs) == len(target):
                        derived_pairs.extend(group_pairs)
                        method_derived += len(group_pairs)

                method_reports[method] = {
                    "historical_rows": len(items),
                    "groups": len(groups),
                    "independently_replayed_rows": method_derived,
                }

            product_to_prints: dict[str, set[int]] = defaultdict(set)
            print_to_products: dict[int, set[str]] = defaultdict(set)
            for pair in derived_pairs:
                product_to_prints[pair["idProduct"]].add(pair["print_id"])
                print_to_products[pair["print_id"]].add(pair["idProduct"])

            product_conflicts = {
                product: sorted(print_ids)
                for product, print_ids in product_to_prints.items()
                if len(print_ids) != 1
            }
            print_conflicts = {
                str(print_id): sorted(products)
                for print_id, products in print_to_products.items()
                if len(products) != 1
            }
            if product_conflicts:
                failures.append({"reason": "global_product_to_print_conflicts", "items": product_conflicts})
            if print_conflicts:
                failures.append({"reason": "global_print_to_product_conflicts", "items": print_conflicts})

            expected_total = sum(EXPECTED.values())
            if len(derived_pairs) != expected_total:
                failures.append({
                    "reason": "replay_total_mismatch",
                    "expected": expected_total,
                    "actual": len(derived_pairs),
                })
            if len(product_to_prints) != expected_total:
                failures.append({
                    "reason": "replay_product_uniqueness_mismatch",
                    "expected": expected_total,
                    "actual": len(product_to_prints),
                })
            if len(print_to_products) != expected_total:
                failures.append({
                    "reason": "replay_print_uniqueness_mismatch",
                    "expected": expected_total,
                    "actual": len(print_to_products),
                })

            report = {
                "status": "PASS" if not failures else "FAIL",
                "mode": "read_only",
                "production_writes": 0,
                "game": GAME,
                "cardmarket_capture": str(capture),
                "contract": {
                    "purpose": "independently reconstruct public OCG ordinal-to-rarity mappings from current Cardmarket products and current JA physical prints",
                    "historical_pair_used_for_derivation": False,
                    "historical_pair_compared_only_after_derivation": True,
                    "writes_allowed": False,
                },
                "expected_rows": expected_total,
                "independently_replayed_rows": len(derived_pairs),
                "unique_idProducts": len(product_to_prints),
                "unique_prints": len(print_to_products),
                "product_to_print_conflicts": len(product_conflicts),
                "print_to_product_conflicts": len(print_conflicts),
                "methods": method_reports,
                "failures": failures,
                "derived_pairs": derived_pairs,
            }

            output = Path(os.getenv(
                "YGO_OCG_PUBLIC_CONTRACT_REPLAY_V6_OUTPUT",
                "/tmp/yugioh-ocg-public-contract-replay-v6.json",
            ))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

            if failures:
                print(json.dumps({"status": "FAIL", "failures": failures[:20]}, indent=2, default=str))
                conn.rollback()
                return 1

            print(json.dumps({
                "status": "PASS",
                "replayed": len(derived_pairs),
                "unique_idProducts": len(product_to_prints),
                "unique_prints": len(print_to_products),
                "production_writes": 0,
            }, indent=2))
            conn.rollback()
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
