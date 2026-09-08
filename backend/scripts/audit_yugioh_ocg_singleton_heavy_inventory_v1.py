from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
METHOD = "cardmarket_ocg_certified_singleton_heavy_cohort_v1"
EXPECTED_ROWS = 445
ACCEPTED = ("accepted", "mapped", "exact")

# These key names are inventoried only as *potential* independent anchors.
# No historical print field is used to derive or certify a mapping in this audit.
ANCHOR_KEY_FAMILIES = {
    "set": {
        "set_code", "setcode", "canonical_set_code", "source_set_code",
        "expansion_code", "cardmarket_expansion_code",
    },
    "card": {
        "card_id", "canonical_card_id", "source_card_id", "oracle_card_id",
    },
    "collector": {
        "collector_number", "collector", "number", "card_number", "print_number",
    },
    "rarity": {
        "rarity", "canonical_rarity", "contract_rarity", "source_rarity",
    },
    "variant": {
        "variant", "version", "printing", "finish", "foil", "edition",
    },
    "ordinal": {
        "product_ordinal", "ordinal", "source_ordinal", "cardmarket_product_ordinal",
    },
    "expansion": {
        "idexpansion", "expansion_external_id", "cardmarket_expansion_id",
    },
    "metacard": {
        "idmetacard", "metacard_external_id", "cardmarket_metacard_id",
    },
}


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _json(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _flatten_keys(value: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{prefix}.{key}" if prefix else str(key)
            out.append(here.lower())
            out.extend(_flatten_keys(child, here))
    elif isinstance(value, list):
        for child in value[:25]:
            out.extend(_flatten_keys(child, prefix))
    return out


def _top_keys(value: dict[str, Any]) -> list[str]:
    return sorted(str(k).lower() for k in value.keys())


def _leaf_names(flat_keys: list[str]) -> set[str]:
    return {key.rsplit(".", 1)[-1].lower() for key in flat_keys}


def _anchor_families(flat_keys: list[str]) -> list[str]:
    leaves = _leaf_names(flat_keys)
    found = []
    for family, names in ANCHOR_KEY_FAMILIES.items():
        if leaves & names:
            found.append(family)
    return sorted(found)


def _safe_sample(row: dict[str, Any]) -> dict[str, Any]:
    evidence = _json(row.get("evidence"))
    # Values are intentionally omitted: shape is sufficient for contract design.
    return {
        "idProduct": str(row.get("id_product") or ""),
        "expansion_external_id_present": row.get("expansion_external_id") is not None,
        "metacard_external_id_present": row.get("metacard_external_id") is not None,
        "evidence_top_keys": _top_keys(evidence),
        "evidence_flat_keys": sorted(set(_flatten_keys(evidence)))[:80],
    }


def main() -> int:
    failures: list[dict[str, Any]] = []
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_singleton_heavy_inventory_v1",
    )
    conn.set_session(readonly=True, autocommit=False)

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game missing")
            gid = int(game["id"])

            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products "
                "WHERE source='cardmarket' AND game_id=%s AND product_group='single'",
                (gid,),
            )
            capture = cur.fetchone()["capture"]

            cur.execute(
                """
                SELECT
                    l.external_product_id,
                    l.print_id AS historical_print_id,
                    l.evidence,
                    e.external_id AS id_product,
                    e.name AS product_name,
                    e.expansion_external_id,
                    e.metacard_external_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket'
                  AND e.game_id=%s
                  AND e.product_group='single'
                  AND l.link_status=ANY(%s)
                  AND l.confidence='exact'
                  AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=%s
                ORDER BY e.external_id::bigint
                """,
                (gid, list(ACCEPTED), METHOD),
            )
            rows = [dict(r) for r in cur.fetchall()]

            if len(rows) != EXPECTED_ROWS:
                failures.append({
                    "reason": "cohort_count_drift",
                    "expected": EXPECTED_ROWS,
                    "actual": len(rows),
                })

            unique_products = {int(r["external_product_id"]) for r in rows}
            unique_historical_prints = {int(r["historical_print_id"]) for r in rows}
            if len(unique_products) != len(rows):
                failures.append({"reason": "historical_product_not_unique"})
            if len(unique_historical_prints) != len(rows):
                failures.append({"reason": "historical_print_not_unique"})

            top_key_counts: Counter[str] = Counter()
            flat_key_counts: Counter[str] = Counter()
            shape_counts: Counter[str] = Counter()
            family_counts: Counter[str] = Counter()
            family_combo_counts: Counter[str] = Counter()
            no_evidence = 0
            samples_by_shape: dict[str, dict[str, Any]] = {}

            for row in rows:
                evidence = _json(row.get("evidence"))
                if not evidence:
                    no_evidence += 1
                top_keys = _top_keys(evidence)
                flat_keys = sorted(set(_flatten_keys(evidence)))
                families = _anchor_families(flat_keys)
                shape = ",".join(top_keys) if top_keys else "<empty>"
                combo = "+".join(families) if families else "<none>"
                shape_counts[shape] += 1
                family_combo_counts[combo] += 1
                top_key_counts.update(top_keys)
                flat_key_counts.update(flat_keys)
                family_counts.update(families)
                samples_by_shape.setdefault(shape, _safe_sample(row))

            # Current Cardmarket product-side grouping is independent of the
            # historical physical mapping and safe to inspect.
            product_group_sizes: Counter[int] = Counter()
            product_group_membership_failures = 0
            group_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                expansion = str(row.get("expansion_external_id") or "")
                metacard = str(row.get("metacard_external_id") or "")
                group_rows[(expansion, metacard)].append(row)

            group_inventory: list[dict[str, Any]] = []
            for (expansion, metacard), targets in sorted(group_rows.items()):
                if not expansion or not metacard:
                    current_count = 0
                    target_ids: set[int] = set()
                    current_ids: set[int] = set()
                else:
                    cur.execute(
                        """
                        SELECT id,external_id
                        FROM external_catalog_products
                        WHERE source='cardmarket'
                          AND game_id=%s
                          AND product_group='single'
                          AND expansion_external_id=%s
                          AND metacard_external_id=%s
                          AND last_seen_at=%s
                        ORDER BY external_id::bigint
                        """,
                        (gid, expansion, metacard, capture),
                    )
                    current = [dict(r) for r in cur.fetchall()]
                    current_count = len(current)
                    current_ids = {int(r["id"]) for r in current}
                    target_ids = {int(r["external_product_id"]) for r in targets}

                product_group_sizes[current_count] += len(targets)
                membership_ok = bool(target_ids) and target_ids.issubset(current_ids)
                if not membership_ok:
                    product_group_membership_failures += len(targets)
                group_inventory.append({
                    "expansion_external_id": expansion,
                    "metacard_external_id": metacard,
                    "cohort_rows": len(targets),
                    "current_cardmarket_group_size": current_count,
                    "cohort_members_are_current": membership_ok,
                })

            if product_group_membership_failures:
                failures.append({
                    "reason": "cohort_product_missing_from_current_capture",
                    "rows": product_group_membership_failures,
                })

            # Historical print IDs are counted only to characterize the stored
            # cohort. They are explicitly forbidden as replay inputs here.
            product_singleton_rows = int(product_group_sizes.get(1, 0))
            report = {
                "status": "PASS" if not failures else "FAIL",
                "mode": "read_only_inventory",
                "production_writes": 0,
                "game": GAME,
                "mapping_method": METHOD,
                "cardmarket_capture": str(capture),
                "contract": {
                    "purpose": "inventory independent evidence before designing a replay contract",
                    "historical_print_id_used_for_derivation": False,
                    "historical_print_metadata_used_for_derivation": False,
                    "mapping_promotion_allowed": False,
                    "writes_allowed": False,
                },
                "cohort": {
                    "rows": len(rows),
                    "unique_products": len(unique_products),
                    "unique_historical_prints": len(unique_historical_prints),
                },
                "evidence": {
                    "rows_without_evidence": no_evidence,
                    "top_key_counts": dict(top_key_counts.most_common()),
                    "flat_key_counts": dict(flat_key_counts.most_common()),
                    "shape_counts": dict(shape_counts.most_common()),
                    "potential_independent_anchor_family_counts": dict(family_counts.most_common()),
                    "potential_independent_anchor_combo_counts": dict(family_combo_counts.most_common()),
                    "samples_by_shape": list(samples_by_shape.values())[:25],
                },
                "current_product_surface": {
                    "rows_in_current_singleton_cardmarket_group": product_singleton_rows,
                    "rows_in_non_singleton_or_missing_group": len(rows) - product_singleton_rows,
                    "group_size_distribution_by_row": {
                        str(k): v for k, v in sorted(product_group_sizes.items())
                    },
                    "cohort_product_membership_failures": product_group_membership_failures,
                    "groups": len(group_rows),
                    "group_inventory": group_inventory,
                },
                "replayability": {
                    "certified_rows": 0,
                    "reason": "inventory only; replay requires an independent physical-side selector established from source evidence, not historical print_id",
                },
                "failures": failures,
            }

            out = Path(os.getenv(
                "YGO_OCG_SINGLETON_HEAVY_INVENTORY_V1_OUTPUT",
                "/tmp/yugioh-ocg-singleton-heavy-inventory-v1.json",
            ))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            print(json.dumps({
                "status": report["status"],
                "rows": len(rows),
                "product_singleton_rows": product_singleton_rows,
                "rows_without_evidence": no_evidence,
                "anchor_families": report["evidence"]["potential_independent_anchor_family_counts"],
                "production_writes": 0,
            }, indent=2, default=str))
            conn.rollback()
            return 0 if not failures else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
