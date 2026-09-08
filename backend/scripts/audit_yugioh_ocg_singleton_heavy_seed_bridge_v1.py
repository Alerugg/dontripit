from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
TARGET_METHOD = "cardmarket_ocg_certified_singleton_heavy_cohort_v1"
TARGET_ROWS = 445
BASE_CERTIFIED_ROWS = 3925
ACCEPTED = ("accepted", "mapped", "exact")
V4_OUTPUT = Path("/tmp/yugioh-ocg-physical-identity-v4-seed-child.json")


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _run_v4() -> dict[str, Any]:
    env = dict(os.environ)
    env["YGO_OCG_PHYSICAL_IDENTITY_V4_OUTPUT"] = str(V4_OUTPUT)
    proc = subprocess.run(
        [sys.executable, "scripts/audit_yugioh_ocg_physical_identity_v4.py"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError({
            "v4_child_failed": proc.returncode,
            "stdout_tail": proc.stdout.splitlines()[-40:],
            "stderr_tail": proc.stderr.splitlines()[-40:],
        })
    if not V4_OUTPUT.exists():
        raise RuntimeError("V4 child output missing")
    return json.loads(V4_OUTPUT.read_text(encoding="utf-8"))


def main() -> int:
    base = _run_v4()
    failures: list[dict[str, Any]] = []

    if base.get("status") != "PASS":
        failures.append({"reason": "base_v4_not_pass"})
    if int(base.get("production_writes") or 0) != 0:
        failures.append({"reason": "base_v4_writes_nonzero"})
    if int(base.get("certified", {}).get("links") or 0) != BASE_CERTIFIED_ROWS:
        failures.append({
            "reason": "base_v4_certified_count_drift",
            "expected": BASE_CERTIFIED_ROWS,
            "actual": base.get("certified", {}).get("links"),
        })

    seed_methods = {
        str(method)
        for method, payload in base.get("certified", {}).get("methods", {}).items()
        if payload.get("status") == "PASS"
    }
    if not seed_methods:
        failures.append({"reason": "no_green_seed_methods"})
    if TARGET_METHOD in seed_methods:
        failures.append({"reason": "target_method_illegally_present_in_seed_certificate"})

    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_singleton_heavy_seed_bridge_v1",
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
            if str(capture) != str(base.get("cardmarket_capture")):
                failures.append({
                    "reason": "capture_mismatch_with_base_v4",
                    "base": str(base.get("cardmarket_capture")),
                    "current": str(capture),
                })

            # Certified seed rows only. These rows are the sole logical bridge.
            # Target-method links and target evidence are excluded by construction.
            cur.execute(
                """
                SELECT l.mapping_method,l.external_product_id,l.print_id,
                       e.external_id id_product,e.expansion_external_id,e.metacard_external_id,
                       e.last_seen_at,p.set_id,p.card_id
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=ANY(%s)
                """,
                (gid, list(ACCEPTED), sorted(seed_methods)),
            )
            seed_rows = [dict(row) for row in cur.fetchall()]

            if len(seed_rows) != BASE_CERTIFIED_ROWS:
                failures.append({
                    "reason": "seed_rows_do_not_equal_current_v4_certificate",
                    "expected": BASE_CERTIFIED_ROWS,
                    "actual": len(seed_rows),
                })

            seed_products = {int(row["external_product_id"]) for row in seed_rows}
            seed_prints = {int(row["print_id"]) for row in seed_rows}
            if len(seed_products) != BASE_CERTIFIED_ROWS:
                failures.append({"reason": "seed_product_bijection_drift"})
            if len(seed_prints) != BASE_CERTIFIED_ROWS:
                failures.append({"reason": "seed_print_bijection_drift"})

            expansion_sets: dict[str, set[int]] = defaultdict(set)
            expansion_support_rows: Counter[str] = Counter()
            expansion_support_methods: dict[str, set[str]] = defaultdict(set)
            meta_cards: dict[str, set[int]] = defaultdict(set)
            meta_support_rows: Counter[str] = Counter()
            meta_support_methods: dict[str, set[str]] = defaultdict(set)

            current_seed_rows = 0
            for row in seed_rows:
                if str(row.get("last_seen_at")) == str(capture):
                    current_seed_rows += 1
                expansion = str(row.get("expansion_external_id") or "")
                metacard = str(row.get("metacard_external_id") or "")
                method = str(row.get("mapping_method") or "")
                if expansion:
                    expansion_sets[expansion].add(int(row["set_id"]))
                    expansion_support_rows[expansion] += 1
                    expansion_support_methods[expansion].add(method)
                if metacard:
                    meta_cards[metacard].add(int(row["card_id"]))
                    meta_support_rows[metacard] += 1
                    meta_support_methods[metacard].add(method)

            cur.execute(
                """
                SELECT l.external_product_id,l.print_id historical_print_id,
                       e.external_id id_product,e.name product_name,
                       e.expansion_external_id,e.metacard_external_id,e.last_seen_at
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=%s
                ORDER BY e.external_id::bigint
                """,
                (gid, list(ACCEPTED), TARGET_METHOD),
            )
            targets = [dict(row) for row in cur.fetchall()]
            if len(targets) != TARGET_ROWS:
                failures.append({
                    "reason": "target_count_drift",
                    "expected": TARGET_ROWS,
                    "actual": len(targets),
                })

            unresolved = Counter()
            derived: list[dict[str, Any]] = []
            support_strength = Counter()

            for target in targets:
                eid = int(target["external_product_id"])
                expansion = str(target.get("expansion_external_id") or "")
                metacard = str(target.get("metacard_external_id") or "")

                if str(target.get("last_seen_at")) != str(capture):
                    unresolved["target_not_in_current_capture"] += 1
                    continue
                if not expansion:
                    unresolved["missing_expansion_external_id"] += 1
                    continue
                if not metacard:
                    unresolved["missing_metacard_external_id"] += 1
                    continue

                # Current Cardmarket side must still be a true singleton group.
                cur.execute(
                    """
                    SELECT id,external_id,name
                    FROM external_catalog_products
                    WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                      AND expansion_external_id=%s AND metacard_external_id=%s
                      AND last_seen_at=%s
                    ORDER BY external_id::bigint
                    """,
                    (gid, expansion, metacard, capture),
                )
                product_group = [dict(row) for row in cur.fetchall()]
                if len(product_group) != 1:
                    unresolved[f"current_cardmarket_group_arity_{len(product_group)}"] += 1
                    continue
                if int(product_group[0]["id"]) != eid:
                    unresolved["current_singleton_is_not_target_product"] += 1
                    continue

                sets = expansion_sets.get(expansion, set())
                if len(sets) != 1:
                    unresolved[f"certified_seed_expansion_set_arity_{len(sets)}"] += 1
                    continue
                cards = meta_cards.get(metacard, set())
                if len(cards) != 1:
                    unresolved[f"certified_seed_metacard_card_arity_{len(cards)}"] += 1
                    continue

                set_id = next(iter(sets))
                card_id = next(iter(cards))

                # Resolve the physical side from current canonical JA data only.
                # No target collector/rareness/variant/evidence is consulted.
                cur.execute(
                    """
                    SELECT p.id print_id,p.collector_number,p.rarity,p.variant,
                           c.name card_name,s.code set_code
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND p.set_id=%s AND p.card_id=%s
                      AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.id
                    """,
                    (gid, set_id, card_id),
                )
                physical = [dict(row) for row in cur.fetchall()]
                if len(physical) != 1:
                    unresolved[f"current_JA_physical_arity_{len(physical)}"] += 1
                    continue

                chosen = physical[0]
                print_id = int(chosen["print_id"])
                if _norm(product_group[0].get("name")) != _norm(chosen.get("card_name")):
                    unresolved["current_product_name_vs_card_name_mismatch"] += 1
                    continue
                if eid in seed_products:
                    unresolved["target_product_already_in_seed_certificate"] += 1
                    continue
                if print_id in seed_prints:
                    unresolved["derived_print_already_claimed_by_seed_certificate"] += 1
                    continue

                # Historical target pair is read only after the independent pair exists.
                historical_print_id = int(target["historical_print_id"])
                if print_id != historical_print_id:
                    unresolved["derived_pair_differs_from_historical_target_pair"] += 1
                    continue

                e_support = int(expansion_support_rows.get(expansion, 0))
                m_support = int(meta_support_rows.get(metacard, 0))
                support_strength[f"expansion_rows_ge_{min(e_support, 5)}"] += 1
                support_strength[f"metacard_rows_ge_{min(m_support, 5)}"] += 1

                derived.append({
                    "external_product_id": eid,
                    "idProduct": str(target.get("id_product") or ""),
                    "print_id": print_id,
                    "expansion_external_id": expansion,
                    "metacard_external_id": metacard,
                    "derived_set_id": set_id,
                    "derived_card_id": card_id,
                    "set_code": str(chosen.get("set_code") or ""),
                    "card_name": str(chosen.get("card_name") or ""),
                    "expansion_seed_support_rows": e_support,
                    "expansion_seed_support_methods": len(expansion_support_methods.get(expansion, set())),
                    "metacard_seed_support_rows": m_support,
                    "metacard_seed_support_methods": len(meta_support_methods.get(metacard, set())),
                    "target_evidence_used_for_derivation": False,
                    "historical_target_print_used_for_derivation": False,
                })

            derived_products = {int(row["external_product_id"]) for row in derived}
            derived_prints = {int(row["print_id"]) for row in derived}
            if len(derived_products) != len(derived):
                failures.append({"reason": "derived_product_collision"})
            if len(derived_prints) != len(derived):
                failures.append({"reason": "derived_print_collision"})
            if derived_products & seed_products:
                failures.append({"reason": "derived_products_overlap_seed"})
            if derived_prints & seed_prints:
                failures.append({"reason": "derived_prints_overlap_seed"})

            report = {
                "status": "PASS" if not failures else "FAIL",
                "mode": "read_only_inventory",
                "production_writes": 0,
                "game": GAME,
                "target_mapping_method": TARGET_METHOD,
                "cardmarket_capture": str(capture),
                "contract": {
                    "base_v4_replayed_in_same_run": True,
                    "base_v4_certified_pairs": BASE_CERTIFIED_ROWS,
                    "logical_bridge_sources": "current exact/reviewed JA rows belonging only to methods green in V4",
                    "target_method_allowed_as_seed": False,
                    "target_evidence_used_for_derivation": False,
                    "historical_target_print_used_for_derivation": False,
                    "historical_target_print_compared_only_after_derivation": True,
                    "physical_selector": "unique current JA print for certified-seed-derived set_id + card_id",
                    "mapping_promotion_allowed": False,
                    "writes_allowed": False,
                },
                "base_v4": {
                    "status": base.get("status"),
                    "certified_pairs": int(base.get("certified", {}).get("links") or 0),
                    "seed_methods": sorted(seed_methods),
                    "seed_method_count": len(seed_methods),
                    "queried_seed_rows": len(seed_rows),
                    "seed_rows_current_capture": current_seed_rows,
                    "unique_seed_products": len(seed_products),
                    "unique_seed_prints": len(seed_prints),
                },
                "target": {
                    "rows": len(targets),
                    "independently_derivable_rows": len(derived),
                    "unresolved_rows": len(targets) - len(derived),
                    "derived_unique_products": len(derived_products),
                    "derived_unique_prints": len(derived_prints),
                    "unresolved_reason_counts": dict(unresolved.most_common()),
                    "support_strength": dict(support_strength.most_common()),
                },
                "hypothetical_union": {
                    "certified_seed_pairs": len(seed_products),
                    "new_derivable_pairs": len(derived),
                    "pairs_if_promoted_after_strict_gate": len(seed_products) + len(derived),
                    "product_collisions": len(derived_products & seed_products),
                    "print_collisions": len(derived_prints & seed_prints),
                },
                "derived_pairs": derived,
                "failures": failures,
            }

            out = Path(os.getenv(
                "YGO_OCG_SINGLETON_HEAVY_SEED_BRIDGE_V1_OUTPUT",
                "/tmp/yugioh-ocg-singleton-heavy-seed-bridge-v1.json",
            ))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            print(json.dumps({
                "status": report["status"],
                "base_v4": report["base_v4"]["certified_pairs"],
                "target_rows": len(targets),
                "independently_derivable_rows": len(derived),
                "unresolved": dict(unresolved.most_common()),
                "hypothetical_union": report["hypothetical_union"],
                "production_writes": 0,
            }, indent=2, default=str))
            conn.rollback()
            return 0 if not failures else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
