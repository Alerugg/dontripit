#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.jobs.cardmarket_identity_resolver import SUPPORTED_GAMES, build_catalog_products
from app.jobs.cardmarket_prices import load_price_guide_file


FINAL_RELATIONSHIPS = {"exact_one_physical", "grouped_physical", "alias_same_physical"}
DIRECT_RELATIONSHIPS = {"exact_one_physical", "grouped_physical"}


def _parse_game_paths(values: list[str], *, required: bool = False) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Expected GAME=PATH, got {value!r}")
        game, raw_path = value.split("=", 1)
        game = game.strip().lower()
        if game not in SUPPORTED_GAMES:
            raise SystemExit(f"Unsupported game {game!r}")
        path = Path(raw_path).expanduser()
        if not path.is_file():
            raise SystemExit(f"Missing input for {game}: {path}")
        result[game] = path
    if required:
        missing = [g for g in SUPPORTED_GAMES if g not in result]
        if missing:
            raise SystemExit(f"Missing inputs for: {', '.join(missing)}")
    return result


def _load_market_evidence(paths: dict[str, Path]) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    evidence: dict[tuple[str, str], dict] = {}
    conflicts: list[dict] = []
    for expected_game, path in paths.items():
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                game = str(row.get("game") or "").strip().lower()
                market = str(row.get("market") or "").strip().lower()
                product_id = str(row.get("external_product_id") or "").strip()
                relationship = str(row.get("relationship") or "").strip().lower()
                fps = tuple(sorted(str(x).strip() for x in (row.get("physical_fingerprints") or []) if str(x).strip()))
                if game != expected_game or market != "cardmarket" or not product_id:
                    continue
                key = (game, product_id)
                normalized = {
                    "game": game,
                    "product_id": product_id,
                    "relationship": relationship,
                    "physical_fingerprints": fps,
                    "evidence_sources": tuple(sorted(str(x) for x in (row.get("evidence_sources") or []) if str(x))),
                }
                previous = evidence.get(key)
                if previous and previous != normalized:
                    conflicts.append({
                        "game": game,
                        "product_id": product_id,
                        "path": str(path),
                        "line": line_number,
                        "first": previous,
                        "second": normalized,
                    })
                    continue
                evidence[key] = normalized
    return evidence, conflicts


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY Cardmarket Physical Identity Coverage V2 audit")
    parser.add_argument("--catalog", action="append", default=[], metavar="GAME=PATH", help="Official current Cardmarket Product List")
    parser.add_argument("--price-guide", action="append", default=[], metavar="GAME=PATH", help="Optional current Cardmarket Price Guide")
    parser.add_argument("--market-evidence", action="append", default=[], metavar="GAME=PATH", help="Certified Physical Identity V2 market_evidence.ndjson")
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/cardmarket-physical-identity-coverage-v2"))
    args = parser.parse_args()

    catalogs = _parse_game_paths(args.catalog, required=True)
    price_guides = _parse_game_paths(args.price_guide)
    evidence_paths = _parse_game_paths(args.market_evidence)
    args.outdir.mkdir(parents=True, exist_ok=True)

    products_by_game: dict[str, dict[str, object]] = {}
    duplicate_catalog_ids: list[dict] = []
    global_owner: dict[str, str] = {}
    for game in SUPPORTED_GAMES:
        products = build_catalog_products(game, load_product_list_file(catalogs[game]))
        by_id: dict[str, object] = {}
        for product in products:
            if product.product_id in by_id:
                duplicate_catalog_ids.append({"game": game, "product_id": product.product_id, "scope": "within_game"})
                continue
            owner = global_owner.get(product.product_id)
            if owner and owner != game:
                duplicate_catalog_ids.append({"game": game, "product_id": product.product_id, "scope": f"cross_game:{owner}"})
                continue
            global_owner[product.product_id] = game
            by_id[product.product_id] = product
        products_by_game[game] = by_id

    priceable_by_game: dict[str, set[str]] = defaultdict(set)
    price_manifest: dict[str, dict] = {}
    for game, path in price_guides.items():
        created_at, rows = load_price_guide_file(path)
        for row in rows:
            if row.product_id:
                priceable_by_game[game].add(str(row.product_id))
        price_manifest[game] = {
            "rows": len(rows),
            "unique_product_ids": len(priceable_by_game[game]),
            "created_at": created_at.isoformat() if created_at else None,
        }

    direct_evidence, evidence_conflicts = _load_market_evidence(evidence_paths)

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        transaction_read_only = str(session.execute(text("SHOW transaction_read_only")).scalar_one()).lower() == "on"
        if not transaction_read_only:
            raise AssertionError("transaction_read_only is not on")

        modern_rows = [dict(row) for row in session.execute(text("""
            SELECT g.slug AS game,
                   ep.external_id AS product_id,
                   l.print_id,
                   l.mapping_method,
                   l.reviewed
            FROM external_catalog_print_links l
            JOIN external_catalog_products ep ON ep.id = l.external_product_id
            JOIN games g ON g.id = ep.game_id
            WHERE ep.source = 'cardmarket'
              AND ep.product_group = 'single'
              AND l.confidence = 'exact'
              AND l.link_status IN ('accepted','mapped')
              AND g.slug IN ('mtg','pokemon','yugioh','onepiece')
        """)).mappings().all()]

        legacy_rows = [dict(row) for row in session.execute(text("""
            SELECT g.slug AS game,
                   pi.external_id AS product_id,
                   pi.print_id
            FROM print_identifiers pi
            JOIN prints p ON p.id = pi.print_id
            JOIN cards c ON c.id = p.card_id
            JOIN games g ON g.id = c.game_id
            WHERE pi.source='cardmarket'
              AND g.slug IN ('mtg','pokemon','yugioh','onepiece')
        """)).mappings().all()]
        session.rollback()

    modern_exact: dict[tuple[str, str], set[int]] = defaultdict(set)
    modern_methods: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in modern_rows:
        game = str(row.get("game") or "").strip().lower()
        product_id = str(row.get("product_id") or "").strip()
        if game in SUPPORTED_GAMES and product_id:
            modern_exact[(game, product_id)].add(int(row["print_id"]))
            modern_methods[(game, product_id)].add(str(row.get("mapping_method") or "unknown"))

    legacy_exact: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in legacy_rows:
        game = str(row.get("game") or "").strip().lower()
        product_id = str(row.get("product_id") or "").strip()
        if game in SUPPORTED_GAMES and product_id:
            legacy_exact[(game, product_id)].add(int(row["print_id"]))

    current_product_ids = {(game, pid) for game, items in products_by_game.items() for pid in items}
    stale_direct_ids = sorted([
        {"game": game, "product_id": product_id, "relationship": row["relationship"]}
        for (game, product_id), row in direct_evidence.items()
        if (game, product_id) not in current_product_ids
    ], key=lambda x: (x["game"], int(x["product_id"]) if x["product_id"].isdigit() else x["product_id"]))

    fingerprint_set_to_products: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for (game, product_id), row in direct_evidence.items():
        fps = tuple(row.get("physical_fingerprints") or ())
        if fps and (game, product_id) in current_product_ids:
            fingerprint_set_to_products[(game, fps)].append(product_id)
    alias_candidates: dict[tuple[str, str], tuple[str, ...]] = {}
    for (game, _fps), product_ids in fingerprint_set_to_products.items():
        unique_ids = tuple(sorted(set(product_ids), key=lambda x: int(x) if x.isdigit() else x))
        if len(unique_ids) > 1:
            for product_id in unique_ids:
                alias_candidates[(game, product_id)] = tuple(pid for pid in unique_ids if pid != product_id)

    product_rows: list[dict] = []
    expansion_gaps: dict[tuple[str, str], Counter] = defaultdict(Counter)
    games_summary: dict[str, dict] = {}

    for game in SUPPORTED_GAMES:
        counts = Counter()
        price_counts = Counter()
        current = products_by_game[game]
        for product_id, product in current.items():
            key = (game, product_id)
            direct = direct_evidence.get(key)
            modern_prints = modern_exact.get(key, set())
            legacy_prints = legacy_exact.get(key, set())
            alias_peers = alias_candidates.get(key, ())

            relationship = str(direct.get("relationship") or "") if direct else ""
            fp_count = len(direct.get("physical_fingerprints") or ()) if direct else 0
            evidence_sources = list(direct.get("evidence_sources") or ()) if direct else []

            if direct and relationship == "exact_one_physical" and fp_count == 1:
                classification = "EXACT_ONE_PHYSICAL"
                gate_bucket = "DIRECT_V2_ACCOUNTED"
                reason = "certified_direct_physical_market_id"
            elif direct and relationship == "grouped_physical" and fp_count >= 2:
                classification = "GROUPED_PHYSICAL"
                gate_bucket = "DIRECT_V2_ACCOUNTED"
                reason = "certified_market_product_groups_multiple_physical_variants"
            elif direct:
                classification = "AMBIGUOUS"
                gate_bucket = "AMBIGUOUS"
                reason = "invalid_or_conflicting_direct_v2_relationship"
            elif modern_prints:
                classification = "EXACT_CANONICAL_PENDING_V2"
                gate_bucket = "PROVISIONAL_ONLY"
                reason = "accepted_exact_canonical_link_needs_physical_v2_corroboration"
                evidence_sources = [f"production_exact:{m}" for m in sorted(modern_methods.get(key, set()))]
            elif legacy_prints:
                classification = "LEGACY_PENDING_V2"
                gate_bucket = "PROVISIONAL_ONLY"
                reason = "legacy_print_identifier_only"
                evidence_sources = ["legacy:print_identifier"]
            else:
                classification = "UNRESOLVED"
                gate_bucket = "UNRESOLVED"
                reason = "no_direct_v2_or_accepted_exact_evidence"

            if alias_peers:
                counts["alias_candidates"] += 1

            counts[classification] += 1
            counts[gate_bucket] += 1
            price_available = product_id in priceable_by_game.get(game, set())
            if price_available:
                price_counts[classification] += 1
                price_counts[gate_bucket] += 1

            if gate_bucket in {"AMBIGUOUS", "UNRESOLVED", "PROVISIONAL_ONLY"}:
                expansion_gaps[(game, str(product.expansion_id or ""))][reason] += 1

            product_rows.append({
                "game": game,
                "product_id": product_id,
                "name": product.name,
                "expansion_id": product.expansion_id,
                "classification": classification,
                "gate_bucket": gate_bucket,
                "reason": reason,
                "physical_fingerprint_count": fp_count,
                "accepted_exact_print_links": len(modern_prints),
                "legacy_print_links": len(legacy_prints),
                "alias_candidate_peer_ids": "|".join(alias_peers),
                "current_price_guide": "yes" if price_available else "no",
                "evidence_sources": "|".join(evidence_sources),
            })

        total = len(current)
        direct_accounted = counts["DIRECT_V2_ACCOUNTED"]
        provisional_only = counts["PROVISIONAL_ONLY"]
        ambiguous = counts["AMBIGUOUS"]
        unresolved = counts["UNRESOLVED"]
        games_summary[game] = {
            "cardmarket_current_products": total,
            "direct_v2_accounted": direct_accounted,
            "direct_v2_coverage": round(direct_accounted / total, 6) if total else 0.0,
            "provisional_existing_exact_or_legacy": provisional_only,
            "provisional_union_accounted": direct_accounted + provisional_only,
            "provisional_union_coverage": round((direct_accounted + provisional_only) / total, 6) if total else 0.0,
            "ambiguous": ambiguous,
            "unresolved": unresolved,
            "alias_candidates_not_promoted": counts["alias_candidates"],
            "classification_counts": {k: v for k, v in sorted(counts.items()) if k not in {"DIRECT_V2_ACCOUNTED","PROVISIONAL_ONLY","AMBIGUOUS","UNRESOLVED","alias_candidates"}},
            "current_price_guide_products": len(priceable_by_game.get(game, set()) & set(current)),
            "direct_v2_accounted_with_current_price": price_counts["DIRECT_V2_ACCOUNTED"],
        }

    gap_rows: list[dict] = []
    for (game, expansion_id), reasons in expansion_gaps.items():
        total = sum(reasons.values())
        gap_rows.append({
            "game": game,
            "expansion_id": expansion_id,
            "gap_products": total,
            "reason_counts_json": json.dumps(dict(reasons.most_common()), sort_keys=True),
        })
    gap_rows.sort(key=lambda row: (row["game"], -int(row["gap_products"]), row["expansion_id"]))

    total_products = sum(v["cardmarket_current_products"] for v in games_summary.values())
    total_direct = sum(v["direct_v2_accounted"] for v in games_summary.values())
    total_union = sum(v["provisional_union_accounted"] for v in games_summary.values())
    total_ambiguous = sum(v["ambiguous"] for v in games_summary.values())
    total_unresolved = sum(v["unresolved"] for v in games_summary.values())

    forbidden = {
        "duplicate_current_catalog_idProduct": len(duplicate_catalog_ids),
        "direct_v2_evidence_conflicts": len(evidence_conflicts),
        "direct_v2_cross_game_or_stale_ids": len(stale_direct_ids),
    }

    summary = {
        "status": "pass" if not any(forbidden.values()) and transaction_read_only else "fail",
        "mode": "READ_ONLY",
        "transaction_read_only": transaction_read_only,
        "production_writes": 0,
        "target_direct_v2_coverage": 0.99,
        "gate_definition": "Only certified direct Physical Identity V2 EXACT/GROUPED (and future separately certified aliases) count toward final 99% cutover coverage. Existing production exact/legacy links are shown as provisional and do not silently satisfy the V2 physical gate.",
        "games": games_summary,
        "global": {
            "cardmarket_current_products": total_products,
            "direct_v2_accounted": total_direct,
            "direct_v2_coverage": round(total_direct / total_products, 6) if total_products else 0.0,
            "provisional_union_accounted": total_union,
            "provisional_union_coverage": round(total_union / total_products, 6) if total_products else 0.0,
            "ambiguous": total_ambiguous,
            "unresolved": total_unresolved,
        },
        "forbidden_mismatches": forbidden,
        "stale_direct_evidence_sample": stale_direct_ids[:100],
        "price_guides": price_manifest,
    }

    (args.outdir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(args.outdir / "products.csv", product_rows, [
        "game","product_id","name","expansion_id","classification","gate_bucket","reason",
        "physical_fingerprint_count","accepted_exact_print_links","legacy_print_links",
        "alias_candidate_peer_ids","current_price_guide","evidence_sources",
    ])
    _write_csv(args.outdir / "gap_expansions.csv", gap_rows, ["game","expansion_id","gap_products","reason_counts_json"])
    (args.outdir / "evidence_conflicts.json").write_text(json.dumps(evidence_conflicts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.outdir / "duplicate_catalog_ids.json").write_text(json.dumps(duplicate_catalog_ids, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("CARDMARKET_PHYSICAL_IDENTITY_COVERAGE_V2=" + json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
