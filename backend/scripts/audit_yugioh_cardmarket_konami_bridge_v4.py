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


ACCEPTED_STATUSES = {"accepted", "mapped", "exact"}


def norm_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def norm_rarity(value: str | None) -> str:
    text = norm_text(value)
    if text.endswith("rare") and text != "rare":
        text = text[:-4]
    return text


def numkey(value: str | None):
    raw = str(value or "")
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Read-only YGO Cardmarket/Konami individual-product resolver V4. "
            "Consumes a certified expansion-composition report and resolves only "
            "single-rarity groups or unique elimination from existing exact siblings."
        )
    )
    ap.add_argument("expansion_cert_report", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--sample-limit", type=int, default=200)
    args = ap.parse_args()

    cert = json.loads(args.expansion_cert_report.read_text(encoding="utf-8"))
    cert_summary = cert.get("summary") or {}
    if cert.get("mode") != "read_only_certification" or cert.get("game") != "yugioh":
        raise SystemExit("Expected a read-only YGO V4 expansion certification report")
    if cert_summary.get("certification_passed") is not True:
        raise SystemExit("Expansion V4 certification did not pass")
    if int(cert_summary.get("production_writes", -1)) != 0:
        raise SystemExit("Expansion V4 certification is not read-only")
    if int(cert_summary.get("fixed_profile_gold_wrong", -1)) != 0:
        raise SystemExit("Expansion V4 certification contains gold errors")

    gold_bridge = {
        str(row["cardmarket_expansion_id"]): str(row["konami_pid"])
        for row in cert.get("certified_gold_expansions") or []
    }
    new_bridge = {
        str(row["cardmarket_expansion_id"]): str(row["konami_pid"])
        for row in cert.get("certified_new_expansions") or []
    }
    bridge_evidence = {
        str(row["cardmarket_expansion_id"]): row
        for row in cert.get("certified_expansions") or []
    }
    if len(gold_bridge) < 200 or not new_bridge:
        raise SystemExit("Insufficient certified expansion bridge coverage")

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
                       name, metacard_external_id
                FROM external_catalog_products
                WHERE source='cardmarket'
                  AND game_id=%s
                  AND product_group='single'
                  AND expansion_external_id IS NOT NULL
                  AND trim(expansion_external_id)<>''
                """,
                (game_id,),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id, l.print_id, l.link_status, l.confidence,
                       l.mapping_method, p.rarity AS linked_rarity
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket'
                  AND e.game_id=%s
                  AND e.product_group='single'
                """,
                (game_id,),
            )
            links = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid,
                       c.id AS card_id, c.name AS card_name,
                       p.set_id, p.collector_number, p.rarity, p.variant, p.language,
                       s.code AS set_code
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE cr.game_id=%s
                  AND cr.source='konami_neuron'
                  AND cr.external_id IS NOT NULL
                  AND trim(cr.external_id)<>''
                """,
                (game_id,),
            )
            official = [dict(r) for r in cur.fetchall()]

        product_by_id = {int(row["external_product_id"]): row for row in products}
        groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in products:
            expansion = str(row.get("expansion_external_id") or "").strip()
            if not expansion:
                continue
            key = (
                expansion,
                str(row.get("metacard_external_id") or ""),
                norm_text(row.get("name")),
            )
            groups[key].append(row)

        exact_rarities_by_product: dict[int, set[str]] = defaultdict(set)
        exact_prints_by_product: dict[int, set[int]] = defaultdict(set)
        exact_products: set[int] = set()
        all_links_by_product: dict[int, list[dict]] = defaultdict(list)
        exact_product_by_print: dict[int, set[int]] = defaultdict(set)
        for row in links:
            product_id = int(row["external_product_id"])
            all_links_by_product[product_id].append(row)
            if row.get("confidence") != "exact" or str(row.get("link_status")) not in ACCEPTED_STATUSES:
                continue
            rarity = norm_rarity(row.get("linked_rarity"))
            print_id = int(row["print_id"])
            exact_products.add(product_id)
            exact_prints_by_product[product_id].add(print_id)
            exact_product_by_print[print_id].add(product_id)
            if rarity:
                exact_rarities_by_product[product_id].add(rarity)

        official_rarities: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        official_targets: dict[str, dict[str, dict[str, set[int]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(set))
        )
        print_meta: dict[int, dict] = {}
        for row in official:
            pid = str(row.get("konami_pid") or "").strip()
            name_key = norm_text(row.get("card_name"))
            rarity = norm_rarity(row.get("rarity"))
            if not pid or not name_key or not rarity:
                continue
            print_id = int(row["print_id"])
            official_rarities[pid][name_key].add(rarity)
            official_targets[pid][name_key][rarity].add(print_id)
            print_meta[print_id] = {
                "print_id": print_id,
                "card_id": int(row["card_id"]),
                "set_id": int(row["set_id"]),
                "card_name": row.get("card_name"),
                "collector_number": row.get("collector_number"),
                "rarity": row.get("rarity"),
                "variant": row.get("variant"),
                "language": row.get("language"),
                "set_code": row.get("set_code"),
            }

        # Masked backtest over the fixed-profile gold expansions. Each known exact
        # product is hidden in turn. The resolver may use only deterministic
        # single-rarity identity or exact siblings left visible in the group.
        backtest_predictable = backtest_correct = backtest_wrong = 0
        backtest_methods = Counter()
        wrong_samples = []
        for group_key, plist in groups.items():
            expansion, _, name_key = group_key
            pid = gold_bridge.get(expansion)
            if not pid or not name_key:
                continue
            ordered = sorted(plist, key=lambda row: numkey(row.get("external_id")))
            canonical = set(official_rarities[pid].get(name_key, set()))
            if not canonical:
                continue
            for hidden in ordered:
                hidden_id = int(hidden["external_product_id"])
                actual_set = exact_rarities_by_product.get(hidden_id, set())
                if len(actual_set) != 1:
                    continue
                actual = next(iter(actual_set))
                predicted = None
                method = None
                if len(ordered) == 1 and len(canonical) == 1:
                    predicted = next(iter(canonical))
                    method = "single_product_single_rarity"
                elif len(canonical) == len(ordered):
                    visible = set()
                    valid = True
                    for sibling in ordered:
                        sibling_id = int(sibling["external_product_id"])
                        if sibling_id == hidden_id:
                            continue
                        rarities = exact_rarities_by_product.get(sibling_id, set())
                        if len(rarities) != 1:
                            valid = False
                            break
                        rarity = next(iter(rarities))
                        if rarity not in canonical or rarity in visible:
                            valid = False
                            break
                        visible.add(rarity)
                    remaining = canonical - visible
                    if valid and len(remaining) == 1 and len(visible) == len(ordered) - 1:
                        predicted = next(iter(remaining))
                        method = "unique_rarity_elimination_from_exact_siblings"
                if predicted is None:
                    continue
                backtest_predictable += 1
                backtest_methods[method] += 1
                if predicted == actual:
                    backtest_correct += 1
                else:
                    backtest_wrong += 1
                    if len(wrong_samples) < args.sample_limit:
                        wrong_samples.append(
                            {
                                "expansion": expansion,
                                "idProduct": str(hidden.get("external_id")),
                                "name": hidden.get("name"),
                                "konami_pid": pid,
                                "method": method,
                                "predicted_rarity": predicted,
                                "actual_rarity": actual,
                            }
                        )

        unresolved = Counter()
        candidates: list[dict] = []
        candidate_targets: dict[int, set[int]] = defaultdict(set)

        for group_key, plist in groups.items():
            expansion, _, name_key = group_key
            pid = new_bridge.get(expansion)
            if not pid or not name_key:
                continue
            ordered = sorted(plist, key=lambda row: numkey(row.get("external_id")))
            canonical = set(official_rarities[pid].get(name_key, set()))
            if not canonical:
                unresolved["no_official_name_in_certified_release"] += 1
                continue

            unknown = [row for row in ordered if int(row["external_product_id"]) not in exact_products]
            if not unknown:
                continue

            assignments: dict[int, tuple[str, str]] = {}
            if len(ordered) == 1 and len(canonical) == 1:
                row = ordered[0]
                assignments[int(row["external_product_id"])] = (
                    next(iter(canonical)),
                    "single_product_single_rarity",
                )
            elif len(canonical) == len(ordered) and len(unknown) == 1:
                used = set()
                valid = True
                for row in ordered:
                    product_id = int(row["external_product_id"])
                    if product_id in {int(u["external_product_id"]) for u in unknown}:
                        continue
                    rarities = exact_rarities_by_product.get(product_id, set())
                    if len(rarities) != 1:
                        valid = False
                        break
                    rarity = next(iter(rarities))
                    if rarity not in canonical or rarity in used:
                        valid = False
                        break
                    used.add(rarity)
                remaining = canonical - used
                if valid and len(remaining) == 1 and len(used) == len(ordered) - 1:
                    product_id = int(unknown[0]["external_product_id"])
                    assignments[product_id] = (
                        next(iter(remaining)),
                        "unique_rarity_elimination_from_exact_siblings",
                    )
            if not assignments:
                unresolved["group_not_deterministically_resolved"] += 1
                continue

            for product_id, (rarity, method) in assignments.items():
                product = product_by_id[product_id]
                if all_links_by_product.get(product_id):
                    unresolved["external_product_has_existing_link"] += 1
                    continue
                target_ids = set(official_targets[pid][name_key][rarity])
                if not target_ids:
                    unresolved["assigned_rarity_has_no_official_target"] += 1
                    continue
                metas = [print_meta[print_id] for print_id in sorted(target_ids)]
                card_ids = {meta["card_id"] for meta in metas}
                set_ids = {meta["set_id"] for meta in metas}
                if len(card_ids) != 1:
                    unresolved["target_spans_multiple_card_ids"] += 1
                    continue
                if len(set_ids) != 1:
                    unresolved["target_spans_multiple_set_ids"] += 1
                    continue
                conflicting_targets = {
                    print_id
                    for print_id in target_ids
                    if exact_product_by_print.get(print_id)
                }
                if conflicting_targets:
                    unresolved["target_print_already_exact"] += 1
                    continue
                row = {
                    "external_product_id": product_id,
                    "idProduct": str(product.get("external_id")),
                    "name": product.get("name"),
                    "expansion": expansion,
                    "konami_pid": pid,
                    "rarity": rarity,
                    "method": method,
                    "target_print_ids": sorted(target_ids),
                    "target_prints": metas,
                    "expansion_evidence": bridge_evidence.get(expansion) or {},
                }
                candidates.append(row)
                for print_id in target_ids:
                    candidate_targets[print_id].add(product_id)

        duplicate_targets = {
            print_id: sorted(product_ids)
            for print_id, product_ids in candidate_targets.items()
            if len(product_ids) > 1
        }
        safe_candidates = [
            row
            for row in candidates
            if not any(print_id in duplicate_targets for print_id in row["target_print_ids"])
        ]
        safe_link_rows = sum(len(row["target_print_ids"]) for row in safe_candidates)

        summary = {
            "certified_gold_expansions": len(gold_bridge),
            "certified_new_expansions": len(new_bridge),
            "existing_exact_products": len(exact_products),
            "masked_backtest_predictable": backtest_predictable,
            "masked_backtest_correct": backtest_correct,
            "masked_backtest_wrong": backtest_wrong,
            "masked_backtest_precision": (backtest_correct / backtest_predictable) if backtest_predictable else None,
            "candidate_external_products_before_target_guard": len(candidates),
            "duplicate_target_prints": len(duplicate_targets),
            "safe_candidate_external_products": len(safe_candidates),
            "safe_candidate_link_rows": safe_link_rows,
            "certification_ready": (
                backtest_predictable >= 100
                and backtest_wrong == 0
                and len(safe_candidates) > 0
            ),
            "production_writes": 0,
        }

        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "resolver": "cardmarket_konami_bridge_v4_deterministic",
            "summary": summary,
            "masked_backtest_methods": dict(sorted(backtest_methods.items())),
            "masked_backtest_wrong_samples": wrong_samples,
            "unresolved": dict(sorted(unresolved.items())),
            "duplicate_targets": duplicate_targets,
            "safe_candidates": safe_candidates,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("YGO_KONAMI_BRIDGE_V4=" + json.dumps(summary, separators=(",", ":")))
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
