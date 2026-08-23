from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

EXPECTED_PDF_SHA256 = "cd518a04ea3ff1acdc1f3bc824ad53d0ca17d8ee2fd0a6427717e0bdaacbdfe0"
EXPECTED_OFFICIAL_ITEMS = 262
MIN_MARKET_METACARDS = 150
OUTPUT = Path(os.getenv("ONEPIECE_DON_CERT_OUTPUT", "artifacts/onepiece-don-structured-v1-cert.json"))


def main() -> int:
    url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url); conn.autocommit = False
    report = {"production_writes": 0}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='12s'")
            cur.execute("SHOW transaction_read_only")
            report["transaction_read_only"] = cur.fetchone()["transaction_read_only"] == "on"
            if not report["transaction_read_only"]:
                raise AssertionError("certifier is not read-only")

            cur.execute("SELECT count(*) n,count(DISTINCT image_sha256) unique_images,count(*) FILTER(WHERE print_id IS NOT NULL) mapped,min(sequence_number) min_sequence,max(sequence_number) max_sequence FROM onepiece_don_official_items WHERE pdf_sha256=%s", (EXPECTED_PDF_SHA256,))
            report["official"] = dict(cur.fetchone())

            cur.execute("SELECT evidence_key,evidence_kind,organization,physical_received,claimed_label,identity_status FROM onepiece_don_evidence_items WHERE evidence_key IN ('osaka-championship-2023-minisite-test','collaborator-bushiroad-premier-received') ORDER BY evidence_key")
            report["evidence"] = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT max(source_as_of) latest FROM onepiece_don_market_items WHERE source='cardmarket'")
            latest = cur.fetchone()["latest"]
            report["cardmarket_as_of"] = latest
            cur.execute("SELECT count(*) n,count(*) FILTER(WHERE subject_normalized IS NOT NULL) subject_rows,sum(product_count) represented_products,count(*) FILTER(WHERE official_item_id IS NOT NULL) mapped FROM onepiece_don_market_items WHERE source='cardmarket' AND source_as_of=%s", (latest,))
            report["market"] = dict(cur.fetchone())

            cur.execute("SELECT count(*) n FROM onepiece_don_prints")
            report["canonical_don_prints"] = int(cur.fetchone()["n"])

            cur.execute("SELECT count(*) n FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id JOIN games g ON g.id=e.game_id WHERE g.slug='onepiece' AND e.source='cardmarket' AND e.product_group='single' AND (lower(e.name) LIKE '%%don!!%%' OR lower(e.name) LIKE '%%don card%%' OR lower(coalesce(e.category,'')) LIKE '%%don%%' OR lower(coalesce(e.website_path,'')) LIKE '%%don%%') AND l.link_status IN ('accepted','mapped','exact')")
            report["accepted_external_don_print_links"] = int(cur.fetchone()["n"])

            o = report["official"]
            if (int(o["n"]), int(o["unique_images"]), int(o["mapped"]), int(o["min_sequence"]), int(o["max_sequence"])) != (262,262,0,1,262):
                raise AssertionError({"official": o})
            if len(report["evidence"]) != 2:
                raise AssertionError({"evidence": report["evidence"]})
            osaka = next(r for r in report["evidence"] if r["evidence_key"].startswith("osaka-"))
            collaborator = next(r for r in report["evidence"] if r["evidence_key"].startswith("collaborator-"))
            if osaka["claimed_label"] != "ST-01" or osaka["physical_received"]:
                raise AssertionError({"osaka": osaka})
            if collaborator["claimed_label"] is not None or not collaborator["physical_received"]:
                raise AssertionError({"collaborator": collaborator})
            if osaka["identity_status"] != "unresolved" or collaborator["identity_status"] != "unresolved":
                raise AssertionError("unresolved evidence was promoted without certification")
            m = report["market"]
            if int(m["n"] or 0) < MIN_MARKET_METACARDS or int(m["represented_products"] or 0) < int(m["n"] or 0):
                raise AssertionError({"market": m})
            if int(m["mapped"] or 0) != 0 or report["canonical_don_prints"] != 0 or report["accepted_external_don_print_links"] != 0:
                raise AssertionError({"market_mapped": m["mapped"], "canonical_don_prints": report["canonical_don_prints"], "accepted_external_don_print_links": report["accepted_external_don_print_links"]})

            report["status"] = "pass"
            conn.rollback()
    finally:
        conn.close()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    print(OUTPUT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
