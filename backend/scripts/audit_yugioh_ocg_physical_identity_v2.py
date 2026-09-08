from __future__ import annotations

import json
import os
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE URL required")
    return url


def _current_state() -> dict:
    conn = psycopg2.connect(
        _db_url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_physical_identity_v2",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Yu-Gi-Oh game missing")
            gid = int(row["id"])

            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products "
                "WHERE source='cardmarket' AND game_id=%s AND product_group='single'",
                (gid,),
            )
            capture = cur.fetchone()["capture"]
            if capture is None:
                raise RuntimeError("Cardmarket YGO capture missing")

            cur.execute(
                """
                SELECT l.mapping_method,l.confidence,l.reviewed,l.link_status,
                       e.id external_product_id,e.external_id id_product,
                       e.expansion_external_id,e.last_seen_at,
                       p.id print_id,p.language,p.collector_number,p.rarity,p.variant,
                       s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s)
                """,
                (gid, list(ACCEPTED)),
            )
            links = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    by_product = defaultdict(list)
    by_print = defaultdict(list)
    for r in links:
        by_product[int(r["external_product_id"])].append(r)
        by_print[int(r["print_id"])].append(r)

    product_conflicts = {
        pid: rows
        for pid, rows in by_product.items()
        if len({int(x["print_id"]) for x in rows}) > 1
    }
    print_conflicts = {
        pid: rows
        for pid, rows in by_print.items()
        if len({int(x["external_product_id"]) for x in rows}) > 1
    }

    ja = [r for r in links if str(r.get("language") or "").lower() == "ja"]
    exact_reviewed_ja = [
        r
        for r in ja
        if str(r.get("confidence") or "") == "exact" and bool(r.get("reviewed"))
    ]
    methods = Counter(str(r.get("mapping_method") or "") for r in exact_reviewed_ja)
    current_ja = sum(r.get("last_seen_at") == capture for r in exact_reviewed_ja)

    return {
        "gid": gid,
        "capture": capture,
        "links": links,
        "exact_reviewed_ja": exact_reviewed_ja,
        "method_counts": methods,
        "product_conflicts": product_conflicts,
        "print_conflicts": print_conflicts,
        "current_exact_reviewed_ja": current_ja,
    }


def _safe_error(exc: BaseException) -> dict:
    payload = exc.args[0] if getattr(exc, "args", None) else str(exc)
    return {
        "type": type(exc).__name__,
        "message": str(payload)[:2000],
        "trace_tail": traceback.format_exc().splitlines()[-8:],
    }


def _replay_full_bijection(current_capture: str) -> dict:
    from app.scripts import apply_yugioh_ocg_full_bijection_cohort_v2 as mod

    old_capture = mod.v1.EXPECTED_CAPTURE
    try:
        mod.v1.EXPECTED_CAPTURE = current_capture
        report = mod.run(False, "")
    finally:
        mod.v1.EXPECTED_CAPTURE = old_capture
    return report


def _replay_singleton_heavy_v1(current_capture: str) -> dict:
    from app.scripts import apply_yugioh_ocg_singleton_heavy_cohort_v1 as mod

    old_capture = mod.EXPECTED_CAPTURE
    try:
        mod.EXPECTED_CAPTURE = current_capture
        report = mod.run(False, "")
    finally:
        mod.EXPECTED_CAPTURE = old_capture
    return report


def _replay_singleton_heavy_v2(current_capture: str) -> dict:
    # Importing the v2 wrapper intentionally configures the shared v1 engine
    # with the separately frozen V2 target geometry/hash.
    from app.scripts import apply_yugioh_ocg_next_singleton_heavy_cohort223_v2 as wrapper

    mod = wrapper.base
    old_capture = mod.EXPECTED_CAPTURE
    try:
        mod.EXPECTED_CAPTURE = current_capture
        report = mod.run(False, "")
    finally:
        mod.EXPECTED_CAPTURE = old_capture
    return report


def _run_replays(current_capture: str) -> list[dict]:
    runners = [
        (
            "full_logical_bijection_715",
            _replay_full_bijection,
            "full-current-catalog physical/logical bijection + unique metacard bridge + frozen identity hash",
        ),
        (
            "singleton_heavy_445",
            _replay_singleton_heavy_v1,
            "full physical multiplicity bijection; only 1-product/1-print groups promoted; frozen identity hash",
        ),
        (
            "singleton_heavy_v2_223",
            _replay_singleton_heavy_v2,
            "separate frozen singleton-heavy cohort using the same full physical bijection contract",
        ),
    ]
    results = []
    for name, runner, basis in runners:
        try:
            report = runner(current_capture)
            ok = (
                report.get("status") == "pass"
                and int(report.get("production_writes") or 0) == 0
                and str(report.get("cardmarket_capture")) == current_capture
                and int(report.get("new_links_ready") or 0) == 0
                and int(report.get("already_accepted_same_pair") or 0)
                == int(report.get("certified_pairs") or 0)
            )
            results.append(
                {
                    "cohort": name,
                    "status": "CERTIFIED" if ok else "REJECTED",
                    "basis": basis,
                    "mapping_method": str(report.get("mapping_method") or ""),
                    "certified_pairs": int(report.get("certified_pairs") or 0),
                    "already_accepted_same_pair": int(
                        report.get("already_accepted_same_pair") or 0
                    ),
                    "new_links_ready": int(report.get("new_links_ready") or 0),
                    "production_writes": int(report.get("production_writes") or 0),
                    "stable_identity_sha256": report.get("stable_identity_sha256"),
                    "frozen_proposal_sha256": report.get("frozen_proposal_sha256"),
                    "sets": report.get("sets", []),
                }
            )
        except Exception as exc:  # audit must preserve failed evidence, not hide it
            results.append(
                {
                    "cohort": name,
                    "status": "UNREPRODUCIBLE",
                    "basis": basis,
                    "certified_pairs": 0,
                    "production_writes": 0,
                    "error": _safe_error(exc),
                }
            )
    return results


def main() -> int:
    state = _current_state()
    capture = str(state["capture"])
    replays = _run_replays(capture)

    certified_methods = {
        r["mapping_method"]: int(r["certified_pairs"])
        for r in replays
        if r["status"] == "CERTIFIED" and r.get("mapping_method")
    }

    failures = []
    if state["product_conflicts"]:
        failures.append("accepted_cardmarket_product_maps_to_multiple_prints")
    if state["print_conflicts"]:
        failures.append("accepted_print_maps_to_multiple_cardmarket_products")

    certified_rows = []
    method_validation = {}
    for method, expected in certified_methods.items():
        rows = [
            r
            for r in state["exact_reviewed_ja"]
            if str(r.get("mapping_method") or "") == method
        ]
        products = {int(r["external_product_id"]) for r in rows}
        prints = {int(r["print_id"]) for r in rows}
        current = sum(r.get("last_seen_at") == state["capture"] for r in rows)
        ok = len(rows) == expected and len(products) == expected and len(prints) == expected
        if not ok:
            failures.append(f"replayed_method_count_drift:{method}")
        method_validation[method] = {
            "expected_from_replay": expected,
            "accepted_exact_reviewed_ja": len(rows),
            "unique_products": len(products),
            "unique_prints": len(prints),
            "current_catalog_products": current,
            "one_to_one": len(rows) == len(products) == len(prints),
            "status": "PASS" if ok else "FAIL",
        }
        if ok:
            certified_rows.extend(rows)

    certified_products = {int(r["external_product_id"]) for r in certified_rows}
    certified_prints = {int(r["print_id"]) for r in certified_rows}
    if len(certified_rows) != len(certified_products) or len(certified_rows) != len(certified_prints):
        failures.append("cross_cohort_identity_collision")

    unreproduced_methods = {
        k: v
        for k, v in state["method_counts"].most_common()
        if k not in certified_methods
    }

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": capture,
        "contract": {
            "region_policy": "JP/OCG remains physically distinct; no JP<->EN collector-code aliasing",
            "promotion_policy": "only cohorts replaying current source geometry with frozen identity hashes count as Physical Identity V2",
            "writes_allowed": False,
        },
        "accepted_ygo": {
            "all_accepted_links": len(state["links"]),
            "exact_reviewed_ja_links": len(state["exact_reviewed_ja"]),
            "exact_reviewed_ja_current_catalog": state["current_exact_reviewed_ja"],
            "accepted_product_conflicts": len(state["product_conflicts"]),
            "accepted_print_conflicts": len(state["print_conflicts"]),
        },
        "replays": replays,
        "replayed_certified": {
            "links": len(certified_rows),
            "unique_products": len(certified_products),
            "unique_prints": len(certified_prints),
            "methods": method_validation,
        },
        "unreproduced_exact_reviewed_ja_methods": unreproduced_methods,
        "all_exact_reviewed_ja_method_counts": dict(state["method_counts"].most_common()),
        "failures": failures,
    }

    out = Path(
        os.getenv(
            "YGO_OCG_PHYSICAL_IDENTITY_V2_OUTPUT",
            "/tmp/yugioh-ocg-physical-identity-v2.json",
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
