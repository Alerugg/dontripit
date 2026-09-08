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
from app.physical_identity_v2 import normalize_token, rarity_family


def _collector_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_token(value))


def _descriptor_key(card_id: object, collector: object, rarity: object) -> tuple[str, str, str]:
    return (str(card_id or "").strip(), _collector_key(collector), rarity_family(str(rarity or "")))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="READ ONLY diagnostic of YGO exact Cardmarket products still pending Physical Identity V2 corroboration"
    )
    parser.add_argument("--corroboration-root", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/yugioh-physical-identity-residual-v2"))
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    cert_summary = json.loads((args.corroboration_root / "summary.json").read_text(encoding="utf-8"))
    if cert_summary.get("status") != "pass" or cert_summary.get("descriptor_shard_files") != 24:
        raise SystemExit("YGO corroboration certificate is not complete/pass")

    pending_ids = {
        str(value)
        for value in json.loads(
            (args.corroboration_root / "pending_existing_exact_product_ids.json").read_text(encoding="utf-8")
        )
    }

    descriptor_rows = list(
        csv.DictReader((args.corroboration_root / "descriptor_results.csv").open("r", encoding="utf-8"))
    )
    exact_descriptor_keys: set[tuple[str, str, str]] = set()
    card_collector_rarities: dict[tuple[str, str], set[str]] = defaultdict(set)
    card_collectors: dict[str, set[str]] = defaultdict(set)
    descriptor_cards: set[str] = set()
    for row in descriptor_rows:
        key = _descriptor_key(row.get("card_id"), row.get("collector"), row.get("rarity"))
        if not key[0] or not key[1]:
            continue
        exact_descriptor_keys.add(key)
        card_collector_rarities[(key[0], key[1])].add(key[2])
        card_collectors[key[0]].add(key[1])
        descriptor_cards.add(key[0])

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        transaction_read_only = str(session.execute(text("SHOW transaction_read_only")).scalar_one()).lower() == "on"
        if not transaction_read_only:
            raise AssertionError("transaction_read_only is not on")

        rows = [dict(row) for row in session.execute(text("""
            SELECT ep.external_id AS product_id,
                   l.print_id,
                   l.mapping_method,
                   p.collector_number,
                   p.rarity,
                   p.variant,
                   p.language,
                   p.yugioh_id AS synthetic_yugioh_print_id,
                   c.yugoprodeck_id AS card_yugoprodeck_id,
                   c.name AS card_name,
                   s.code AS set_code,
                   s.name AS set_name
            FROM external_catalog_print_links l
            JOIN external_catalog_products ep ON ep.id = l.external_product_id
            JOIN prints p ON p.id = l.print_id
            JOIN cards c ON c.id = p.card_id
            JOIN sets s ON s.id = p.set_id
            JOIN games g ON g.id = ep.game_id
            WHERE g.slug = 'yugioh'
              AND ep.source = 'cardmarket'
              AND ep.product_group = 'single'
              AND l.confidence = 'exact'
              AND l.link_status IN ('accepted','mapped')
        """)).mappings().all()]
        session.rollback()

    rows_by_product: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        product_id = str(row.get("product_id") or "").strip()
        if product_id in pending_ids:
            rows_by_product[product_id].append(row)

    result_rows: list[dict] = []
    reason_counts = Counter()
    method_counts = Counter()
    unexpected_exact_products: list[str] = []
    products_missing_db_links: list[str] = []

    for product_id in sorted(pending_ids, key=lambda x: int(x) if x.isdigit() else x):
        product_rows = rows_by_product.get(product_id, [])
        if not product_rows:
            reason = "NO_ACCEPTED_EXACT_DB_LINK_FOUND"
            products_missing_db_links.append(product_id)
            result_rows.append({
                "product_id": product_id,
                "reason": reason,
                "print_count": 0,
                "mapping_methods": "",
                "card_ids": "",
                "collectors": "",
                "rarities": "",
                "descriptor_rarities_same_card_collector": "",
                "descriptor_collectors_same_card": "",
            })
            reason_counts[reason] += 1
            continue

        per_print_reasons: list[str] = []
        same_cc_rarities: set[str] = set()
        same_card_collectors: set[str] = set()
        card_ids: set[str] = set()
        collectors: set[str] = set()
        rarities: set[str] = set()
        methods: set[str] = set()

        for row in product_rows:
            card_id = str(row.get("card_yugoprodeck_id") or "").strip()
            collector = _collector_key(row.get("collector_number"))
            rarity = rarity_family(str(row.get("rarity") or ""))
            methods.add(str(row.get("mapping_method") or "unknown"))
            if card_id:
                card_ids.add(card_id)
            if collector:
                collectors.add(collector)
            if rarity:
                rarities.add(rarity)

            if not card_id:
                per_print_reasons.append("MISSING_CANONICAL_CARD_YGOPRODECK_ID")
                continue

            key = (card_id, collector, rarity)
            if key in exact_descriptor_keys:
                per_print_reasons.append("EXACT_DESCRIPTOR_KEY_PRESENT_UNEXPECTED")
                continue

            cc_key = (card_id, collector)
            if cc_key in card_collector_rarities:
                per_print_reasons.append("RARITY_DIMENSION_MISMATCH")
                same_cc_rarities.update(card_collector_rarities[cc_key])
                continue

            if card_id in descriptor_cards:
                per_print_reasons.append("COLLECTOR_OR_SET_CODE_MISMATCH")
                same_card_collectors.update(card_collectors[card_id])
                continue

            per_print_reasons.append("NO_SHADOW_DESCRIPTOR_FOR_CARD")

        reasons = set(per_print_reasons)
        if "EXACT_DESCRIPTOR_KEY_PRESENT_UNEXPECTED" in reasons:
            reason = "EXACT_DESCRIPTOR_KEY_PRESENT_UNEXPECTED"
            unexpected_exact_products.append(product_id)
        elif "RARITY_DIMENSION_MISMATCH" in reasons:
            reason = "RARITY_DIMENSION_MISMATCH"
        elif "COLLECTOR_OR_SET_CODE_MISMATCH" in reasons:
            reason = "COLLECTOR_OR_SET_CODE_MISMATCH"
        elif reasons == {"MISSING_CANONICAL_CARD_YGOPRODECK_ID"}:
            reason = "MISSING_CANONICAL_CARD_YGOPRODECK_ID"
        elif "NO_SHADOW_DESCRIPTOR_FOR_CARD" in reasons:
            reason = "NO_SHADOW_DESCRIPTOR_FOR_CARD"
        else:
            reason = "MIXED_OR_OTHER"

        reason_counts[reason] += 1
        for method in methods:
            method_counts[method] += 1
        result_rows.append({
            "product_id": product_id,
            "reason": reason,
            "print_count": len({int(row["print_id"]) for row in product_rows}),
            "mapping_methods": "|".join(sorted(methods)),
            "card_ids": "|".join(sorted(card_ids)),
            "collectors": "|".join(sorted(collectors)),
            "rarities": "|".join(sorted(rarities)),
            "descriptor_rarities_same_card_collector": "|".join(sorted(same_cc_rarities)),
            "descriptor_collectors_same_card": "|".join(sorted(same_card_collectors)[:50]),
        })

    summary = {
        "status": "review_required" if unexpected_exact_products or products_missing_db_links else "pass",
        "mode": "READ_ONLY",
        "transaction_read_only": transaction_read_only,
        "production_writes": 0,
        "input_pending_products": len(pending_ids),
        "db_linked_pending_products": len(rows_by_product),
        "reason_counts": dict(reason_counts),
        "mapping_method_product_counts": dict(method_counts),
        "unexpected_exact_descriptor_products": len(unexpected_exact_products),
        "missing_db_link_products": len(products_missing_db_links),
        "source_corroboration_run": 34230844229,
    }

    (args.outdir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(args.outdir / "pending_products.csv", result_rows, [
        "product_id",
        "reason",
        "print_count",
        "mapping_methods",
        "card_ids",
        "collectors",
        "rarities",
        "descriptor_rarities_same_card_collector",
        "descriptor_collectors_same_card",
    ])
    (args.outdir / "unexpected_exact_product_ids.json").write_text(
        json.dumps(unexpected_exact_products, indent=2) + "\n", encoding="utf-8"
    )
    print("YGO_PHYSICAL_IDENTITY_RESIDUAL_V2=" + json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
