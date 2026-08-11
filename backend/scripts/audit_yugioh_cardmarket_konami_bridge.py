#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only YGO audit: derive Cardmarket expansion -> Konami product bridges "
            "only from already accepted exact print links, then test exact normalized-name "
            "resolution inside the bridged official product."
        )
    )
    parser.add_argument("--min-samples", type=int, default=3)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            row = cur.fetchone()
            if not row:
                raise AssertionError("yugioh game row missing")
            game_id = int(row["id"])

            cur.execute(
                """
                SELECT ecp.id AS external_product_id,
                       ecp.external_id,
                       ecp.expansion_external_id,
                       ecp.name,
                       ecp.website_path,
                       ecl.print_id
                FROM external_catalog_products ecp
                LEFT JOIN external_catalog_print_links ecl
                  ON ecl.external_product_id=ecp.id
                 AND ecl.link_status IN ('accepted','mapped')
                 AND ecl.confidence='exact'
                WHERE ecp.source='cardmarket'
                  AND ecp.game_id=%s
                  AND ecp.product_group='single'
                """,
                (game_id,),
            )
            products = list(cur.fetchall())

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid, cr.name AS konami_product_name
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE cr.game_id=%s AND cr.source='konami_neuron'
                """,
                (game_id,),
            )
            release_rows = list(cur.fetchall())

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid,
                       c.name AS card_name, p.collector_number, p.rarity
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE cr.game_id=%s AND cr.source='konami_neuron'
                """,
                (game_id,),
            )
            official_print_rows = list(cur.fetchall())

        print_to_pids: dict[int, set[str]] = defaultdict(set)
        pid_names: dict[str, str] = {}
        for row in release_rows:
            pid = str(row["konami_pid"])
            print_to_pids[int(row["print_id"])].add(pid)
            pid_names[pid] = str(row["konami_product_name"] or "")

        expansion_mapped_prints: dict[str, set[int]] = defaultdict(set)
        all_external_by_expansion: dict[str, list[dict]] = defaultdict(list)
        accepted_external_ids: set[int] = set()
        accepted_print_ids: set[int] = set()
        for row in products:
            expansion_id = str(row["expansion_external_id"] or "")
            if not expansion_id:
                continue
            data = dict(row)
            all_external_by_expansion[expansion_id].append(data)
            if row["print_id"] is not None:
                print_id = int(row["print_id"])
                expansion_mapped_prints[expansion_id].add(print_id)
                accepted_external_ids.add(int(row["external_product_id"]))
                accepted_print_ids.add(print_id)

        bridge_rows = []
        bridge_by_expansion: dict[str, str] = {}
        rejected_bridge_counts = defaultdict(int)
        for expansion_id, print_ids in sorted(expansion_mapped_prints.items()):
            if len(print_ids) < args.min_samples:
                rejected_bridge_counts["insufficient_samples"] += 1
                continue
            pid_sets = [print_to_pids.get(print_id, set()) for print_id in sorted(print_ids)]
            if any(not values for values in pid_sets):
                rejected_bridge_counts["mapped_print_without_konami_release"] += 1
                continue
            intersection = set.intersection(*pid_sets)
            if len(intersection) != 1:
                rejected_bridge_counts["non_unique_release_intersection"] += 1
                continue
            pid = next(iter(intersection))
            bridge_by_expansion[expansion_id] = pid
            bridge_rows.append(
                {
                    "cardmarket_expansion_id": expansion_id,
                    "konami_pid": pid,
                    "konami_product_name": pid_names.get(pid),
                    "evidence_print_count": len(print_ids),
                    "cardmarket_product_count": len(all_external_by_expansion.get(expansion_id, [])),
                    "identity_basis": "intersection_of_all_existing_exact_cardmarket_print_links_with_official_konami_memberships",
                }
            )

        pid_name_index: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        print_meta: dict[int, dict] = {}
        for row in official_print_rows:
            pid = str(row["konami_pid"])
            print_id = int(row["print_id"])
            key = norm(row["card_name"])
            if key:
                pid_name_index[pid][key].add(print_id)
            print_meta[print_id] = {
                "card_name": row["card_name"],
                "collector_number": row["collector_number"],
                "rarity": row["rarity"],
            }

        candidates = []
        unresolved = defaultdict(int)
        target_to_external: dict[int, set[int]] = defaultdict(set)
        for expansion_id, pid in sorted(bridge_by_expansion.items()):
            for row in all_external_by_expansion.get(expansion_id, []):
                external_pk = int(row["external_product_id"])
                if external_pk in accepted_external_ids:
                    continue
                key = norm(row["name"])
                if not key:
                    unresolved["empty_name"] += 1
                    continue
                print_ids = pid_name_index[pid].get(key, set())
                if len(print_ids) != 1:
                    unresolved["no_exact_name_in_release" if not print_ids else "multiple_exact_prints_in_release"] += 1
                    continue
                print_id = next(iter(print_ids))
                if print_id in accepted_print_ids:
                    unresolved["target_already_has_exact_cardmarket_link"] += 1
                    continue
                target_to_external[print_id].add(external_pk)
                candidates.append(
                    {
                        "external_product_id": external_pk,
                        "cardmarket_id_product": str(row["external_id"]),
                        "cardmarket_name": row["name"],
                        "cardmarket_expansion_id": expansion_id,
                        "konami_pid": pid,
                        "print_id": print_id,
                        **print_meta[print_id],
                        "website_path": row["website_path"],
                        "identity_basis": [
                            "existing_exact_links_release_intersection",
                            "exact_normalized_card_name_inside_official_konami_product",
                            "unique_canonical_print_inside_release",
                            "target_has_no_existing_exact_cardmarket_link",
                        ],
                    }
                )

        duplicate_target_conflicts = {
            str(print_id): sorted(externals)
            for print_id, externals in target_to_external.items()
            if len(externals) > 1
        }
        safe_candidates = [
            row for row in candidates if str(row["print_id"]) not in duplicate_target_conflicts
        ]

        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "min_samples": args.min_samples,
            "summary": {
                "cardmarket_single_rows": len(products),
                "accepted_exact_external_products": len(accepted_external_ids),
                "accepted_exact_target_prints": len(accepted_print_ids),
                "konami_memberships": len(release_rows),
                "cardmarket_expansions_with_existing_exact_links": len(expansion_mapped_prints),
                "bridged_expansions": len(bridge_rows),
                "candidate_links_before_target_guard": len(candidates),
                "safe_candidate_links": len(safe_candidates),
                "duplicate_target_conflicts": len(duplicate_target_conflicts),
                "rejected_bridge_counts": dict(sorted(rejected_bridge_counts.items())),
                "unresolved_candidate_counts": dict(sorted(unresolved.items())),
            },
            "bridges": bridge_rows,
            "safe_candidates": safe_candidates,
            "duplicate_target_conflicts": duplicate_target_conflicts,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        print(rendered)
        if args.report:
            args.report.write_text(rendered + "\n", encoding="utf-8")
        conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
