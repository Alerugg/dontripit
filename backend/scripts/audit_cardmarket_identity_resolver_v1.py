#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from sqlalchemy import text

from app import db
from app.jobs.cardmarket_catalog_audit import load_product_list_file
from app.jobs.cardmarket_identity_resolver import (
    SUPPORTED_GAMES,
    CatalogProduct,
    ResolverIdentity,
    build_catalog_products,
    cardmarket_redirect_url,
    normalize_collector,
    normalize_text,
    resolve_identity,
)
from app.jobs.cardmarket_prices import CardmarketPriceRow, load_price_guide_file


GAME_ORDER = {"mtg": 1, "pokemon": 2, "yugioh": 3, "onepiece": 4}


def _parse_game_paths(values: list[str], *, required: bool) -> dict[str, Path]:
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
        missing = [game for game in SUPPORTED_GAMES if game not in result]
        if missing:
            raise SystemExit(f"Missing catalog inputs for: {', '.join(missing)}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_created_at(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if isinstance(payload, dict):
        value = payload.get("createdAt") or payload.get("created_at")
        return str(value) if value else None
    return None


def _meaningful_snapshot(row: dict | None) -> bool:
    if not row:
        return False
    return any(row.get(field) is not None for field in ("price_low", "price_mid", "price_high", "price_market", "price_last"))


def _price_row_has_finish(row: CardmarketPriceRow | None, *, is_foil: bool) -> bool:
    if row is None:
        return False
    if is_foil:
        values = (row.foil_avg, row.foil_low, row.foil_trend, row.foil_avg1, row.foil_avg7, row.foil_avg30)
    else:
        values = (row.avg, row.low, row.low_ex, row.trend, row.avg1, row.avg7, row.avg30)
    return any(value is not None for value in values)


def _uniq(values) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "READ ONLY Cardmarket Identity Resolver V1. Resolves grouped canonical Prints to current "
            "Cardmarket idProduct using existing exact links, trusted source IDs, learned expansion "
            "crosswalks and fail-closed game-specific matching."
        )
    )
    parser.add_argument("--catalog", action="append", default=[], metavar="GAME=PATH", help="Current official Cardmarket singles catalog; repeat for all four games")
    parser.add_argument("--price-guide", action="append", default=[], metavar="GAME=PATH", help="Optional current official Cardmarket price guide; used only to project price recoverability")
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/cardmarket-identity-resolver-v1"))
    parser.add_argument("--min-expansion-samples", type=int, default=3)
    parser.add_argument("--high-confidence-threshold", type=float, default=0.985)
    parser.add_argument("--min-margin", type=float, default=0.05)
    args = parser.parse_args()

    catalogs = _parse_game_paths(args.catalog, required=True)
    price_guides = _parse_game_paths(args.price_guide, required=False)
    args.outdir.mkdir(parents=True, exist_ok=True)

    catalog_products: dict[str, list[CatalogProduct]] = {}
    products_by_game_id: dict[str, dict[str, CatalogProduct]] = {}
    products_by_game_expansion: dict[str, dict[str, list[CatalogProduct]]] = {}
    catalog_manifest: dict[str, dict] = {}
    global_product_owner: dict[str, str] = {}
    duplicate_product_ids: list[dict] = []

    for game in SUPPORTED_GAMES:
        rows = load_product_list_file(catalogs[game])
        products = build_catalog_products(game, rows)
        by_id: dict[str, CatalogProduct] = {}
        by_expansion: dict[str, list[CatalogProduct]] = defaultdict(list)
        for product in products:
            if product.product_id in by_id:
                duplicate_product_ids.append({"game": game, "product_id": product.product_id, "scope": "within_game"})
                continue
            owner = global_product_owner.get(product.product_id)
            if owner and owner != game:
                duplicate_product_ids.append({"game": game, "product_id": product.product_id, "scope": f"cross_game:{owner}"})
                continue
            global_product_owner[product.product_id] = game
            by_id[product.product_id] = product
            by_expansion[product.expansion_id].append(product)
        catalog_products[game] = products
        products_by_game_id[game] = by_id
        products_by_game_expansion[game] = dict(by_expansion)
        catalog_manifest[game] = {
            "path": str(catalogs[game]),
            "sha256": _sha256(catalogs[game]),
            "created_at": _json_created_at(catalogs[game]),
            "rows": len(products),
            "unique_product_ids": len(by_id),
        }

    if duplicate_product_ids:
        payload = {"status": "failed", "reason": "duplicate_cardmarket_idProduct", "duplicates": duplicate_product_ids[:100]}
        (args.outdir / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    price_rows_by_game: dict[str, dict[str, CardmarketPriceRow]] = {}
    price_manifest: dict[str, dict] = {}
    for game, path in price_guides.items():
        created_at, rows = load_price_guide_file(path)
        by_id: dict[str, CardmarketPriceRow] = {}
        duplicate_rows = 0
        for row in rows:
            if row.product_id in by_id:
                duplicate_rows += 1
                continue
            by_id[row.product_id] = row
        price_rows_by_game[game] = by_id
        price_manifest[game] = {
            "path": str(path),
            "sha256": _sha256(path),
            "created_at": created_at.isoformat() if created_at else _json_created_at(path),
            "rows": len(rows),
            "unique_product_ids": len(by_id),
            "duplicate_rows": duplicate_rows,
        }

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
            SELECT p.id AS print_id, p.card_id, p.set_id, p.collector_number, p.language,
                   p.rarity, p.is_foil, p.variant, p.print_key, p.scryfall_id,
                   p.tcgdex_id, p.yugioh_id, c.name AS card_name, c.card_key,
                   s.code AS set_code, s.name AS set_name, s.region AS set_region,
                   g.slug AS game
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN sets s ON s.id=p.set_id
            JOIN games g ON g.id=s.game_id
            WHERE g.slug IN ('mtg','pokemon','yugioh','onepiece')
            ORDER BY p.id
        """)).mappings().all()]

        modern_link_rows = [dict(row) for row in session.execute(text("""
            SELECT l.print_id, ep.external_id, ep.expansion_external_id, g.slug AS game,
                   l.mapping_method, l.reviewed
            FROM external_catalog_print_links l
            JOIN external_catalog_products ep ON ep.id=l.external_product_id
            JOIN games g ON g.id=ep.game_id
            WHERE ep.source='cardmarket'
              AND ep.product_group='single'
              AND l.confidence='exact'
              AND l.link_status IN ('accepted','mapped')
              AND g.slug IN ('mtg','pokemon','yugioh','onepiece')
        """)).mappings().all()]

        legacy_link_rows = [dict(row) for row in session.execute(text("""
            SELECT pi.print_id, pi.external_id, g.slug AS game
            FROM print_identifiers pi
            JOIN prints p ON p.id=pi.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            WHERE pi.source='cardmarket'
              AND g.slug IN ('mtg','pokemon','yugioh','onepiece')
        """)).mappings().all()]

        scryfall_attribute_rows = [dict(row) for row in session.execute(text("""
            SELECT pa.print_id, pa.attributes_json
            FROM print_attributes pa
            JOIN prints p ON p.id=pa.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            WHERE pa.source='scryfall' AND g.slug='mtg'
        """)).mappings().all()]

        price_source_id = session.execute(text("SELECT id FROM price_sources WHERE name='cardmarket' LIMIT 1")).scalar_one_or_none()
        latest_price_by_print: dict[int, dict] = {}
        if price_source_id is not None:
            latest_rows = session.execute(text("""
                SELECT DISTINCT ON (ps.entity_id)
                       ps.entity_id, ps.as_of, ps.price_low, ps.price_mid, ps.price_high,
                       ps.price_market, ps.price_last
                FROM price_snapshots ps
                WHERE ps.entity_type='print'
                  AND ps.source_id=:source_id
                  AND ps.currency='EUR'
                ORDER BY ps.entity_id, ps.as_of DESC
            """), {"source_id": int(price_source_id)}).mappings().all()
            latest_price_by_print = {int(row["entity_id"]): dict(row) for row in latest_rows}

        session.rollback()

    by_print = {int(row["print_id"]): row for row in print_rows}
    known_ids_by_print: dict[int, set[str]] = defaultdict(set)
    known_methods_by_print: dict[int, set[str]] = defaultdict(set)
    known_cross_game_conflicts: list[dict] = []

    for row in modern_link_rows:
        pid = int(row["print_id"])
        product_id = str(row["external_id"] or "").strip()
        if not product_id:
            continue
        print_game = str(by_print.get(pid, {}).get("game") or "")
        if print_game and print_game != str(row["game"]):
            known_cross_game_conflicts.append({"print_id": pid, "print_game": print_game, "link_game": row["game"], "product_id": product_id, "source": "modern"})
            continue
        known_ids_by_print[pid].add(product_id)
        known_methods_by_print[pid].add(f"modern:{row.get('mapping_method') or 'unknown'}")

    for row in legacy_link_rows:
        pid = int(row["print_id"])
        product_id = str(row["external_id"] or "").strip()
        if not product_id:
            continue
        print_game = str(by_print.get(pid, {}).get("game") or "")
        if print_game and print_game != str(row["game"]):
            known_cross_game_conflicts.append({"print_id": pid, "print_game": print_game, "link_game": row["game"], "product_id": product_id, "source": "legacy"})
            continue
        known_ids_by_print[pid].add(product_id)
        known_methods_by_print[pid].add("legacy:print_identifier")

    # Learn Cardmarket Expansion -> one internal set only from distinct current idProducts.
    expansion_targets: dict[tuple[str, str], dict[tuple[int, str], set[str]]] = defaultdict(lambda: defaultdict(set))
    for pid, ids in known_ids_by_print.items():
        meta = by_print.get(pid)
        if not meta:
            continue
        game = str(meta["game"])
        catalog = products_by_game_id[game]
        for product_id in ids:
            product = catalog.get(product_id)
            if not product or not product.expansion_id:
                continue
            expansion_targets[(game, product.expansion_id)][(int(meta["set_id"]), str(meta["set_code"]))].add(product_id)

    trusted_expansions_by_set: dict[tuple[str, int], set[str]] = defaultdict(set)
    crosswalk_rows: list[dict] = []
    crosswalk_conflicts = 0
    for (game, expansion_id), targets in sorted(expansion_targets.items()):
        target_items = sorted(targets.items(), key=lambda item: (-len(item[1]), item[0][1]))
        if len(target_items) != 1:
            status = "CONFLICT_MULTIPLE_INTERNAL_SETS"
            crosswalk_conflicts += 1
            set_id = set_code = None
            support = sum(len(values) for values in targets.values())
        else:
            (set_id, set_code), product_ids = target_items[0]
            support = len(product_ids)
            if support >= max(1, args.min_expansion_samples):
                status = "TRUSTED"
                trusted_expansions_by_set[(game, int(set_id))].add(expansion_id)
            else:
                status = "INSUFFICIENT_EVIDENCE"
        crosswalk_rows.append({
            "game": game,
            "cardmarket_expansion_id": expansion_id,
            "status": status,
            "set_id": set_id if len(target_items) == 1 else "",
            "set_code": set_code if len(target_items) == 1 else "",
            "distinct_idProduct_support": support,
            "target_count": len(target_items),
            "targets_json": json.dumps([
                {"set_id": target[0], "set_code": target[1], "support": len(product_ids)}
                for target, product_ids in target_items
            ], sort_keys=True),
        })

    # Scryfall cardmarket_id is trusted for MTG only when one source id maps to one
    # canonical card/set/collector signature and exists in the current MTG catalog.
    mtg_source_id_by_print: dict[int, str] = {}
    mtg_source_signatures: dict[str, set[tuple[int, int, str]]] = defaultdict(set)
    for row in scryfall_attribute_rows:
        pid = int(row["print_id"])
        attrs = row.get("attributes_json") or {}
        if not isinstance(attrs, dict):
            continue
        product_id = str(attrs.get("cardmarket_id") or "").strip()
        meta = by_print.get(pid)
        if not product_id or not meta:
            continue
        mtg_source_id_by_print[pid] = product_id
        mtg_source_signatures[product_id].add((int(meta["card_id"]), int(meta["set_id"]), normalize_collector(meta["collector_number"])))
    safe_mtg_source_ids = {
        product_id
        for product_id, signatures in mtg_source_signatures.items()
        if len(signatures) == 1 and product_id in products_by_game_id["mtg"]
    }
    mtg_source_conflict_ids = {product_id for product_id, signatures in mtg_source_signatures.items() if len(signatures) > 1}
    mtg_source_absent_catalog_ids = {product_id for product_id in mtg_source_signatures if product_id not in products_by_game_id["mtg"]}

    # Group Cardmarket work by canonical commercial identity. Language and finish are
    # intentionally excluded: Cardmarket idProduct may span both dimensions. We never
    # merge/delete Prints; propagation is output only.
    groups: dict[tuple[str, int, int, str, str], list[dict]] = defaultdict(list)
    for row in print_rows:
        key = (
            str(row["game"]),
            int(row["card_id"]),
            int(row["set_id"]),
            normalize_collector(row["collector_number"]),
            normalize_text(row.get("variant") or "default"),
        )
        groups[key].append(row)

    canonical_by_game = Counter(str(row["game"]) for row in print_rows)
    pending_by_game = Counter()
    pending_print_ids: set[int] = set()
    for row in print_rows:
        pid = int(row["print_id"])
        if not _meaningful_snapshot(latest_price_by_print.get(pid)):
            pending_print_ids.add(pid)
            pending_by_game[str(row["game"])] += 1

    decision_rows: list[dict] = []
    proposed_rows: list[dict] = []
    gold_rows: list[dict] = []
    category_identity_counts = Counter()
    method_identity_counts = Counter()
    category_print_counts = Counter()
    by_game_category_identity: dict[str, Counter] = defaultdict(Counter)
    by_game_category_print: dict[str, Counter] = defaultdict(Counter)
    projected_price_rows_by_game = Counter()
    exact_resolved_rows_by_game = Counter()
    high_conf_rows_by_game = Counter()
    resolved_no_price_by_game = Counter()
    target_identity_count = 0

    all_identities: list[tuple[ResolverIdentity, tuple[str, ...], dict[int, dict]]] = []
    for key, items in sorted(groups.items(), key=lambda item: (GAME_ORDER[item[0][0]], item[0][2], item[0][3], item[0][1])):
        pending_items = [row for row in items if int(row["print_id"]) in pending_print_ids]
        if not pending_items:
            continue
        target_identity_count += 1
        first = items[0]
        group_print_ids = tuple(sorted(int(row["print_id"]) for row in items))
        group_pending_ids = tuple(sorted(int(row["print_id"]) for row in pending_items))
        known_ids = _uniq(product_id for pid in group_print_ids for product_id in sorted(known_ids_by_print.get(pid, set())))
        source_ids = _uniq(
            mtg_source_id_by_print.get(pid)
            for pid in group_print_ids
            if mtg_source_id_by_print.get(pid) in safe_mtg_source_ids
        )
        identity = ResolverIdentity(
            identity_key=f"{key[0]}:{key[1]}:{key[2]}:{key[3]}:{key[4]}",
            game=key[0],
            card_id=key[1],
            set_id=key[2],
            set_code=str(first["set_code"]),
            card_name=str(first["card_name"]),
            collector_number=str(first["collector_number"] or ""),
            variant=str(first.get("variant") or "default"),
            print_ids=group_print_ids,
            pending_print_ids=group_pending_ids,
            languages=_uniq(row.get("language") or "unknown" for row in items),
            finishes=_uniq("foil" if row.get("is_foil") else "nonfoil" for row in items),
            rarities=_uniq(row.get("rarity") or "" for row in items),
            known_product_ids=known_ids,
            mtg_source_product_ids=source_ids,
        )
        all_identities.append((identity, tuple(sorted(trusted_expansions_by_set.get((identity.game, identity.set_id), set()))), {int(row["print_id"]): row for row in items}))

    for identity, trusted_expansion_ids, item_by_pid in all_identities:
        decision = resolve_identity(
            identity,
            products_by_id=products_by_game_id[identity.game],
            products_by_expansion=products_by_game_expansion[identity.game],
            trusted_expansion_ids=trusted_expansion_ids,
            high_confidence_threshold=args.high_confidence_threshold,
            min_margin=args.min_margin,
        )
        pending_count = len(identity.pending_print_ids)
        category_identity_counts[decision.category] += 1
        method_identity_counts[decision.method] += 1
        category_print_counts[decision.category] += pending_count
        by_game_category_identity[identity.game][decision.category] += 1
        by_game_category_print[identity.game][decision.category] += pending_count

        price_projectable = 0
        if decision.product_id:
            price_row = price_rows_by_game.get(identity.game, {}).get(decision.product_id)
            for pid in identity.pending_print_ids:
                meta = item_by_pid[pid]
                has_price = _price_row_has_finish(price_row, is_foil=bool(meta.get("is_foil")))
                if has_price:
                    price_projectable += 1
                    projected_price_rows_by_game[identity.game] += 1
                else:
                    resolved_no_price_by_game[identity.game] += 1
                if decision.category == "EXACT":
                    exact_resolved_rows_by_game[identity.game] += 1
                elif decision.category == "UNIQUE_HIGH_CONFIDENCE":
                    high_conf_rows_by_game[identity.game] += 1
                proposed_rows.append({
                    "print_id": pid,
                    "identity_key": identity.identity_key,
                    "game": identity.game,
                    "card_name": identity.card_name,
                    "set_code": identity.set_code,
                    "collector_number": identity.collector_number,
                    "language": meta.get("language") or "",
                    "finish": "foil" if meta.get("is_foil") else "nonfoil",
                    "variant": meta.get("variant") or "default",
                    "category": decision.category,
                    "method": decision.method,
                    "idProduct": decision.product_id,
                    "cardmarket_url": cardmarket_redirect_url(identity.game, decision.product_id),
                    "price_guide_has_finish_price": str(bool(has_price)).lower(),
                    "future_auto_write_eligible": str(decision.category == "EXACT").lower(),
                    "review_required": str(decision.category != "EXACT").lower(),
                })

        decision_rows.append({
            "identity_key": identity.identity_key,
            "game": identity.game,
            "card_name": identity.card_name,
            "set_code": identity.set_code,
            "collector_number": identity.collector_number,
            "variant": identity.variant,
            "languages": " | ".join(identity.languages),
            "finishes": " | ".join(identity.finishes),
            "rarities": " | ".join(identity.rarities),
            "all_print_count": len(identity.print_ids),
            "pending_print_count": pending_count,
            "all_print_ids": " | ".join(map(str, identity.print_ids)),
            "pending_print_ids": " | ".join(map(str, identity.pending_print_ids)),
            "category": decision.category,
            "method": decision.method,
            "idProduct": decision.product_id or "",
            "cardmarket_url": cardmarket_redirect_url(identity.game, decision.product_id) if decision.product_id else "",
            "candidate_count": len(decision.candidate_ids),
            "candidate_ids": " | ".join(decision.candidate_ids),
            "score": "" if decision.score is None else decision.score,
            "second_score": "" if decision.second_score is None else decision.second_score,
            "trusted_expansion_ids": " | ".join(trusted_expansion_ids),
            "known_product_ids": " | ".join(identity.known_product_ids),
            "mtg_source_product_ids": " | ".join(identity.mtg_source_product_ids),
            "price_projectable_pending_prints": price_projectable,
            "evidence_json": json.dumps(decision.evidence, ensure_ascii=False, sort_keys=True),
        })

    # Gold-set shadow: hide known idProduct and ensure deterministic/source levels recover
    # the same identity. Expansion crosswalk is intentionally reused from the complete known
    # corpus in V1; this is reported explicitly and can later be upgraded to leave-one-out.
    gold_method_counts = Counter()
    gold_wrong_exact = 0
    gold_resolved = gold_correct = 0
    gold_by_game = defaultdict(lambda: Counter())
    for identity, trusted_expansion_ids, _ in all_identities:
        valid_known = tuple(pid for pid in identity.known_product_ids if pid in products_by_game_id[identity.game])
        if len(valid_known) != 1:
            continue
        expected = valid_known[0]
        shadow = replace(identity, known_product_ids=())
        prediction = resolve_identity(
            shadow,
            products_by_id=products_by_game_id[identity.game],
            products_by_expansion=products_by_game_expansion[identity.game],
            trusted_expansion_ids=trusted_expansion_ids,
            high_confidence_threshold=args.high_confidence_threshold,
            min_margin=args.min_margin,
        )
        correct = bool(prediction.product_id and prediction.product_id == expected)
        if prediction.resolved:
            gold_resolved += 1
            if correct:
                gold_correct += 1
            if prediction.category == "EXACT" and not correct:
                gold_wrong_exact += 1
        gold_method_counts[prediction.method] += 1
        gold_by_game[identity.game]["total"] += 1
        gold_by_game[identity.game]["resolved"] += int(prediction.resolved)
        gold_by_game[identity.game]["correct"] += int(correct)
        gold_by_game[identity.game]["wrong_exact"] += int(prediction.category == "EXACT" and not correct)
        gold_rows.append({
            "identity_key": identity.identity_key,
            "game": identity.game,
            "expected_idProduct": expected,
            "predicted_idProduct": prediction.product_id or "",
            "category": prediction.category,
            "method": prediction.method,
            "correct": str(correct).lower(),
            "candidate_ids": " | ".join(prediction.candidate_ids),
        })

    decision_rows.sort(key=lambda row: (GAME_ORDER[row["game"]], row["category"], row["set_code"], row["collector_number"], row["card_name"]))
    proposed_rows.sort(key=lambda row: (GAME_ORDER[row["game"]], int(row["print_id"])))
    gold_rows.sort(key=lambda row: (GAME_ORDER[row["game"]], row["identity_key"]))

    decision_fields = [
        "identity_key", "game", "card_name", "set_code", "collector_number", "variant", "languages", "finishes", "rarities",
        "all_print_count", "pending_print_count", "all_print_ids", "pending_print_ids", "category", "method", "idProduct",
        "cardmarket_url", "candidate_count", "candidate_ids", "score", "second_score", "trusted_expansion_ids",
        "known_product_ids", "mtg_source_product_ids", "price_projectable_pending_prints", "evidence_json",
    ]
    proposed_fields = [
        "print_id", "identity_key", "game", "card_name", "set_code", "collector_number", "language", "finish", "variant",
        "category", "method", "idProduct", "cardmarket_url", "price_guide_has_finish_price", "future_auto_write_eligible", "review_required",
    ]
    crosswalk_fields = ["game", "cardmarket_expansion_id", "status", "set_id", "set_code", "distinct_idProduct_support", "target_count", "targets_json"]
    gold_fields = ["identity_key", "game", "expected_idProduct", "predicted_idProduct", "category", "method", "correct", "candidate_ids"]

    _write_csv(args.outdir / "decisions.csv", decision_rows, decision_fields)
    _write_csv(args.outdir / "proposed_print_links.csv", proposed_rows, proposed_fields)
    _write_csv(args.outdir / "expansion_crosswalk.csv", crosswalk_rows, crosswalk_fields)
    _write_csv(args.outdir / "goldset_results.csv", gold_rows, gold_fields)
    _write_csv(args.outdir / "ambiguous.csv", [row for row in decision_rows if row["category"] == "AMBIGUOUS"], decision_fields)
    _write_csv(args.outdir / "unmatched.csv", [row for row in decision_rows if row["category"] == "UNMATCHED"], decision_fields)

    pending_total = len(pending_print_ids)
    resolved_exact_rows = sum(exact_resolved_rows_by_game.values())
    review_rows = sum(high_conf_rows_by_game.values())
    projected_price_rows = sum(projected_price_rows_by_game.values())
    resolved_no_price_rows = sum(resolved_no_price_by_game.values())
    summary = {
        "status": "pass" if gold_wrong_exact == 0 else "failed",
        "mode": "READ_ONLY",
        "production_writes": 0,
        "transaction_read_only": transaction_read_only,
        "resolver_git_sha": os.getenv("GITHUB_SHA"),
        "grouping_contract": "card + set/region + collector + variant; language and finish excluded from Cardmarket identity work key; canonical Prints are never merged or deleted",
        "auto_write_policy": "V1 writes nothing. Future apply eligibility is EXACT only after certification; UNIQUE_HIGH_CONFIDENCE always requires review; AMBIGUOUS/UNMATCHED never auto-write.",
        "catalogs": catalog_manifest,
        "price_guides": price_manifest,
        "canonical_prints": len(print_rows),
        "canonical_prints_by_game": dict(sorted(canonical_by_game.items())),
        "pending_missing_current_cardmarket_price": pending_total,
        "pending_missing_current_cardmarket_price_by_game": dict(sorted(pending_by_game.items())),
        "resolver_identities": target_identity_count,
        "deduplicated_pending_rows": pending_total - target_identity_count,
        "identity_results": dict(sorted(category_identity_counts.items())),
        "print_row_results": dict(sorted(category_print_counts.items())),
        "method_results": dict(sorted(method_identity_counts.items())),
        "identity_results_by_game": {game: dict(sorted(counter.items())) for game, counter in sorted(by_game_category_identity.items())},
        "print_results_by_game": {game: dict(sorted(counter.items())) for game, counter in sorted(by_game_category_print.items())},
        "exact_resolvable_pending_print_rows": resolved_exact_rows,
        "exact_resolvable_pending_print_rows_by_game": dict(sorted(exact_resolved_rows_by_game.items())),
        "high_confidence_review_pending_print_rows": review_rows,
        "high_confidence_review_pending_print_rows_by_game": dict(sorted(high_conf_rows_by_game.items())),
        "projected_price_recoverable_pending_print_rows": projected_price_rows,
        "projected_price_recoverable_pending_print_rows_by_game": dict(sorted(projected_price_rows_by_game.items())),
        "resolved_identity_but_no_price_for_finish_rows": resolved_no_price_rows,
        "resolved_identity_but_no_price_for_finish_rows_by_game": dict(sorted(resolved_no_price_by_game.items())),
        "trusted_expansion_crosswalks": sum(1 for row in crosswalk_rows if row["status"] == "TRUSTED"),
        "expansion_crosswalk_conflicts": crosswalk_conflicts,
        "known_cross_game_conflicts": len(known_cross_game_conflicts),
        "mtg_source_conflicting_signature_ids": len(mtg_source_conflict_ids),
        "mtg_source_ids_absent_current_catalog": len(mtg_source_absent_catalog_ids),
        "goldset": {
            "total": len(gold_rows),
            "resolved": gold_resolved,
            "correct": gold_correct,
            "resolved_precision": round(gold_correct / gold_resolved, 6) if gold_resolved else None,
            "wrong_exact": gold_wrong_exact,
            "crosswalk_note": "V1 shadow validation reuses crosswalk learned from the full known corpus; not leave-one-out.",
            "by_game": {game: dict(counter) for game, counter in sorted(gold_by_game.items())},
            "methods": dict(sorted(gold_method_counts.items())),
        },
        "forbidden_mismatches": {
            "duplicate_catalog_idProduct": 0,
            "wrong_exact_gold_predictions": gold_wrong_exact,
            "cross_game_resolver_writes": 0,
        },
    }

    manifest = {
        "resolver": "cardmarket-identity-resolver-v1",
        "resolver_git_sha": os.getenv("GITHUB_SHA"),
        "mode": "READ_ONLY",
        "production_writes_enabled": False,
        "database_transaction_read_only": transaction_read_only,
        "catalogs": catalog_manifest,
        "price_guides": price_manifest,
        "output_files": {
            name: _sha256(args.outdir / name)
            for name in ("decisions.csv", "proposed_print_links.csv", "expansion_crosswalk.csv", "goldset_results.csv", "ambiguous.csv", "unmatched.csv")
        },
    }
    (args.outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["output_files"]["summary.json"] = _sha256(args.outdir / "summary.json")
    (args.outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if known_cross_game_conflicts:
        (args.outdir / "known_cross_game_conflicts.json").write_text(json.dumps(known_cross_game_conflicts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if gold_wrong_exact:
        print(f"REFUSED CERTIFICATION: {gold_wrong_exact} EXACT gold-set predictions disagree with known idProduct.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
