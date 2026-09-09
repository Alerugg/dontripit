#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.jobs.cardmarket_identity_resolver import build_catalog_products
from app.physical_identity_v2 import normalize_token, rarity_family


def _collector_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_token(value))


def _descriptor_key(card_id: object, collector: object, rarity: object) -> tuple[str, str, str]:
    return (str(card_id or "").strip(), _collector_key(collector), rarity_family(str(rarity or "")))


def _load_descriptors(root: Path) -> tuple[list[dict], list[Path]]:
    rows: list[dict] = []
    files = sorted(root.rglob("descriptors.ndjson"))
    for path in files:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("game") != "yugioh":
                    continue
                row["_source_path"] = str(path)
                row["_line"] = line_number
                rows.append(row)
    return rows, files


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY YGO Physical Identity V2 corroboration against accepted exact Cardmarket links")
    parser.add_argument("--shards-root", type=Path, required=True)
    parser.add_argument("--expected-shards", type=int, default=24)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/yugioh-physical-identity-corroboration-v2"))
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    descriptors, descriptor_files = _load_descriptors(args.shards_root)
    if not descriptors:
        raise SystemExit(f"No YGO descriptors found under {args.shards_root}")
    input_complete = len(descriptor_files) == args.expected_shards

    catalog_products = build_catalog_products("yugioh", load_product_list_file(args.catalog))
    current_catalog_ids = {p.product_id for p in catalog_products}

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        transaction_read_only = str(session.execute(text("SHOW transaction_read_only")).scalar_one()).lower() == "on"
        if not transaction_read_only:
            raise AssertionError("transaction_read_only is not on")

        print_rows = [dict(row) for row in session.execute(text("""
            SELECT p.id AS print_id,
                   p.yugioh_id AS synthetic_yugioh_print_id,
                   p.collector_number,
                   p.rarity,
                   p.language,
                   p.variant,
                   c.name AS card_name,
                   c.yugoprodeck_id AS card_yugoprodeck_id,
                   s.name AS set_name,
                   s.code AS set_code
            FROM prints p
            JOIN cards c ON c.id = p.card_id
            JOIN sets s ON s.id = p.set_id
            JOIN games g ON g.id = s.game_id
            WHERE g.slug = 'yugioh'
        """)).mappings().all()]

        link_rows = [dict(row) for row in session.execute(text("""
            SELECT l.print_id,
                   ep.external_id AS product_id,
                   l.mapping_method,
                   l.reviewed
            FROM external_catalog_print_links l
            JOIN external_catalog_products ep ON ep.id = l.external_product_id
            JOIN games g ON g.id = ep.game_id
            WHERE g.slug = 'yugioh'
              AND ep.source = 'cardmarket'
              AND ep.product_group = 'single'
              AND l.confidence = 'exact'
              AND l.link_status IN ('accepted','mapped')
        """)).mappings().all()]
        session.rollback()

    products_by_print: dict[int, set[str]] = defaultdict(set)
    methods_by_print: dict[int, set[str]] = defaultdict(set)
    for row in link_rows:
        product_id = str(row.get("product_id") or "").strip()
        if not product_id:
            continue
        pid = int(row["print_id"])
        products_by_print[pid].add(product_id)
        methods_by_print[pid].add(str(row.get("mapping_method") or "unknown"))

    prints_by_key: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in print_rows:
        # Print.yugioh_id is a synthetic per-print key (cardId::setCode::ordinal).
        # The actual YGOPRODeck card id lives on Card.yugoprodeck_id.
        key = _descriptor_key(row.get("card_yugoprodeck_id"), row.get("collector_number"), row.get("rarity"))
        if key[0] and key[1]:
            prints_by_key[key].append(row)

    descriptor_results: list[dict] = []
    product_to_fps: dict[str, set[str]] = defaultdict(set)
    product_sources: dict[str, set[str]] = defaultdict(set)
    counters = Counter()
    conflict_rows: list[dict] = []

    for descriptor in descriptors:
        facts = descriptor.get("source_facts") or {}
        dimensions = descriptor.get("dimensions") or {}
        rarity_values = ((dimensions.get("rarity") or {}).get("values") or [])
        collector_values = ((dimensions.get("collector_number") or {}).get("values") or [])
        card_id = str(facts.get("ygoprodeck_card_id") or descriptor.get("card_concept") or "").strip()
        collector = str(facts.get("set_code") or (collector_values[0] if collector_values else "")).strip()
        rarity = str(rarity_values[0] if rarity_values else facts.get("set_rarity") or "").strip()
        key = _descriptor_key(card_id, collector, rarity)
        matched_prints = prints_by_key.get(key, [])
        matched_print_ids = {int(row["print_id"]) for row in matched_prints}
        linked_products = sorted(
            {pid for print_id in matched_print_ids for pid in products_by_print.get(print_id, set())},
            key=lambda x: int(x) if x.isdigit() else x,
        )
        current_products = [pid for pid in linked_products if pid in current_catalog_ids]
        stale_products = [pid for pid in linked_products if pid not in current_catalog_ids]

        if not matched_prints:
            status = "NO_CANONICAL_MATCH"
        elif len(current_products) == 1:
            status = "CORROBORATED_ONE_PRODUCT"
            product_id = current_products[0]
            product_to_fps[product_id].add(str(descriptor["fingerprint"]))
            for print_id in matched_print_ids:
                product_sources[product_id].update(methods_by_print.get(print_id, set()))
        elif len(current_products) > 1:
            status = "MULTI_PRODUCT_CONFLICT"
            conflict_rows.append({
                "fingerprint": descriptor.get("fingerprint"),
                "card_id": card_id,
                "collector": collector,
                "rarity": rarity,
                "product_ids": "|".join(current_products),
                "matched_print_ids": "|".join(str(x) for x in sorted(matched_print_ids)),
            })
        else:
            status = "CANONICAL_MATCH_NO_CURRENT_PRODUCT"

        counters[status] += 1
        descriptor_results.append({
            "fingerprint": descriptor.get("fingerprint"),
            "source_print_id": descriptor.get("source_print_id"),
            "card_id": card_id,
            "release": descriptor.get("release"),
            "collector": collector,
            "rarity": rarity,
            "status": status,
            "matched_print_count": len(matched_print_ids),
            "matched_print_ids": "|".join(str(x) for x in sorted(matched_print_ids)),
            "current_product_ids": "|".join(current_products),
            "stale_product_ids": "|".join(stale_products),
        })

    evidence_rows: list[dict] = []
    relationship_counts = Counter()
    for product_id, fps in sorted(product_to_fps.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
        relationship = "exact_one_physical" if len(fps) == 1 else "grouped_physical"
        relationship_counts[relationship] += 1
        evidence_rows.append({
            "game": "yugioh",
            "market": "cardmarket",
            "external_product_id": product_id,
            "relationship": relationship,
            "physical_fingerprints": sorted(fps),
            "evidence_sources": ["ygoprodeck:physical_v2", "production:accepted_exact_cardmarket"],
            "mapping_methods": sorted(product_sources.get(product_id, set())),
        })

    accepted_current_products = {
        product_id
        for products in products_by_print.values()
        for product_id in products
        if product_id in current_catalog_ids
    }
    corroborated_products = set(product_to_fps)
    pending_products = accepted_current_products - corroborated_products

    if not input_complete:
        status = "incomplete_input"
    elif not transaction_read_only:
        status = "unsafe"
    elif not corroborated_products:
        status = "no_corroboration"
    else:
        status = "pass"

    summary = {
        "status": status,
        "mode": "READ_ONLY",
        "transaction_read_only": transaction_read_only,
        "production_writes": 0,
        "expected_shards": args.expected_shards,
        "descriptor_shard_files": len(descriptor_files),
        "yugioh_shadow_descriptor_files": len(descriptor_files),
        "input_complete": input_complete,
        "yugioh_shadow_descriptors": len(descriptors),
        "canonical_prints": len(print_rows),
        "accepted_exact_current_cardmarket_products": len(accepted_current_products),
        "physically_corroborated_current_products": len(corroborated_products),
        "corroboration_coverage_of_existing_exact": round(len(corroborated_products) / len(accepted_current_products), 6) if accepted_current_products else 0.0,
        "accepted_exact_pending_physical_corroboration": len(pending_products),
        "descriptor_status_counts": dict(counters),
        "market_relationships": dict(relationship_counts),
        "multi_product_conflicts_quarantined": len(conflict_rows),
        "current_cardmarket_products": len(current_catalog_ids),
    }

    (args.outdir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (args.outdir / "market_evidence.ndjson").open("w", encoding="utf-8") as handle:
        for row in evidence_rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    _write_csv(args.outdir / "descriptor_results.csv", descriptor_results, [
        "fingerprint","source_print_id","card_id","release","collector","rarity","status",
        "matched_print_count","matched_print_ids","current_product_ids","stale_product_ids",
    ])
    _write_csv(args.outdir / "conflicts.csv", conflict_rows, ["fingerprint","card_id","collector","rarity","product_ids","matched_print_ids"])
    (args.outdir / "pending_existing_exact_product_ids.json").write_text(
        json.dumps(sorted(pending_products, key=lambda x: int(x) if x.isdigit() else x), indent=2) + "\n",
        encoding="utf-8",
    )

    print("YGO_PHYSICAL_IDENTITY_CORROBORATION_V2=" + json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
