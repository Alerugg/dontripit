from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
BASE_OUTPUT = Path("/tmp/yugioh-ocg-physical-identity-v2-base.json")
STRUCTURAL_OUTPUT = Path("/tmp/yugioh-ocg-structural-replay-v4-child.json")


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _run_child(script: str, output_env: str, output_path: Path) -> dict:
    env = dict(os.environ)
    env[output_env] = str(output_path)
    proc = subprocess.run(
        [sys.executable, script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            {
                "child_failed": script,
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout.splitlines()[-30:],
                "stderr_tail": proc.stderr.splitlines()[-30:],
            }
        )
    if not output_path.exists():
        raise RuntimeError({"child_output_missing": str(output_path), "script": script})
    return json.loads(output_path.read_text(encoding="utf-8"))


def _expected_methods(base: dict, structural: dict) -> dict[str, int]:
    expected: dict[str, int] = {}

    strict = base.get("replayed_certified", {}).get("strict_methods", {})
    for method, payload in strict.items():
        if payload.get("status") != "PASS":
            continue
        count = int(payload.get("expected_from_replay") or 0)
        if count <= 0:
            raise RuntimeError({"invalid_base_strict_count": method, "payload": payload})
        expected[method] = count

    neutral = base.get("replayed_certified", {}).get("provenance_neutral_methods", {})
    for method, payload in neutral.items():
        count = int(payload.get("certified_pairs") or 0)
        if count <= 0:
            continue
        if method in expected and expected[method] != count:
            raise RuntimeError({"base_method_count_disagreement": method, "a": expected[method], "b": count})
        expected[method] = count

    for method, payload in structural.get("methods", {}).items():
        if payload.get("status") != "CERTIFIED":
            continue
        count = int(payload.get("certified_pairs") or 0)
        if count <= 0:
            raise RuntimeError({"invalid_structural_count": method, "payload": payload})
        if method in expected and expected[method] != count:
            raise RuntimeError({"cross_audit_method_count_disagreement": method, "a": expected[method], "b": count})
        expected[method] = count

    return expected


def main() -> int:
    base = _run_child(
        "scripts/audit_yugioh_ocg_physical_identity_v2.py",
        "YGO_OCG_PHYSICAL_IDENTITY_V2_OUTPUT",
        BASE_OUTPUT,
    )
    structural = _run_child(
        "scripts/audit_yugioh_ocg_structural_replay_v4.py",
        "YGO_OCG_STRUCTURAL_REPLAY_V4_OUTPUT",
        STRUCTURAL_OUTPUT,
    )

    failures = []
    if base.get("status") != "PASS":
        failures.append("physical_identity_v2_not_pass")
    if int(base.get("production_writes") or 0) != 0:
        failures.append("physical_identity_v2_writes_nonzero")
    if structural.get("status") != "PASS":
        failures.append("structural_replay_v4_not_pass")
    if int(structural.get("production_writes") or 0) != 0:
        failures.append("structural_replay_v4_writes_nonzero")
    if str(base.get("cardmarket_capture")) != str(structural.get("cardmarket_capture")):
        failures.append("child_capture_mismatch")

    expected = _expected_methods(base, structural)
    methods = sorted(expected)

    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_physical_identity_v3",
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
                SELECT l.mapping_method,l.external_product_id,l.print_id,
                       e.external_id id_product,e.last_seen_at,
                       p.language,s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                """,
                (gid, list(ACCEPTED)),
            )
            all_exact_reviewed_ja = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    if str(capture) != str(base.get("cardmarket_capture")):
        failures.append("aggregator_capture_mismatch")

    by_method: dict[str, list[dict]] = defaultdict(list)
    for row in all_exact_reviewed_ja:
        by_method[str(row.get("mapping_method") or "")].append(row)

    selected_rows: list[dict] = []
    method_validation = {}
    for method in methods:
        rows = by_method.get(method, [])
        pairs = {(int(r["external_product_id"]), int(r["print_id"])) for r in rows}
        products = {pair[0] for pair in pairs}
        prints = {pair[1] for pair in pairs}
        expected_count = int(expected[method])
        ok = (
            len(rows) == expected_count
            and len(pairs) == expected_count
            and len(products) == expected_count
            and len(prints) == expected_count
        )
        if not ok:
            failures.append(f"aggregated_method_count_or_bijection_drift:{method}")
        method_validation[method] = {
            "expected_certified_pairs": expected_count,
            "accepted_exact_reviewed_ja_rows": len(rows),
            "unique_pairs": len(pairs),
            "unique_products": len(products),
            "unique_prints": len(prints),
            "status": "PASS" if ok else "FAIL",
        }
        if ok:
            selected_rows.extend(rows)

    selected_pairs = {(int(r["external_product_id"]), int(r["print_id"])) for r in selected_rows}
    selected_products = {pair[0] for pair in selected_pairs}
    selected_prints = {pair[1] for pair in selected_pairs}
    if len(selected_rows) != len(selected_pairs):
        failures.append("cross_method_duplicate_pair")
    if len(selected_pairs) != len(selected_products):
        failures.append("cross_method_product_collision")
    if len(selected_pairs) != len(selected_prints):
        failures.append("cross_method_print_collision")

    base_count = int(base.get("replayed_certified", {}).get("links") or 0)
    structural_count = int(structural.get("certified_pairs") or 0)
    expected_union_if_disjoint = base_count + structural_count
    if len(selected_pairs) != expected_union_if_disjoint:
        failures.append("unexpected_overlap_or_missing_between_v2_and_v4")

    total_exact_reviewed = len(all_exact_reviewed_ja)
    coverage = round(100 * len(selected_pairs) / total_exact_reviewed, 4) if total_exact_reviewed else 0.0

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": str(capture),
        "contract": {
            "physical_identity_v2_must_replay_green_in_same_run": True,
            "structural_replay_v4_must_replay_green_in_same_run": True,
            "certified_methods_must_match_current_exact_reviewed_JA_rows": True,
            "global_product_one_to_one": True,
            "global_print_one_to_one": True,
            "no_cross_audit_overlap_expected": True,
            "writes_allowed": False,
        },
        "children": {
            "physical_identity_v2": {
                "status": base.get("status"),
                "production_writes": int(base.get("production_writes") or 0),
                "certified_pairs": base_count,
                "coverage_pct": base.get("replayed_certified", {}).get("coverage_of_exact_reviewed_ja_pct"),
            },
            "structural_replay_v4": {
                "status": structural.get("status"),
                "production_writes": int(structural.get("production_writes") or 0),
                "certified_pairs": structural_count,
            },
        },
        "accepted_ygo": {
            "exact_reviewed_ja_links": total_exact_reviewed,
            "exact_reviewed_ja_product_conflicts": int(base.get("accepted_ygo", {}).get("exact_reviewed_ja_product_conflicts") or 0),
            "exact_reviewed_ja_print_conflicts": int(base.get("accepted_ygo", {}).get("exact_reviewed_ja_print_conflicts") or 0),
        },
        "certified": {
            "links": len(selected_pairs),
            "unique_products": len(selected_products),
            "unique_prints": len(selected_prints),
            "coverage_of_exact_reviewed_ja_pct": coverage,
            "base_v2_links": base_count,
            "added_structural_v4_links": structural_count,
            "expected_disjoint_union": expected_union_if_disjoint,
            "methods": method_validation,
        },
        "remaining_unreproduced_exact_reviewed_ja": total_exact_reviewed - len(selected_pairs),
        "base_v2_blocked_or_unreproduced_methods": base.get("unreproduced_exact_reviewed_ja_methods", {}),
        "failures": failures,
    }

    out = Path(os.getenv("YGO_OCG_PHYSICAL_IDENTITY_V3_OUTPUT", "/tmp/yugioh-ocg-physical-identity-v3.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
