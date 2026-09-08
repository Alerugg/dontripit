from __future__ import annotations

import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
TARGET_METHODS = (
    "cardmarket_ocg_certified_full_logical_bijection_v1",
    "cardmarket_ocg_certified_public_code_singleton_v1",
    "cardmarket_ocg_certified_public_code_singleton_v2",
)
EXPECTED_TOTALS = {
    "cardmarket_ocg_certified_full_logical_bijection_v1": 460,
    "cardmarket_ocg_certified_public_code_singleton_v1": 219,
    "cardmarket_ocg_certified_public_code_singleton_v2": 168,
}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def main() -> int:
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_structural_replay_v4",
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
            if capture is None:
                raise RuntimeError("Cardmarket capture missing")

            cur.execute(
                """
                SELECT l.mapping_method,l.external_product_id,l.print_id,
                       e.external_id id_product,e.metacard_external_id,e.expansion_external_id,
                       p.card_id,p.language,s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=ANY(%s)
                """,
                (gid, list(ACCEPTED), list(TARGET_METHODS)),
            )
            historical = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.mapping_method,l.external_product_id,l.print_id,
                       e.metacard_external_id,p.card_id,p.language
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                """,
                (gid, list(ACCEPTED)),
            )
            support_rows = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT count(*) n FROM prints p JOIN cards c ON c.id=p.card_id "
                "WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja'",
                (gid,),
            )
            ja_baseline = int(cur.fetchone()["n"])

            historical_by_method: dict[str, list[dict]] = defaultdict(list)
            for row in historical:
                historical_by_method[str(row["mapping_method"])].append(row)

            reports = {}
            all_certified: set[tuple[int, int]] = set()
            failures = []

            for method in TARGET_METHODS:
                method_rows = historical_by_method.get(method, [])
                expected_total = EXPECTED_TOTALS[method]
                historical_pairs = {
                    (int(r["external_product_id"]), int(r["print_id"])) for r in method_rows
                }
                if len(method_rows) != expected_total or len(historical_pairs) != expected_total:
                    failures.append(f"historical_count_drift:{method}")

                cohorts = Counter(
                    (str(r.get("set_code") or "").upper(), str(r.get("expansion_external_id") or ""))
                    for r in method_rows
                )
                method_derived: set[tuple[int, int]] = set()
                set_reports = []

                for (set_code, expansion_id), historical_count in sorted(cohorts.items()):
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
                    prints = [dict(r) for r in cur.fetchall()]

                    product_names = Counter(_norm(r.get("name")) for r in products)
                    print_names = Counter(_norm(r.get("card_name")) for r in prints)
                    names_equal = product_names == print_names
                    complete_geometry = (
                        len(products) == historical_count
                        and len(prints) == historical_count
                        and names_equal
                    )

                    products_by_meta: dict[str, list[dict]] = defaultdict(list)
                    prints_by_card: dict[int, list[dict]] = defaultdict(list)
                    name_to_cards: dict[str, set[int]] = defaultdict(set)
                    canonical_cards: set[int] = set()
                    for product in products:
                        products_by_meta[str(product.get("metacard_external_id") or "")].append(product)
                    for print_row in prints:
                        card_id = int(print_row["card_id"])
                        canonical_cards.add(card_id)
                        prints_by_card[card_id].append(print_row)
                        name_to_cards[_norm(print_row.get("card_name"))].add(card_id)

                    set_pairs: set[tuple[int, int]] = set()
                    unresolved = Counter()
                    for meta, group in products_by_meta.items():
                        if not meta or len(group) != 1:
                            unresolved["non_singleton_product_metacard"] += len(group)
                            continue
                        product = group[0]
                        external_product_id = int(product["external_product_id"])
                        independent_cards = {
                            int(r["card_id"])
                            for r in support_rows
                            if str(r.get("metacard_external_id") or "") == meta
                            and str(r.get("mapping_method") or "") != method
                            and int(r["external_product_id"]) != external_product_id
                        }
                        name_cards = set(name_to_cards.get(_norm(product.get("name")), set()))
                        candidates = independent_cards & canonical_cards & name_cards
                        if len(candidates) != 1:
                            unresolved["non_unique_independent_card"] += 1
                            continue
                        card_id = next(iter(candidates))
                        card_prints = prints_by_card.get(card_id, [])
                        if len(card_prints) != 1:
                            unresolved["non_unique_canonical_print"] += 1
                            continue
                        print_row = card_prints[0]
                        if _norm(product.get("name")) != _norm(print_row.get("card_name")):
                            unresolved["name_mismatch"] += 1
                            continue
                        set_pairs.add((external_product_id, int(print_row["print_id"])))

                    one_to_one = (
                        len({p for p, _ in set_pairs}) == len(set_pairs)
                        and len({q for _, q in set_pairs}) == len(set_pairs)
                    )
                    historical_set_pairs = {
                        (int(r["external_product_id"]), int(r["print_id"]))
                        for r in method_rows
                        if str(r.get("set_code") or "").upper() == set_code
                        and str(r.get("expansion_external_id") or "") == expansion_id
                    }
                    intersection = set_pairs & historical_set_pairs
                    set_status = (
                        "CERTIFIED"
                        if complete_geometry and one_to_one
                        and len(set_pairs) == historical_count
                        and set_pairs == historical_set_pairs
                        else "REJECTED"
                    )
                    if set_status == "CERTIFIED":
                        method_derived.update(set_pairs)
                    set_reports.append(
                        {
                            "set_code": set_code,
                            "idExpansion": expansion_id,
                            "historical_pairs": historical_count,
                            "current_products": len(products),
                            "canonical_ja_prints": len(prints),
                            "name_multiset_equal": names_equal,
                            "independently_rederived_pairs": len(set_pairs),
                            "same_historical_pairs": len(intersection),
                            "derived_not_historical": len(set_pairs - historical_set_pairs),
                            "historical_not_rederived": len(historical_set_pairs - set_pairs),
                            "one_to_one": one_to_one,
                            "unresolved": dict(unresolved),
                            "status": set_status,
                        }
                    )

                status = (
                    "CERTIFIED"
                    if len(method_derived) == expected_total and method_derived == historical_pairs
                    and all(x["status"] == "CERTIFIED" for x in set_reports)
                    else "PARTIAL" if method_derived else "UNREPRODUCIBLE"
                )
                if status == "CERTIFIED":
                    all_certified.update(method_derived)
                reports[method] = {
                    "status": status,
                    "expected_historical_pairs": expected_total,
                    "historical_pairs": len(historical_pairs),
                    "certified_pairs": len(method_derived),
                    "sets": set_reports,
                }

            products = {p for p, _ in all_certified}
            prints = {q for _, q in all_certified}
            if len(products) != len(all_certified) or len(prints) != len(all_certified):
                failures.append("cross_method_collision")
            conn.rollback()
    finally:
        conn.close()

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": str(capture),
        "ja_baseline": ja_baseline,
        "contract": {
            "historical_method_is_cohort_locator_only": True,
            "target_method_rows_excluded_from_metacard_support": True,
            "current_product_and_canonical_name_multisets_must_match_exactly": True,
            "product_metacard_must_be_singleton_inside_expansion": True,
            "resolved_card_must_have_one_exact_JA_print_inside_set": True,
            "strict_name_match": True,
            "global_one_to_one": True,
            "writes_allowed": False,
        },
        "certified_pairs": len(all_certified),
        "unique_products": len(products),
        "unique_prints": len(prints),
        "methods": reports,
        "failures": failures,
    }
    out = Path(os.getenv("YGO_OCG_STRUCTURAL_REPLAY_V4_OUTPUT", "/tmp/yugioh-ocg-structural-replay-v4.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
