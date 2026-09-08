#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only YGO Cardmarket -> Konami consensus bridge V2 audit.")
    parser.add_argument("--min-linked", type=int, default=5)
    parser.add_argument("--min-informative", type=int, default=5)
    parser.add_argument("--min-support", type=int, default=5)
    parser.add_argument("--min-evidence-coverage", type=float, default=0.25)
    parser.add_argument("--min-purity", type=float, default=0.95)
    parser.add_argument("--min-margin", type=int, default=3)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sample-limit", type=int, default=50)
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            game_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                SELECT id AS external_product_id, external_id, expansion_external_id,
                       name, website_path, raw_json
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                """,
                (game_id,),
            )
            products = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id, l.print_id, l.link_status, l.confidence,
                       l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products ecp ON ecp.id=l.external_product_id
                WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                """,
                (game_id,),
            )
            all_links = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid, cr.name AS konami_product_name,
                       c.name AS card_name, p.collector_number, p.rarity, p.variant,
                       s.code AS set_code
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE cr.game_id=%s AND cr.source='konami_neuron'
                """,
                (game_id,),
            )
            official_rows = [dict(row) for row in cur.fetchall()]

        product_by_id = {int(row["external_product_id"]): row for row in products}
        products_by_expansion: dict[str, list[dict]] = defaultdict(list)
        for row in products:
            expansion_id = str(row.get("expansion_external_id") or "")
            if expansion_id:
                products_by_expansion[expansion_id].append(row)

        exact_links_by_external: dict[int, set[int]] = defaultdict(set)
        all_links_by_external: dict[int, list[dict]] = defaultdict(list)
        exact_external_ids: set[int] = set()
        exact_print_ids: set[int] = set()
        exact_prints_by_expansion: dict[str, set[int]] = defaultdict(set)
        for row in all_links:
            external_pk = int(row["external_product_id"])
            all_links_by_external[external_pk].append(row)
            if row["confidence"] == "exact" and row["link_status"] in ("accepted", "mapped"):
                print_id = int(row["print_id"])
                exact_links_by_external[external_pk].add(print_id)
                exact_external_ids.add(external_pk)
                exact_print_ids.add(print_id)
                product = product_by_id.get(external_pk)
                expansion_id = str(product.get("expansion_external_id") or "") if product else ""
                if expansion_id:
                    exact_prints_by_expansion[expansion_id].add(print_id)

        print_to_pids: dict[int, set[str]] = defaultdict(set)
        pid_names: dict[str, str] = {}
        pid_name_index: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        print_meta: dict[int, dict] = {}
        for row in official_rows:
            print_id = int(row["print_id"])
            pid = str(row["konami_pid"])
            print_to_pids[print_id].add(pid)
            pid_names[pid] = str(row.get("konami_product_name") or "")
            key = norm(row.get("card_name"))
            if key:
                pid_name_index[pid][key].add(print_id)
            print_meta[print_id] = {
                "card_name": row.get("card_name"),
                "collector_number": row.get("collector_number"),
                "rarity": row.get("rarity"),
                "variant": row.get("variant"),
                "set_code": row.get("set_code"),
            }

        bridges: list[dict] = []
        bridge_by_expansion: dict[str, str] = {}
        rejected = Counter()
        for expansion_id, linked_prints in sorted(exact_prints_by_expansion.items()):
            linked_count = len(linked_prints)
            if linked_count < args.min_linked:
                rejected["insufficient_linked_prints"] += 1
                continue

            informative = [print_id for print_id in linked_prints if print_to_pids.get(print_id)]
            informative_count = len(informative)
            if informative_count < args.min_informative:
                rejected["insufficient_informative_prints"] += 1
                continue

            evidence_coverage = informative_count / linked_count if linked_count else 0.0
            if evidence_coverage < args.min_evidence_coverage:
                rejected["insufficient_evidence_coverage"] += 1
                continue

            support = Counter()
            for print_id in informative:
                for pid in print_to_pids[print_id]:
                    support[pid] += 1
            ranked = support.most_common()
            if not ranked:
                rejected["no_release_support"] += 1
                continue
            winner_pid, winner_support = ranked[0]
            runner_support = ranked[1][1] if len(ranked) > 1 else 0
            if len(ranked) > 1 and ranked[1][1] == winner_support:
                rejected["tied_release_support"] += 1
                continue
            purity = winner_support / informative_count
            margin = winner_support - runner_support
            if winner_support < args.min_support:
                rejected["insufficient_winner_support"] += 1
                continue
            if purity < args.min_purity:
                rejected["insufficient_release_purity"] += 1
                continue
            if margin < args.min_margin:
                rejected["insufficient_runner_up_margin"] += 1
                continue

            bridge_by_expansion[expansion_id] = winner_pid
            bridges.append(
                {
                    "cardmarket_expansion_id": expansion_id,
                    "konami_pid": winner_pid,
                    "konami_product_name": pid_names.get(winner_pid),
                    "linked_exact_prints": linked_count,
                    "informative_exact_prints": informative_count,
                    "winner_support": winner_support,
                    "runner_up_support": runner_support,
                    "purity": round(purity, 6),
                    "evidence_coverage": round(evidence_coverage, 6),
                    "support_top5": ranked[:5],
                }
            )

        validation = Counter()
        validation_samples: list[dict] = []
        for expansion_id, pid in bridge_by_expansion.items():
            for product in products_by_expansion.get(expansion_id, []):
                external_pk = int(product["external_product_id"])
                existing = exact_links_by_external.get(external_pk, set())
                if len(existing) != 1:
                    continue
                key = norm(product.get("name"))
                predicted = pid_name_index[pid].get(key, set()) if key else set()
                if len(predicted) != 1:
                    continue
                validation["comparable"] += 1
                predicted_print = next(iter(predicted))
                actual_print = next(iter(existing))
                if predicted_print == actual_print:
                    validation["correct"] += 1
                else:
                    validation["wrong"] += 1
                    if len(validation_samples) < args.sample_limit:
                        validation_samples.append(
                            {
                                "expansion": expansion_id,
                                "konami_pid": pid,
                                "external_product_id": external_pk,
                                "cardmarket_id_product": str(product["external_id"]),
                                "name": product["name"],
                                "predicted_print_id": predicted_print,
                                "actual_print_id": actual_print,
                            }
                        )

        candidates: list[dict] = []
        unresolved = Counter()
        target_to_external: dict[int, set[int]] = defaultdict(set)
        for expansion_id, pid in bridge_by_expansion.items():
            for product in products_by_expansion.get(expansion_id, []):
                external_pk = int(product["external_product_id"])
                if external_pk in exact_external_ids:
                    continue
                if all_links_by_external.get(external_pk):
                    unresolved["external_product_has_existing_nonexact_or_conflicting_link"] += 1
                    continue
                key = norm(product.get("name"))
                if not key:
                    unresolved["empty_name"] += 1
                    continue
                print_ids = pid_name_index[pid].get(key, set())
                if not print_ids:
                    unresolved["no_exact_name_in_consensus_release"] += 1
                    continue
                if len(print_ids) != 1:
                    unresolved["multiple_exact_prints_in_consensus_release"] += 1
                    continue
                print_id = next(iter(print_ids))
                if print_id in exact_print_ids:
                    unresolved["target_already_has_exact_cardmarket_link"] += 1
                    continue
                target_to_external[print_id].add(external_pk)
                candidates.append(
                    {
                        "external_product_id": external_pk,
                        "cardmarket_id_product": str(product["external_id"]),
                        "cardmarket_name": product["name"],
                        "cardmarket_expansion_id": expansion_id,
                        "konami_pid": pid,
                        "konami_product_name": pid_names.get(pid),
                        "print_id": print_id,
                        **print_meta.get(print_id, {}),
                        "website_path": product.get("website_path"),
                    }
                )

        duplicate_target_conflicts = {
            print_id: sorted(externals)
            for print_id, externals in target_to_external.items()
            if len(externals) > 1
        }
        safe_candidates = [row for row in candidates if row["print_id"] not in duplicate_target_conflicts]

        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "resolver": "cardmarket_konami_consensus_bridge_v2",
            "thresholds": {
                "min_linked": args.min_linked,
                "min_informative": args.min_informative,
                "min_support": args.min_support,
                "min_evidence_coverage": args.min_evidence_coverage,
                "min_purity": args.min_purity,
                "min_margin": args.min_margin,
            },
            "summary": {
                "cardmarket_single_rows": len(products),
                "existing_exact_external_products": len(exact_external_ids),
                "existing_exact_target_prints": len(exact_print_ids),
                "konami_memberships": len(official_rows),
                "expansions_with_existing_exact_links": len(exact_prints_by_expansion),
                "certified_consensus_expansions": len(bridges),
                "rejected_expansion_counts": dict(sorted(rejected.items())),
                "validation_comparable": validation["comparable"],
                "validation_correct": validation["correct"],
                "validation_wrong": validation["wrong"],
                "validation_precision": round(validation["correct"] / validation["comparable"], 8) if validation["comparable"] else None,
                "candidate_links_before_target_guard": len(candidates),
                "duplicate_target_conflicts": len(duplicate_target_conflicts),
                "safe_candidate_links": len(safe_candidates),
                "unresolved_candidate_counts": dict(sorted(unresolved.items())),
                "write_ready": validation["wrong"] == 0 and len(duplicate_target_conflicts) == 0,
            },
            "bridges": bridges,
            "safe_candidates": safe_candidates,
            "duplicate_target_conflicts": {str(k): v for k, v in duplicate_target_conflicts.items()},
            "validation_wrong_samples": validation_samples,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.report:
            args.report.write_text(rendered + "\n", encoding="utf-8")
        print("YGO_KONAMI_BRIDGE_V2=" + json.dumps(payload["summary"], ensure_ascii=False, separators=(",", ":")))
        conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
