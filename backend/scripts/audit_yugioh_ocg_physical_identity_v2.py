from __future__ import annotations

import importlib
import inspect
import json
import os
import traceback
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")

NEUTRAL_REPLAYS = (
    ("certified_unique_physical_v3", "app.scripts.apply_yugioh_ocg_certified_singletons_v1"),
    ("certified_unique_physical_v4", "app.scripts.apply_yugioh_ocg_certified_singletons_v2"),
    ("certified_unique_physical_v5", "app.scripts.apply_yugioh_ocg_certified_singletons_v3"),
    ("certified_unique_physical_v7", "app.scripts.apply_yugioh_ocg_certified_singletons_v5"),
)


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE URL required")
    return url


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _conflicts(rows: list[dict]) -> tuple[dict, dict]:
    by_product = defaultdict(list)
    by_print = defaultdict(list)
    for row in rows:
        by_product[int(row["external_product_id"])].append(row)
        by_print[int(row["print_id"])].append(row)
    return (
        {key: value for key, value in by_product.items() if len({int(x["print_id"]) for x in value}) > 1},
        {key: value for key, value in by_print.items() if len({int(x["external_product_id"]) for x in value}) > 1},
    )


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
                       e.metacard_external_id,e.expansion_external_id,e.last_seen_at,
                       p.id print_id,p.card_id,p.language,p.collector_number,p.rarity,p.variant,
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

    ja_links = [r for r in links if str(r.get("language") or "").lower() == "ja"]
    exact_reviewed_ja = [
        r for r in ja_links
        if str(r.get("confidence") or "") == "exact" and bool(r.get("reviewed"))
    ]
    all_market_group_product_conflicts, _ = _conflicts(links)
    product_conflicts, print_conflicts = _conflicts(exact_reviewed_ja)
    methods = Counter(str(r.get("mapping_method") or "") for r in exact_reviewed_ja)
    current_ja = sum(r.get("last_seen_at") == capture for r in exact_reviewed_ja)
    return {
        "gid": gid,
        "capture": capture,
        "links": links,
        "ja_links": ja_links,
        "exact_reviewed_ja": exact_reviewed_ja,
        "method_counts": methods,
        "product_conflicts": product_conflicts,
        "print_conflicts": print_conflicts,
        "all_market_group_multi_print_products": len(all_market_group_product_conflicts),
        "current_exact_reviewed_ja": current_ja,
    }


def _safe_error(exc: BaseException) -> dict:
    payload = exc.args[0] if getattr(exc, "args", None) else str(exc)
    return {
        "type": type(exc).__name__,
        "message": str(payload)[:2000],
        "trace_tail": traceback.format_exc().splitlines()[-8:],
    }


def _normalize_report(name: str, basis: str, report: dict) -> dict:
    certified = int(report.get("certified_pairs") or report.get("expected_total") or 0)
    existing = int(report.get("already_accepted_same_pair") or 0)
    new = int(report.get("new_links_ready") or 0)
    ok = (
        report.get("status") == "pass"
        and int(report.get("production_writes") or 0) == 0
        and certified > 0
        and existing == certified
        and new == 0
    )
    return {
        "cohort": name,
        "replay_type": "strict_historical_contract",
        "status": "CERTIFIED" if ok else "REJECTED",
        "basis": basis,
        "mapping_method": str(report.get("mapping_method") or ""),
        "certified_pairs": certified if ok else 0,
        "observed_pairs": certified,
        "already_accepted_same_pair": existing,
        "new_links_ready": new,
        "production_writes": int(report.get("production_writes") or 0),
        "stable_identity_sha256": report.get("stable_identity_sha256"),
        "frozen_proposal_sha256": report.get("frozen_proposal_sha256"),
        "cardmarket_capture": report.get("cardmarket_capture"),
        "sets": report.get("sets", []),
    }


def _replay_full_bijection(current_capture: str) -> dict:
    from app.scripts import apply_yugioh_ocg_full_bijection_cohort_v2 as mod

    old_capture = mod.v1.EXPECTED_CAPTURE
    try:
        mod.v1.EXPECTED_CAPTURE = current_capture
        return mod.run(False, "")
    finally:
        mod.v1.EXPECTED_CAPTURE = old_capture


def _replay_singleton_heavy_v1(current_capture: str) -> dict:
    from app.scripts import apply_yugioh_ocg_singleton_heavy_cohort_v1 as mod

    old_capture = mod.EXPECTED_CAPTURE
    try:
        mod.EXPECTED_CAPTURE = current_capture
        return mod.run(False, "")
    finally:
        mod.EXPECTED_CAPTURE = old_capture


def _replay_singleton_heavy_v2(current_capture: str) -> dict:
    from app.scripts import apply_yugioh_ocg_next_singleton_heavy_cohort223_v2 as wrapper

    mod = wrapper.base
    old_capture = mod.EXPECTED_CAPTURE
    try:
        mod.EXPECTED_CAPTURE = current_capture
        return mod.run(False, "")
    finally:
        mod.EXPECTED_CAPTURE = old_capture


def _replay_current_module(module_name: str) -> dict:
    mod = importlib.import_module(module_name)
    sig = inspect.signature(mod.run)
    kwargs = {"apply": False}
    if "confirm" in sig.parameters:
        kwargs["confirm"] = ""
    return mod.run(**kwargs)


def _run_strict_replays(current_capture: str) -> list[dict]:
    runners = [
        (
            "full_logical_bijection_715",
            lambda: _replay_full_bijection(current_capture),
            "current catalog full logical/physical bijection + unique metacard bridge + frozen identity hash",
        ),
        (
            "singleton_heavy_445",
            lambda: _replay_singleton_heavy_v1(current_capture),
            "full physical multiplicity bijection; singleton products only; frozen identity hash",
        ),
        (
            "singleton_heavy_v2_223",
            lambda: _replay_singleton_heavy_v2(current_capture),
            "second frozen singleton-heavy geometry; later variant claims are not auto-trusted",
        ),
        (
            "certified_unique_physical_v6",
            lambda: _replay_current_module("app.scripts.apply_yugioh_ocg_certified_singletons_v4"),
            "current OCG regional expansion surface + certified Cardmarket region/image evidence + unique product/metacard + one canonical JA physical print + strict name + one-to-one",
        ),
        (
            "certified_unique_physical_v8",
            lambda: _replay_current_module("app.scripts.apply_yugioh_ocg_certified_singletons_v6"),
            "current OCG regional expansion surface + certified Cardmarket region/image evidence + unique product/metacard + one canonical JA physical print + strict name + one-to-one",
        ),
    ]
    results = []
    for name, runner, basis in runners:
        try:
            results.append(_normalize_report(name, basis, runner()))
        except Exception as exc:
            results.append(
                {
                    "cohort": name,
                    "replay_type": "strict_historical_contract",
                    "status": "UNREPRODUCIBLE",
                    "basis": basis,
                    "certified_pairs": 0,
                    "production_writes": 0,
                    "error": _safe_error(exc),
                }
            )
    return results


def _neutral_rederive(state: dict, cohort: str, module_name: str) -> tuple[dict, set[tuple[int, int]]]:
    mod = importlib.import_module(module_name)
    surfaces = dict(mod.SURFACES)
    expected_total = int(mod.EXPECTED_TOTAL)
    method = str(mod.METHOD)
    gid = int(state["gid"])
    capture = state["capture"]

    historical_rows = [
        r for r in state["exact_reviewed_ja"]
        if str(r.get("mapping_method") or "") == method
    ]
    historical_pairs = {
        (int(r["external_product_id"]), int(r["print_id"])) for r in historical_rows
    }
    historical_products = {pair[0] for pair in historical_pairs}
    historical_prints = {pair[1] for pair in historical_pairs}

    support_by_meta: dict[str, list[dict]] = defaultdict(list)
    ja_pair_rows: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in state["links"]:
        meta = str(row.get("metacard_external_id") or "")
        if meta:
            support_by_meta[meta].append(row)
        if str(row.get("language") or "").lower() == "ja":
            ja_pair_rows[(int(row["external_product_id"]), int(row["print_id"]))].append(row)

    conn = psycopg2.connect(
        _db_url(),
        connect_timeout=30,
        application_name=f"dontripit_ygo_ocg_physical_identity_v2_{cohort}",
    )
    conn.set_session(readonly=True, autocommit=False)
    derived: dict[tuple[int, int], dict] = {}
    set_reports = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id "
                "WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",
                (gid,),
            )
            ja_baseline = int(cur.fetchone()["n"])
            if ja_baseline != 36426:
                raise RuntimeError({"yugioh_ja_baseline_drift": ja_baseline})

            for set_code, cfg in surfaces.items():
                expansion_id = str(cfg["idExpansion"])
                cur.execute(
                    """
                    SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                    FROM external_catalog_products e
                    WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                      AND e.expansion_external_id=%s AND e.last_seen_at=%s
                    ORDER BY e.metacard_external_id,e.external_id::bigint
                    """,
                    (gid, expansion_id, capture),
                )
                products = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,c.name card_name
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN sets s ON s.id=p.set_id
                    WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                      AND lower(coalesce(p.language,''))='ja'
                    ORDER BY p.card_id,p.collector_number,p.id
                    """,
                    (gid, set_code),
                )
                canonical = [dict(r) for r in cur.fetchall()]
                if len(products) != int(cfg["products"]) or len(canonical) != int(cfg["prints"]):
                    raise RuntimeError(
                        {
                            "surface_drift": set_code,
                            "actual_products": len(products),
                            "expected_products": int(cfg["products"]),
                            "actual_prints": len(canonical),
                            "expected_prints": int(cfg["prints"]),
                        }
                    )

                products_by_meta: dict[str, list[dict]] = defaultdict(list)
                prints_by_card: dict[int, list[dict]] = defaultdict(list)
                name_to_cards: dict[str, set[int]] = defaultdict(set)
                canonical_cards: set[int] = set()
                for product in products:
                    products_by_meta[str(product.get("metacard_external_id") or "")].append(product)
                for print_row in canonical:
                    card_id = int(print_row["card_id"])
                    canonical_cards.add(card_id)
                    prints_by_card[card_id].append(print_row)
                    name_to_cards[_norm(print_row.get("card_name"))].add(card_id)

                set_derived: dict[tuple[int, int], dict] = {}
                for meta, group in products_by_meta.items():
                    if not meta or len(group) != 1:
                        continue
                    product = group[0]
                    external_product_id = int(product["external_product_id"])
                    name_cards = set(name_to_cards.get(_norm(product.get("name")), set()))
                    if not name_cards:
                        continue

                    independent_support_cards = {
                        int(row["card_id"])
                        for row in support_by_meta.get(meta, [])
                        if int(row["external_product_id"]) != external_product_id
                    }
                    card_id = None
                    resolution = None
                    if len(independent_support_cards) == 1:
                        only = next(iter(independent_support_cards))
                        if only in canonical_cards and only in name_cards:
                            card_id = only
                            resolution = "nonself_metacard_unique_card_in_certified_set_and_name"
                    elif len(independent_support_cards) > 1:
                        intersection = independent_support_cards & canonical_cards & name_cards
                        if len(intersection) == 1:
                            card_id = next(iter(intersection))
                            resolution = "nonself_metacard_ambiguous_resolved_by_certified_set_and_name"
                    if card_id is None:
                        continue

                    card_prints = prints_by_card.get(card_id, [])
                    if len(card_prints) != 1:
                        continue
                    print_row = card_prints[0]
                    if _norm(product.get("name")) != _norm(print_row.get("card_name")):
                        continue

                    pair = (external_product_id, int(print_row["print_id"]))
                    set_derived[pair] = {
                        "set_code": set_code,
                        "idExpansion": expansion_id,
                        "external_product_id": external_product_id,
                        "idProduct": str(product["id_product"]),
                        "idMetacard": meta,
                        "print_id": int(print_row["print_id"]),
                        "card_id": card_id,
                        "card_name": str(print_row["card_name"]),
                        "collector_number": str(print_row.get("collector_number") or ""),
                        "canonical_rarity": print_row.get("rarity"),
                        "canonical_variant": print_row.get("variant"),
                        "resolution_method": resolution,
                        "independent_support_products": len(
                            {
                                int(row["external_product_id"])
                                for row in support_by_meta.get(meta, [])
                                if int(row["external_product_id"]) != external_product_id
                            }
                        ),
                    }

                if len({pair[0] for pair in set_derived}) != len(set_derived):
                    raise RuntimeError({"derived_product_collision": set_code})
                if len({pair[1] for pair in set_derived}) != len(set_derived):
                    raise RuntimeError({"derived_print_collision": set_code})

                historical_set_pairs = {
                    (int(r["external_product_id"]), int(r["print_id"]))
                    for r in historical_rows
                    if str(r.get("set_code") or "").upper() == str(set_code).upper()
                }
                intersection = set(set_derived) & historical_set_pairs
                set_reports.append(
                    {
                        "set_code": set_code,
                        "idExpansion": expansion_id,
                        "products": len(products),
                        "canonical_ja_prints": len(canonical),
                        "historical_pairs": len(historical_set_pairs),
                        "expected_historical_pairs": int(cfg["pairs"]),
                        "independently_rederived_pairs": len(set_derived),
                        "rederived_same_historical_pair": len(intersection),
                        "derived_not_historical": len(set(set_derived) - historical_set_pairs),
                        "historical_not_rederived": len(historical_set_pairs - set(set_derived)),
                    }
                )
                derived.update(set_derived)
            conn.rollback()
    finally:
        conn.close()

    derived_pairs = set(derived)
    certified_pairs = derived_pairs & historical_pairs
    derived_not_historical = derived_pairs - historical_pairs
    historical_not_rederived = historical_pairs - derived_pairs

    derived_product_count = len({pair[0] for pair in derived_pairs})
    derived_print_count = len({pair[1] for pair in derived_pairs})
    hard_failures = []
    if len(historical_rows) != expected_total:
        hard_failures.append("historical_method_count_drift")
    if len(historical_pairs) != expected_total:
        hard_failures.append("historical_pair_dedup_drift")
    if len(historical_products) != expected_total:
        hard_failures.append("historical_product_collision")
    if len(historical_prints) != expected_total:
        hard_failures.append("historical_print_collision")
    if derived_product_count != len(derived_pairs):
        hard_failures.append("independent_derived_product_collision")
    if derived_print_count != len(derived_pairs):
        hard_failures.append("independent_derived_print_collision")

    replacement_provenance = Counter()
    replacement_samples = []
    for pair in sorted(derived_not_historical):
        methods = sorted({str(r.get("mapping_method") or "") for r in ja_pair_rows.get(pair, [])})
        if not methods:
            replacement_provenance["UNLINKED"] += 1
        else:
            replacement_provenance[" + ".join(methods)] += 1
        if len(replacement_samples) < 20:
            item = dict(derived[pair])
            item["current_pair_mapping_methods"] = methods
            replacement_samples.append(item)

    historical_samples = []
    historical_by_pair = {
        (int(r["external_product_id"]), int(r["print_id"])): r for r in historical_rows
    }
    for pair in sorted(historical_not_rederived):
        if len(historical_samples) >= 20:
            break
        row = historical_by_pair[pair]
        historical_samples.append(
            {
                "external_product_id": pair[0],
                "idProduct": str(row.get("id_product") or ""),
                "print_id": pair[1],
                "set_code": str(row.get("set_code") or ""),
                "collector_number": str(row.get("collector_number") or ""),
                "rarity": row.get("rarity"),
                "variant": row.get("variant"),
            }
        )

    if hard_failures:
        status = "FAIL"
    elif len(certified_pairs) == expected_total and not derived_not_historical and not historical_not_rederived:
        status = "CERTIFIED"
    elif certified_pairs:
        status = "PARTIAL"
    else:
        status = "UNREPRODUCIBLE"

    report = {
        "cohort": cohort,
        "replay_type": "provenance_neutral_nonself_rederivation",
        "status": status,
        "basis": (
            "current Cardmarket expansion geometry + exact canonical JA set + singleton metacard product + "
            "strict normalized name + non-self metacard support + one canonical JA print; mapping_method is used "
            "only to identify the historical cohort and never as identity evidence"
        ),
        "mapping_method": method,
        "expected_historical_pairs": expected_total,
        "historical_exact_reviewed_pairs": len(historical_pairs),
        "independently_rederived_pairs": len(derived_pairs),
        "certified_pairs": len(certified_pairs),
        "derived_not_historical": len(derived_not_historical),
        "historical_not_rederived": len(historical_not_rederived),
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "replacement_current_provenance": dict(replacement_provenance.most_common()),
        "replacement_samples": replacement_samples,
        "historical_not_rederived_samples": historical_samples,
        "sets": set_reports,
        "hard_failures": hard_failures,
    }
    return report, certified_pairs


def main() -> int:
    state = _current_state()
    capture = str(state["capture"])
    strict_replays = _run_strict_replays(capture)

    neutral_replays = []
    neutral_certified_pairs: set[tuple[int, int]] = set()
    for cohort, module_name in NEUTRAL_REPLAYS:
        try:
            report, pairs = _neutral_rederive(state, cohort, module_name)
            neutral_replays.append(report)
            neutral_certified_pairs.update(pairs)
        except Exception as exc:
            neutral_replays.append(
                {
                    "cohort": cohort,
                    "replay_type": "provenance_neutral_nonself_rederivation",
                    "status": "UNREPRODUCIBLE",
                    "certified_pairs": 0,
                    "production_writes": 0,
                    "error": _safe_error(exc),
                }
            )

    failures = []
    if state["product_conflicts"]:
        failures.append("exact_reviewed_ja_product_maps_to_multiple_prints")
    if state["print_conflicts"]:
        failures.append("exact_reviewed_ja_print_maps_to_multiple_products")

    strict_certified_pairs: set[tuple[int, int]] = set()
    strict_method_validation = {}
    strict_methods = set()
    for replay in strict_replays:
        if replay.get("status") != "CERTIFIED" or not replay.get("mapping_method"):
            continue
        method = str(replay["mapping_method"])
        expected = int(replay["certified_pairs"])
        rows = [
            r for r in state["exact_reviewed_ja"]
            if str(r.get("mapping_method") or "") == method
        ]
        pairs = {(int(r["external_product_id"]), int(r["print_id"])) for r in rows}
        products = {pair[0] for pair in pairs}
        prints = {pair[1] for pair in pairs}
        current = sum(r.get("last_seen_at") == state["capture"] for r in rows)
        ok = len(rows) == expected and len(pairs) == expected and len(products) == expected and len(prints) == expected
        if not ok:
            failures.append(f"replayed_method_count_drift:{method}")
        strict_method_validation[method] = {
            "expected_from_replay": expected,
            "accepted_exact_reviewed_ja": len(rows),
            "unique_pairs": len(pairs),
            "unique_products": len(products),
            "unique_prints": len(prints),
            "current_catalog_products": current,
            "one_to_one": len(pairs) == len(products) == len(prints),
            "status": "PASS" if ok else "FAIL",
        }
        if ok:
            strict_methods.add(method)
            strict_certified_pairs.update(pairs)

    neutral_hard_failures = [
        r["cohort"] for r in neutral_replays
        if r.get("status") == "FAIL"
    ]
    if neutral_hard_failures:
        failures.append("neutral_replay_hard_failure:" + ",".join(neutral_hard_failures))

    all_certified_pairs = strict_certified_pairs | neutral_certified_pairs
    certified_products = {pair[0] for pair in all_certified_pairs}
    certified_prints = {pair[1] for pair in all_certified_pairs}
    if len(all_certified_pairs) != len(certified_products) or len(all_certified_pairs) != len(certified_prints):
        failures.append("cross_cohort_identity_collision")

    represented_methods = strict_methods | {
        str(r.get("mapping_method") or "")
        for r in neutral_replays
        if int(r.get("certified_pairs") or 0) > 0
    }
    unreproduced_methods = {
        key: value
        for key, value in state["method_counts"].most_common()
        if key not in represented_methods
    }

    neutral_summary = {
        str(r.get("mapping_method") or r.get("cohort") or "unknown"): {
            "status": r.get("status"),
            "expected_historical_pairs": int(r.get("expected_historical_pairs") or 0),
            "independently_rederived_pairs": int(r.get("independently_rederived_pairs") or 0),
            "certified_pairs": int(r.get("certified_pairs") or 0),
            "derived_not_historical": int(r.get("derived_not_historical") or 0),
            "historical_not_rederived": int(r.get("historical_not_rederived") or 0),
        }
        for r in neutral_replays
    }

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": capture,
        "contract": {
            "region_policy": "JP/OCG remains physically distinct; no JP<->EN collector-code aliasing",
            "market_group_policy": "Cardmarket idProduct may span canonical language Prints; conflicts are evaluated inside the exact JA physical surface",
            "promotion_policy": "only pairs independently rederived from current source geometry count as Physical Identity V2",
            "provenance_policy": "mapping_method may identify a historical cohort but is never accepted as proof of physical identity",
            "anti_circularity_policy": "neutral replay excludes the target idProduct from metacard support before resolving its card",
            "writes_allowed": False,
        },
        "accepted_ygo": {
            "all_accepted_links": len(state["links"]),
            "all_language_market_group_multi_print_products": state["all_market_group_multi_print_products"],
            "exact_reviewed_ja_links": len(state["exact_reviewed_ja"]),
            "exact_reviewed_ja_current_catalog": state["current_exact_reviewed_ja"],
            "exact_reviewed_ja_product_conflicts": len(state["product_conflicts"]),
            "exact_reviewed_ja_print_conflicts": len(state["print_conflicts"]),
        },
        "replays": strict_replays + neutral_replays,
        "replayed_certified": {
            "links": len(all_certified_pairs),
            "strict_links": len(strict_certified_pairs),
            "provenance_neutral_links": len(neutral_certified_pairs),
            "unique_products": len(certified_products),
            "unique_prints": len(certified_prints),
            "coverage_of_exact_reviewed_ja_pct": round(
                100 * len(all_certified_pairs) / len(state["exact_reviewed_ja"]), 4
            ) if state["exact_reviewed_ja"] else 0,
            "strict_methods": strict_method_validation,
            "provenance_neutral_methods": neutral_summary,
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
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
