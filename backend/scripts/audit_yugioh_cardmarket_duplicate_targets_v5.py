#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Read-only diagnostic of YGO V4 duplicate target claims for V5 design."
    )
    ap.add_argument("product_audit", type=Path)
    ap.add_argument("expansion_cert", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--sample-limit", type=int, default=200)
    args = ap.parse_args()

    audit = json.loads(args.product_audit.read_text(encoding="utf-8"))
    cert = json.loads(args.expansion_cert.read_text(encoding="utf-8"))
    if audit.get("mode") != "read_only" or audit.get("game") != "yugioh":
        raise SystemExit("Expected read-only YGO product audit")
    if cert.get("mode") != "read_only_certification" or cert.get("game") != "yugioh":
        raise SystemExit("Expected read-only YGO expansion certification")
    if int((audit.get("summary") or {}).get("production_writes", -1)) != 0:
        raise SystemExit("Product audit is not proven read-only")
    if int((cert.get("summary") or {}).get("production_writes", -1)) != 0:
        raise SystemExit("Expansion cert is not proven read-only")

    duplicate_targets = {
        int(print_id): [int(product_id) for product_id in product_ids]
        for print_id, product_ids in (audit.get("duplicate_targets") or {}).items()
    }
    if not duplicate_targets:
        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "summary": {"duplicate_targets": 0, "production_writes": 0},
            "patterns": [],
            "samples": [],
        }
        args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print("YGO_DUPLICATE_TARGETS_V5=" + json.dumps(payload["summary"], separators=(",", ":")))
        return 0

    expansion_evidence = {
        str(row["cardmarket_expansion_id"]): row
        for row in cert.get("certified_expansions") or []
    }

    product_ids = sorted({pid for ids in duplicate_targets.values() for pid in ids})
    print_ids = sorted(duplicate_targets)
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh'")
            gid = int(cur.fetchone()["id"])
            cur.execute(
                """
                SELECT id, external_id, expansion_external_id, name,
                       metacard_external_id, category_id, category, date_added
                FROM external_catalog_products
                WHERE id = ANY(%s)
                  AND source='cardmarket'
                  AND game_id=%s
                  AND product_group='single'
                """,
                (product_ids, gid),
            )
            products = {int(row["id"]): dict(row) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT p.id AS print_id, c.id AS card_id, c.name AS card_name,
                       p.set_id, s.code AS set_code, p.collector_number,
                       p.rarity, p.variant, p.language, p.is_foil
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE p.id = ANY(%s) AND c.game_id=%s
                """,
                (print_ids, gid),
            )
            prints = {int(row["print_id"]): dict(row) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid, cr.name AS release_name
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                WHERE pr.print_id = ANY(%s)
                  AND cr.game_id=%s
                  AND cr.source='konami_neuron'
                """,
                (print_ids, gid),
            )
            release_rows = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT l.print_id, l.external_product_id, l.confidence,
                       l.link_status, l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE l.print_id = ANY(%s)
                  AND e.source='cardmarket'
                  AND e.game_id=%s
                  AND e.product_group='single'
                """,
                (print_ids, gid),
            )
            existing_links = [dict(row) for row in cur.fetchall()]

        releases_by_print: dict[int, list[dict]] = defaultdict(list)
        for row in release_rows:
            releases_by_print[int(row["print_id"])].append(row)
        links_by_print: dict[int, list[dict]] = defaultdict(list)
        for row in existing_links:
            links_by_print[int(row["print_id"])].append(row)

        pattern_counter = Counter()
        expansion_pair_counter = Counter()
        same_metacard_count = 0
        same_name_count = 0
        same_category_count = 0
        all_same_pid_count = 0
        exact_existing_on_duplicate_target = 0
        samples = []

        for print_id, claimant_ids in sorted(duplicate_targets.items()):
            claimant_rows = [products.get(pid) for pid in claimant_ids]
            if any(row is None for row in claimant_rows):
                pattern_counter["missing_product_metadata"] += 1
                continue
            expansions = [str(row.get("expansion_external_id") or "") for row in claimant_rows]
            external_ids = [str(row.get("external_id") or "") for row in claimant_rows]
            names = [str(row.get("name") or "") for row in claimant_rows]
            metacards = [str(row.get("metacard_external_id") or "") for row in claimant_rows]
            categories = [str(row.get("category") or row.get("category_id") or "") for row in claimant_rows]
            pids = [str((expansion_evidence.get(exp) or {}).get("konami_pid") or "") for exp in expansions]
            same_name = len(set(names)) == 1
            same_metacard = len(set(metacards)) == 1
            same_category = len(set(categories)) == 1
            all_same_pid = len(set(pids)) == 1 and bool(pids[0])
            if same_name:
                same_name_count += 1
            if same_metacard:
                same_metacard_count += 1
            if same_category:
                same_category_count += 1
            if all_same_pid:
                all_same_pid_count += 1

            existing_exact = [
                row for row in links_by_print.get(print_id, [])
                if row.get("confidence") == "exact" and row.get("link_status") in ("accepted", "mapped", "exact")
            ]
            if existing_exact:
                exact_existing_on_duplicate_target += 1

            signature = (
                len(claimant_ids),
                same_name,
                same_metacard,
                same_category,
                all_same_pid,
                bool(existing_exact),
            )
            pattern_counter[signature] += 1
            expansion_pair_counter[tuple(sorted(expansions))] += 1

            if len(samples) < args.sample_limit:
                samples.append(
                    {
                        "print_id": print_id,
                        "print": prints.get(print_id),
                        "konami_releases": releases_by_print.get(print_id, []),
                        "claimants": [
                            {
                                "external_product_id": int(row["id"]),
                                "idProduct": str(row.get("external_id") or ""),
                                "expansion": str(row.get("expansion_external_id") or ""),
                                "name": row.get("name"),
                                "idMetacard": row.get("metacard_external_id"),
                                "category": row.get("category"),
                                "date_added": str(row.get("date_added") or ""),
                                "certified_expansion": expansion_evidence.get(str(row.get("expansion_external_id") or "")),
                            }
                            for row in claimant_rows
                        ],
                        "same_name": same_name,
                        "same_metacard": same_metacard,
                        "same_category": same_category,
                        "all_same_konami_pid": all_same_pid,
                        "existing_links": links_by_print.get(print_id, []),
                    }
                )

        patterns = [
            {
                "claimant_count": key[0],
                "same_name": key[1],
                "same_metacard": key[2],
                "same_category": key[3],
                "all_same_konami_pid": key[4],
                "has_existing_exact_target": key[5],
                "targets": count,
            }
            for key, count in pattern_counter.most_common()
        ]
        expansion_patterns = [
            {"expansions": list(pair), "duplicate_targets": count}
            for pair, count in expansion_pair_counter.most_common(100)
        ]
        summary = {
            "duplicate_targets": len(duplicate_targets),
            "claimant_products": len(product_ids),
            "two_claimants": sum(1 for ids in duplicate_targets.values() if len(ids) == 2),
            "three_claimants": sum(1 for ids in duplicate_targets.values() if len(ids) == 3),
            "same_name_targets": same_name_count,
            "same_metacard_targets": same_metacard_count,
            "same_category_targets": same_category_count,
            "all_same_konami_pid_targets": all_same_pid_count,
            "existing_exact_on_duplicate_target": exact_existing_on_duplicate_target,
            "distinct_expansion_claim_patterns": len(expansion_pair_counter),
            "production_writes": 0,
        }
        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "summary": summary,
            "patterns": patterns,
            "top_expansion_claim_patterns": expansion_patterns,
            "samples": samples,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("YGO_DUPLICATE_TARGETS_V5=" + json.dumps(summary, separators=(",", ":")))
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
