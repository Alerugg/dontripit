from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
V3_OUTPUT = Path("/tmp/yugioh-ocg-physical-identity-v3-child.json")
PUBLIC_OUTPUT = Path("/tmp/yugioh-ocg-public-contract-replay-v6-child.json")


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
        raise RuntimeError({
            "child_failed": script,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout.splitlines()[-30:],
            "stderr_tail": proc.stderr.splitlines()[-30:],
        })
    if not output_path.exists():
        raise RuntimeError({"child_output_missing": str(output_path), "script": script})
    return json.loads(output_path.read_text(encoding="utf-8"))


def main() -> int:
    v3 = _run_child(
        "scripts/audit_yugioh_ocg_physical_identity_v3.py",
        "YGO_OCG_PHYSICAL_IDENTITY_V3_OUTPUT",
        V3_OUTPUT,
    )
    public = _run_child(
        "scripts/audit_yugioh_ocg_public_contract_replay_v6.py",
        "YGO_OCG_PUBLIC_CONTRACT_REPLAY_V6_OUTPUT",
        PUBLIC_OUTPUT,
    )

    failures: list[str] = []
    if v3.get("status") != "PASS":
        failures.append("physical_identity_v3_not_pass")
    if int(v3.get("production_writes") or 0) != 0:
        failures.append("physical_identity_v3_writes_nonzero")
    if public.get("status") != "PASS":
        failures.append("public_contract_replay_v6_not_pass")
    if int(public.get("production_writes") or 0) != 0:
        failures.append("public_contract_replay_v6_writes_nonzero")
    if str(v3.get("cardmarket_capture")) != str(public.get("cardmarket_capture")):
        failures.append("child_capture_mismatch")
    if public.get("contract", {}).get("historical_pair_used_for_derivation") is not False:
        failures.append("public_replay_not_independent")

    expected: dict[str, int] = {}
    v3_methods = set()
    for method, payload in v3.get("certified", {}).get("methods", {}).items():
        if payload.get("status") != "PASS":
            continue
        count = int(payload.get("expected_certified_pairs") or 0)
        if count <= 0:
            failures.append(f"invalid_v3_method_count:{method}")
            continue
        expected[method] = count
        v3_methods.add(method)

    public_methods = set()
    for method, payload in public.get("methods", {}).items():
        count = int(payload.get("independently_replayed_rows") or 0)
        if count <= 0:
            failures.append(f"invalid_public_method_count:{method}")
            continue
        if method in expected:
            failures.append(f"unexpected_method_overlap:{method}")
            continue
        expected[method] = count
        public_methods.add(method)

    if int(public.get("independently_replayed_rows") or 0) != 101:
        failures.append("public_contract_expected_101_not_met")
    if len(v3_methods & public_methods) != 0:
        failures.append("v3_public_method_sets_overlap")

    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_physical_identity_v4",
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
                SELECT l.mapping_method,l.external_product_id,l.print_id,e.external_id id_product,
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
            all_rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    if str(capture) != str(v3.get("cardmarket_capture")):
        failures.append("aggregator_capture_mismatch")

    by_method: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        by_method[str(row.get("mapping_method") or "")].append(row)

    selected_rows: list[dict] = []
    method_validation = {}
    for method, expected_count in sorted(expected.items()):
        rows = by_method.get(method, [])
        pairs = {(int(r["external_product_id"]), int(r["print_id"])) for r in rows}
        products = {p for p, _ in pairs}
        prints = {p for _, p in pairs}
        ok = (
            len(rows) == expected_count
            and len(pairs) == expected_count
            and len(products) == expected_count
            and len(prints) == expected_count
        )
        if not ok:
            failures.append(f"method_count_or_bijection_drift:{method}")
        method_validation[method] = {
            "expected_certified_pairs": expected_count,
            "accepted_exact_reviewed_ja_rows": len(rows),
            "unique_pairs": len(pairs),
            "unique_products": len(products),
            "unique_prints": len(prints),
            "source": "physical_identity_v3" if method in v3_methods else "public_contract_replay_v6",
            "status": "PASS" if ok else "FAIL",
        }
        if ok:
            selected_rows.extend(rows)

    selected_pairs = {(int(r["external_product_id"]), int(r["print_id"])) for r in selected_rows}
    selected_products = {p for p, _ in selected_pairs}
    selected_prints = {p for _, p in selected_pairs}
    if len(selected_rows) != len(selected_pairs):
        failures.append("cross_method_duplicate_pair")
    if len(selected_pairs) != len(selected_products):
        failures.append("cross_method_product_collision")
    if len(selected_pairs) != len(selected_prints):
        failures.append("cross_method_print_collision")

    v3_count = int(v3.get("certified", {}).get("links") or 0)
    public_count = int(public.get("independently_replayed_rows") or 0)
    expected_union = v3_count + public_count
    if len(selected_pairs) != expected_union:
        failures.append("unexpected_overlap_or_missing_between_v3_and_public_v6")

    residual = [r for r in all_rows if str(r.get("mapping_method") or "") not in expected]
    residual_by_method = Counter(str(r.get("mapping_method") or "") for r in residual)
    total = len(all_rows)
    coverage = round(100 * len(selected_pairs) / total, 4) if total else 0.0

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": str(capture),
        "contract": {
            "physical_identity_v3_must_replay_green_in_same_run": True,
            "public_contract_replay_v6_must_replay_green_in_same_run": True,
            "public_contract_replay_is_independent_of_historical_pair": True,
            "certified_methods_must_match_current_exact_reviewed_JA_rows": True,
            "global_product_one_to_one": True,
            "global_print_one_to_one": True,
            "writes_allowed": False,
        },
        "children": {
            "physical_identity_v3": {
                "status": v3.get("status"),
                "certified_pairs": v3_count,
                "production_writes": int(v3.get("production_writes") or 0),
            },
            "public_contract_replay_v6": {
                "status": public.get("status"),
                "certified_pairs": public_count,
                "production_writes": int(public.get("production_writes") or 0),
            },
        },
        "accepted_ygo": {"exact_reviewed_ja_links": total},
        "certified": {
            "links": len(selected_pairs),
            "unique_products": len(selected_products),
            "unique_prints": len(selected_prints),
            "coverage_of_exact_reviewed_ja_pct": coverage,
            "expected_disjoint_union": expected_union,
            "methods": method_validation,
        },
        "remaining_unreproduced_exact_reviewed_ja": len(residual),
        "remaining_by_mapping_method": dict(sorted(residual_by_method.items(), key=lambda kv: (-kv[1], kv[0]))),
        "failures": failures,
    }

    out = Path(os.getenv("YGO_OCG_PHYSICAL_IDENTITY_V4_OUTPUT", "/tmp/yugioh-ocg-physical-identity-v4.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
