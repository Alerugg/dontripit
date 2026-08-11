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
    parser = argparse.ArgumentParser(description="Read-only classification of the remaining MTG Cardmarket exact-link gap.")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sample-limit", type=int, default=30)
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                "SELECT max(last_seen_at) AS latest_seen FROM external_catalog_products WHERE source='cardmarket' AND game_id=%s",
                (game_id,),
            )
            latest_seen = cur.fetchone()["latest_seen"]

            cur.execute(
                """
                SELECT p.id AS print_id, p.scryfall_id, p.variant, p.language, p.collector_number,
                       s.code AS set_code, c.id AS card_id, c.name AS card_name,
                       pa.attributes_json,
                       EXISTS (
                         SELECT 1
                         FROM external_catalog_print_links l
                         JOIN external_catalog_products ep ON ep.id=l.external_product_id
                         WHERE l.print_id=p.id
                           AND ep.source='cardmarket' AND ep.game_id=%s AND ep.product_group='single'
                           AND l.link_status IN ('accepted','mapped') AND l.confidence='exact'
                       ) AS exact_mapped
                FROM prints p
                JOIN cards c ON c.id=p.card_id AND c.game_id=%s
                JOIN sets s ON s.id=p.set_id
                LEFT JOIN print_attributes pa ON pa.print_id=p.id AND pa.source='scryfall'
                ORDER BY p.id
                """,
                (game_id, game_id),
            )
            prints = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT external_id
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single' AND last_seen_at=%s
                """,
                (game_id, latest_seen),
            )
            current_external_ids = {str(row["external_id"]) for row in cur.fetchall()}

        by_cmid: dict[str, list[dict]] = defaultdict(list)
        for row in prints:
            attrs = row.get("attributes_json") or {}
            cmid = attrs.get("cardmarket_id")
            if cmid is not None and str(cmid).strip():
                by_cmid[str(cmid)].append(row)

        cmid_status: dict[str, str] = {}
        for cmid, rows in by_cmid.items():
            signatures = {(r["set_code"], str(r["collector_number"]), int(r["card_id"])) for r in rows}
            if cmid not in current_external_ids:
                cmid_status[cmid] = "source_id_absent_current_catalog"
            elif len(signatures) != 1:
                cmid_status[cmid] = "source_id_conflicting_signature"
            else:
                cmid_status[cmid] = "source_id_structurally_safe"

        counts = Counter()
        variant_by_reason: dict[str, Counter] = defaultdict(Counter)
        language_by_reason: dict[str, Counter] = defaultdict(Counter)
        set_by_reason: dict[str, Counter] = defaultdict(Counter)
        samples: dict[str, list[dict]] = defaultdict(list)
        attribute_keys_without_id = Counter()

        for row in prints:
            if row["exact_mapped"]:
                reason = "mapped_exact"
            else:
                attrs = row.get("attributes_json") or {}
                cmid = attrs.get("cardmarket_id")
                if cmid is None or not str(cmid).strip():
                    reason = "source_cardmarket_id_absent"
                    for key in attrs.keys():
                        attribute_keys_without_id[str(key)] += 1
                else:
                    status = cmid_status.get(str(cmid), "source_id_unknown")
                    reason = "source_id_safe_but_unmapped" if status == "source_id_structurally_safe" else status

            counts[reason] += 1
            variant_by_reason[reason][str(row.get("variant") or "")] += 1
            language_by_reason[reason][str(row.get("language") or "")] += 1
            set_by_reason[reason][str(row.get("set_code") or "")] += 1
            if reason != "mapped_exact" and len(samples[reason]) < args.sample_limit:
                attrs = row.get("attributes_json") or {}
                samples[reason].append(
                    {
                        "print_id": int(row["print_id"]),
                        "scryfall_id": row.get("scryfall_id"),
                        "set_code": row.get("set_code"),
                        "collector_number": row.get("collector_number"),
                        "card_name": row.get("card_name"),
                        "variant": row.get("variant"),
                        "language": row.get("language"),
                        "cardmarket_id": attrs.get("cardmarket_id"),
                        "scryfall_attribute_keys": sorted(map(str, attrs.keys())),
                    }
                )

        total = len(prints)
        mapped = counts.get("mapped_exact", 0)
        gap = total - mapped
        payload = {
            "mode": "read_only",
            "game": "mtg",
            "source": "scryfall",
            "market": "cardmarket",
            "latest_cardmarket_seen_at": latest_seen.isoformat() if latest_seen else None,
            "summary": {
                "canonical_prints": total,
                "mapped_exact": mapped,
                "remaining_gap": gap,
                "coverage_pct": round((mapped / total * 100.0), 4) if total else 0.0,
                "reasons": dict(sorted(counts.items())),
                "source_distinct_cardmarket_ids": len(by_cmid),
                "structurally_safe_ids": sum(1 for value in cmid_status.values() if value == "source_id_structurally_safe"),
                "conflicting_signature_ids": sum(1 for value in cmid_status.values() if value == "source_id_conflicting_signature"),
                "absent_current_catalog_ids": sum(1 for value in cmid_status.values() if value == "source_id_absent_current_catalog"),
            },
            "top_variants_by_reason": {reason: counter.most_common(20) for reason, counter in variant_by_reason.items() if reason != "mapped_exact"},
            "top_languages_by_reason": {reason: counter.most_common(20) for reason, counter in language_by_reason.items() if reason != "mapped_exact"},
            "top_sets_by_reason": {reason: counter.most_common(30) for reason, counter in set_by_reason.items() if reason != "mapped_exact"},
            "attribute_keys_on_source_id_absent": attribute_keys_without_id.most_common(50),
            "samples": dict(samples),
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
