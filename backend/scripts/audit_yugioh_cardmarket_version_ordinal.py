#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def numkey(value: str):
    raw = str(value or "")
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only backtest of YGO Cardmarket product ordinal vs canonical rarity.")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--min-support", type=int, default=3)
    args = ap.parse_args()
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
                SELECT ecp.id AS external_product_id, ecp.external_id, ecp.expansion_external_id,
                       ecp.name, ecp.metacard_external_id,
                       l.print_id, p.rarity, p.variant, p.collector_number, s.code AS set_code
                FROM external_catalog_products ecp
                LEFT JOIN external_catalog_print_links l
                  ON l.external_product_id=ecp.id
                 AND l.confidence='exact' AND l.link_status IN ('accepted','mapped')
                LEFT JOIN prints p ON p.id=l.print_id
                LEFT JOIN sets s ON s.id=p.set_id
                WHERE ecp.source='cardmarket' AND ecp.game_id=%s AND ecp.product_group='single'
                ORDER BY ecp.expansion_external_id, lower(ecp.name), ecp.external_id, l.print_id
                """,
                (game_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        groups = defaultdict(lambda: defaultdict(lambda: {"links": []}))
        for r in rows:
            group_key = (
                str(r.get("expansion_external_id") or ""),
                str(r.get("metacard_external_id") or ""),
                str(r.get("name") or "").casefold(),
            )
            pk = int(r["external_product_id"])
            product = groups[group_key][pk]
            product["external_product_id"] = pk
            product["idProduct"] = str(r["external_id"])
            if r.get("print_id") is not None:
                product["links"].append(
                    {
                        "print_id": int(r["print_id"]),
                        "rarity": str(r.get("rarity") or ""),
                        "variant": str(r.get("variant") or ""),
                        "collector_number": str(r.get("collector_number") or ""),
                        "set_code": str(r.get("set_code") or ""),
                    }
                )

        observations = []
        expansion_counts = defaultdict(lambda: defaultdict(Counter))
        group_counts = defaultdict(lambda: defaultdict(Counter))
        for group_key, product_map in groups.items():
            expansion = group_key[0]
            products = sorted(product_map.values(), key=lambda p: numkey(p["idProduct"]))
            if len(products) < 2:
                continue
            product_count = len(products)
            for ordinal, product in enumerate(products, start=1):
                rarities = {x["rarity"] for x in product["links"] if x["rarity"]}
                variants = {x["variant"] for x in product["links"] if x["variant"]}
                if len(rarities) != 1:
                    continue
                rarity = next(iter(rarities))
                slot = (product_count, ordinal)
                expansion_counts[expansion][slot][rarity] += 1
                group_counts[group_key][slot][rarity] += 1
                observations.append(
                    {
                        "group_key": group_key,
                        "expansion": expansion,
                        "product_count": product_count,
                        "ordinal": ordinal,
                        "idProduct": product["idProduct"],
                        "rarity": rarity,
                        "variant": next(iter(variants)) if len(variants) == 1 else None,
                    }
                )

        def rule(counter: Counter):
            total = sum(counter.values())
            if total < args.min_support:
                return None
            ranked = counter.most_common()
            if len(ranked) != 1:
                return None
            winner, support = ranked[0]
            if support != total:
                return None
            return winner

        full_rules = []
        for expansion, slots in sorted(expansion_counts.items()):
            for (product_count, ordinal), counts in sorted(slots.items()):
                winner = rule(counts)
                if winner is not None:
                    full_rules.append(
                        {
                            "expansion": expansion,
                            "product_count": product_count,
                            "ordinal": ordinal,
                            "rarity": winner,
                            "support": sum(counts.values()),
                        }
                    )

        # True leave-one-card-group-out validation. For each known observation,
        # remove every observation from that card/metacard group before learning
        # the expansion/product-count/ordinal rule.
        loo_predictable = loo_correct = loo_wrong = 0
        loo_wrong_samples = []
        for obs in observations:
            slot = (obs["product_count"], obs["ordinal"])
            training = expansion_counts[obs["expansion"]][slot].copy()
            heldout = group_counts[obs["group_key"]][slot]
            training.subtract(heldout)
            training += Counter()  # drop zero/negative keys
            predicted = rule(training)
            if predicted is None:
                continue
            loo_predictable += 1
            if predicted == obs["rarity"]:
                loo_correct += 1
            else:
                loo_wrong += 1
                if len(loo_wrong_samples) < 50:
                    loo_wrong_samples.append(
                        {
                            "expansion": obs["expansion"],
                            "product_count": obs["product_count"],
                            "ordinal": obs["ordinal"],
                            "idProduct": obs["idProduct"],
                            "actual": obs["rarity"],
                            "predicted": predicted,
                            "training_support": sum(training.values()),
                        }
                    )

        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "validation": "leave_one_card_group_out",
            "min_support": args.min_support,
            "summary": {
                "mapped_multi_product_observations": len(observations),
                "perfect_full_expansion_slot_rules": len(full_rules),
                "loo_predictable_existing_observations": loo_predictable,
                "loo_correct_existing_observations": loo_correct,
                "loo_wrong_existing_observations": loo_wrong,
                "loo_precision": round(loo_correct / loo_predictable, 8) if loo_predictable else None,
                "write_rule_ready": loo_predictable > 0 and loo_wrong == 0,
            },
            "perfect_full_expansion_slot_rules": full_rules,
            "loo_wrong_samples": loo_wrong_samples,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("YGO_VERSION_ORDINAL_LOO=" + json.dumps(payload["summary"], separators=(",", ":")))
        conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
