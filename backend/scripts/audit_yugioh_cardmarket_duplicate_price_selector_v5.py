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


def norm_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def norm_rarity(value: str | None) -> str:
    text = norm_text(value)
    if text.endswith("rare") and text != "rare":
        text = text[:-4]
    return text


def positive(value) -> bool:
    if value is None:
        return False
    try:
        return float(value) > 0
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Read-only backtest of current Cardmarket Price Guide presence as a selector "
            "between YGO products that otherwise resolve to the same physical Print."
        )
    )
    ap.add_argument("expansion_cert", type=Path)
    ap.add_argument("duplicate_diagnostic", type=Path)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--sample-limit", type=int, default=200)
    args = ap.parse_args()

    cert = json.loads(args.expansion_cert.read_text(encoding="utf-8"))
    diag = json.loads(args.duplicate_diagnostic.read_text(encoding="utf-8"))
    if cert.get("mode") != "read_only_certification" or cert.get("game") != "yugioh":
        raise SystemExit("Expected read-only YGO expansion certification")
    if diag.get("mode") != "read_only" or diag.get("game") != "yugioh":
        raise SystemExit("Expected read-only YGO duplicate diagnostic")
    if int((cert.get("summary") or {}).get("production_writes", -1)) != 0:
        raise SystemExit("Expansion certification is not read-only")
    if int((diag.get("summary") or {}).get("production_writes", -1)) != 0:
        raise SystemExit("Duplicate diagnostic is not read-only")

    expansion_to_pid = {
        str(row["cardmarket_expansion_id"]): str(row["konami_pid"])
        for row in cert.get("certified_expansions") or []
    }

    duplicate_target_claimants = {
        int(sample["print_id"]): [int(c["external_product_id"]) for c in sample["claimants"]]
        for sample in diag.get("samples") or []
    }
    # Samples may be capped, so reconstruct target ids/products from database for the
    # current residual rather than relying on this map for production counts.

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
                       metacard_external_id, date_added
                FROM external_catalog_products
                WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                  AND expansion_external_id IS NOT NULL
                  AND trim(expansion_external_id)<>''
                """,
                (gid,),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT pr.print_id, cr.external_id AS konami_pid,
                       c.name AS card_name, p.rarity, p.set_id, c.id AS card_id
                FROM print_releases pr
                JOIN catalog_releases cr ON cr.id=pr.release_id
                JOIN prints p ON p.id=pr.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE cr.game_id=%s AND cr.source='konami_neuron'
                  AND cr.external_id IS NOT NULL AND trim(cr.external_id)<>''
                """,
                (gid,),
            )
            official = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id, l.print_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.confidence='exact' AND l.link_status IN ('accepted','mapped','exact')
                """,
                (gid,),
            )
            exact_links = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT max(s.as_of) AS latest
                FROM external_market_price_snapshots s
                JOIN external_catalog_products e ON e.id=s.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND s.currency='EUR'
                """,
                (gid,),
            )
            latest = cur.fetchone()["latest"]
            if latest is None:
                raise SystemExit("No current YGO Cardmarket external price capture")

            cur.execute(
                """
                SELECT s.external_product_id, s.price_variant,
                       s.price_low, s.price_mid, s.price_market, s.price_last
                FROM external_market_price_snapshots s
                JOIN external_catalog_products e ON e.id=s.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND s.currency='EUR' AND s.as_of=%s
                """,
                (gid, latest),
            )
            price_rows = [dict(r) for r in cur.fetchall()]

        product_by_id = {int(r["id"]): r for r in products}
        current_price_products: set[int] = set()
        price_variants_by_product: dict[int, list[str]] = defaultdict(list)
        for row in price_rows:
            product_id = int(row["external_product_id"])
            if any(positive(row.get(k)) for k in ("price_low", "price_mid", "price_market", "price_last")):
                current_price_products.add(product_id)
                price_variants_by_product[product_id].append(str(row.get("price_variant") or ""))

        official_by_pid_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in official:
            pid = str(row.get("konami_pid") or "").strip()
            name = norm_text(row.get("card_name"))
            rarity = norm_rarity(row.get("rarity"))
            if pid and name and rarity:
                row = dict(row)
                row["norm_rarity"] = rarity
                official_by_pid_name[(pid, name)].append(row)

        # Eligible deterministic products are exactly the V4 single-product / single-rarity shape.
        by_expansion_group: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in products:
            exp = str(row.get("expansion_external_id") or "")
            if exp not in expansion_to_pid:
                continue
            by_expansion_group[(exp, str(row.get("metacard_external_id") or ""), norm_text(row.get("name")))].append(row)

        eligible_target_by_product: dict[int, int] = {}
        for (exp, _meta, name), rows in by_expansion_group.items():
            if len(rows) != 1 or not name:
                continue
            pid = expansion_to_pid[exp]
            official_rows = official_by_pid_name.get((pid, name), [])
            rarities = {r["norm_rarity"] for r in official_rows}
            print_ids = {int(r["print_id"]) for r in official_rows}
            if len(rarities) == 1 and len(print_ids) == 1:
                eligible_target_by_product[int(rows[0]["id"])] = next(iter(print_ids))

        candidates_by_target: dict[int, list[int]] = defaultdict(list)
        for product_id, print_id in eligible_target_by_product.items():
            candidates_by_target[print_id].append(product_id)

        exact_by_target: dict[int, set[int]] = defaultdict(set)
        for row in exact_links:
            exact_by_target[int(row["print_id"])].add(int(row["external_product_id"]))

        # Backtest only where one exact product is known and at least one alternate eligible
        # Cardmarket product independently resolves to the same target.
        backtest = []
        for print_id, candidate_ids in candidates_by_target.items():
            candidate_ids = sorted(set(candidate_ids))
            if len(candidate_ids) < 2:
                continue
            exact_ids = exact_by_target.get(print_id, set()) & set(candidate_ids)
            if len(exact_ids) != 1:
                continue
            exact_id = next(iter(exact_ids))
            priced_ids = [pid for pid in candidate_ids if pid in current_price_products]
            unique_price_pick = priced_ids[0] if len(priced_ids) == 1 else None
            newest_pick = max(
                candidate_ids,
                key=lambda pid: (str(product_by_id[pid].get("date_added") or ""), int(str(product_by_id[pid].get("external_id") or 0) or 0)),
            )
            oldest_pick = min(
                candidate_ids,
                key=lambda pid: (str(product_by_id[pid].get("date_added") or "9999-99-99"), int(str(product_by_id[pid].get("external_id") or 0) or 0)),
            )
            backtest.append({
                "print_id": print_id,
                "candidate_ids": candidate_ids,
                "exact_product_id": exact_id,
                "priced_ids": priced_ids,
                "unique_price_pick": unique_price_pick,
                "unique_price_correct": unique_price_pick == exact_id if unique_price_pick is not None else None,
                "newest_pick": newest_pick,
                "newest_correct": newest_pick == exact_id,
                "oldest_pick": oldest_pick,
                "oldest_correct": oldest_pick == exact_id,
            })

        unique_price_predictable = [r for r in backtest if r["unique_price_pick"] is not None]
        unique_price_correct = sum(1 for r in unique_price_predictable if r["unique_price_correct"])
        newest_correct = sum(1 for r in backtest if r["newest_correct"])
        oldest_correct = sum(1 for r in backtest if r["oldest_correct"])

        # Current unresolved duplicate targets from all eligible deterministic products.
        residual_targets = {
            print_id: sorted(ids)
            for print_id, ids in candidates_by_target.items()
            if len(set(ids)) > 1 and not exact_by_target.get(print_id)
        }
        residual_classes = Counter()
        residual_unique_price_candidates = []
        for print_id, ids in sorted(residual_targets.items()):
            priced = [pid for pid in ids if pid in current_price_products]
            if len(priced) == 1:
                residual_classes["unique_current_price"] += 1
                pick = priced[0]
                residual_unique_price_candidates.append({
                    "print_id": print_id,
                    "candidate_product_ids": ids,
                    "selected_external_product_id": pick,
                    "selected_idProduct": str(product_by_id[pick].get("external_id") or ""),
                    "selected_expansion": str(product_by_id[pick].get("expansion_external_id") or ""),
                    "selected_date_added": str(product_by_id[pick].get("date_added") or ""),
                    "price_variants": price_variants_by_product.get(pick, []),
                    "claimants": [
                        {
                            "external_product_id": pid,
                            "idProduct": str(product_by_id[pid].get("external_id") or ""),
                            "expansion": str(product_by_id[pid].get("expansion_external_id") or ""),
                            "date_added": str(product_by_id[pid].get("date_added") or ""),
                            "has_current_price": pid in current_price_products,
                        }
                        for pid in ids
                    ],
                })
            elif len(priced) == 0:
                residual_classes["no_current_price"] += 1
            else:
                residual_classes["multiple_current_prices"] += 1

        summary = {
            "latest_price_as_of": latest.isoformat(),
            "eligible_parallel_backtest_targets": len(backtest),
            "unique_current_price_predictable": len(unique_price_predictable),
            "unique_current_price_correct": unique_price_correct,
            "unique_current_price_wrong": len(unique_price_predictable) - unique_price_correct,
            "unique_current_price_precision": (unique_price_correct / len(unique_price_predictable)) if unique_price_predictable else None,
            "newest_selector_correct": newest_correct,
            "newest_selector_wrong": len(backtest) - newest_correct,
            "newest_selector_precision": (newest_correct / len(backtest)) if backtest else None,
            "oldest_selector_correct": oldest_correct,
            "oldest_selector_wrong": len(backtest) - oldest_correct,
            "oldest_selector_precision": (oldest_correct / len(backtest)) if backtest else None,
            "residual_duplicate_targets": len(residual_targets),
            "residual_unique_current_price": residual_classes["unique_current_price"],
            "residual_multiple_current_prices": residual_classes["multiple_current_prices"],
            "residual_no_current_price": residual_classes["no_current_price"],
            "production_writes": 0,
        }
        payload = {
            "mode": "read_only",
            "game": "yugioh",
            "selector": "unique_current_cardmarket_price_v5",
            "summary": summary,
            "backtest_samples": backtest[: args.sample_limit],
            "unique_price_wrong_samples": [r for r in unique_price_predictable if not r["unique_price_correct"]][: args.sample_limit],
            "residual_unique_price_candidates": residual_unique_price_candidates,
        }
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("YGO_DUPLICATE_PRICE_SELECTOR_V5=" + json.dumps(summary, separators=(",", ":")))
        conn.rollback()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
