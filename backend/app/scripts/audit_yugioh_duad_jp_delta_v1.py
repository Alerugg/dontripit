from __future__ import annotations

import json
import os
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "6129"
SET_CODE = "DUAD"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED_PRODUCTS = 121
EXPECTED_METACARDS = 81
EXPECTED_CANONICAL_PRINTS = 117
EXPECTED_CANONICAL_CARDS = 78


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def main() -> int:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_duad_jp_delta_v1",
    )
    conn.set_session(readonly=True, autocommit=False)
    failures: list[str] = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]
            if capture is None:
                raise RuntimeError("Current Cardmarket capture missing")

            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                          e.expansion_external_id,e.last_seen_at
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s
                   ORDER BY e.metacard_external_id,e.external_id::bigint""",
                (game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            product_row_ids = [int(r["external_product_id"]) for r in products]
            existing_candidate_links: list[dict] = []
            if product_row_ids:
                cur.execute(
                    """SELECT e.external_id id_product,l.print_id,l.mapping_method,l.confidence,
                              l.link_status,l.reviewed,p.language,s.code set_code
                       FROM external_catalog_print_links l
                       JOIN external_catalog_products e ON e.id=l.external_product_id
                       JOIN prints p ON p.id=l.print_id
                       JOIN sets s ON s.id=p.set_id
                       WHERE l.external_product_id=ANY(%s) AND l.link_status=ANY(%s)
                       ORDER BY e.external_id::bigint""",
                    (product_row_ids, list(ACCEPTED)),
                )
                existing_candidate_links = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.is_foil,
                          p.language,c.name card_name,s.code set_code
                   FROM prints p
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                     AND lower(coalesce(p.language,''))=%s
                   ORDER BY p.card_id,p.collector_number,p.id""",
                (game_id, SET_CODE, LANGUAGE),
            )
            canonical_prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT e.metacard_external_id,p.card_id,count(*) accepted_links,
                          array_agg(DISTINCT e.expansion_external_id) expansions,
                          array_agg(DISTINCT p.language) languages,
                          array_agg(DISTINCT s.code) set_codes
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id
                   JOIN cards c ON c.id=p.card_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                   GROUP BY e.metacard_external_id,p.card_id
                   ORDER BY e.metacard_external_id,p.card_id""",
                (game_id, list(ACCEPTED)),
            )
            global_meta_card_rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    if len(products) != EXPECTED_PRODUCTS:
        failures.append(f"candidate_products_expected_{EXPECTED_PRODUCTS}_got_{len(products)}")
    unique_metas = {str(r.get("metacard_external_id") or "") for r in products}
    if "" in unique_metas:
        failures.append("candidate_product_missing_metacard")
        unique_metas.discard("")
    if len(unique_metas) != EXPECTED_METACARDS:
        failures.append(f"candidate_metacards_expected_{EXPECTED_METACARDS}_got_{len(unique_metas)}")
    if existing_candidate_links:
        failures.append(f"candidate_already_has_accepted_links_{len(existing_candidate_links)}")

    canonical_cards = {int(r["card_id"]) for r in canonical_prints}
    if len(canonical_prints) != EXPECTED_CANONICAL_PRINTS:
        failures.append(f"canonical_prints_expected_{EXPECTED_CANONICAL_PRINTS}_got_{len(canonical_prints)}")
    if len(canonical_cards) != EXPECTED_CANONICAL_CARDS:
        failures.append(f"canonical_cards_expected_{EXPECTED_CANONICAL_CARDS}_got_{len(canonical_cards)}")

    prints_by_card: dict[int, list[dict]] = defaultdict(list)
    canonical_name_to_cards: dict[str, set[int]] = defaultdict(set)
    canonical_name_by_card: dict[int, str] = {}
    for row in canonical_prints:
        card_id = int(row["card_id"])
        prints_by_card[card_id].append(row)
        canonical_name_by_card[card_id] = str(row["card_name"])
        canonical_name_to_cards[_norm(row["card_name"])].add(card_id)

    meta_to_global_cards: dict[str, set[int]] = defaultdict(set)
    meta_global_evidence: dict[str, list[dict]] = defaultdict(list)
    for row in global_meta_card_rows:
        meta = str(row.get("metacard_external_id") or "")
        if not meta:
            continue
        card_id = int(row["card_id"])
        meta_to_global_cards[meta].add(card_id)
        meta_global_evidence[meta].append(
            {
                "card_id": card_id,
                "accepted_links": int(row["accepted_links"]),
                "expansions": list(row.get("expansions") or []),
                "languages": list(row.get("languages") or []),
                "set_codes": list(row.get("set_codes") or []),
            }
        )

    products_by_meta: dict[str, list[dict]] = defaultdict(list)
    for row in products:
        products_by_meta[str(row.get("metacard_external_id") or "")].append(row)

    resolved_meta: dict[str, dict] = {}
    ambiguous_meta: list[dict] = []
    external_only_meta: list[dict] = []
    for meta, group in products_by_meta.items():
        names = sorted({str(r.get("name") or "") for r in group})
        global_cards = sorted(meta_to_global_cards.get(meta, set()))
        global_duad_cards = [card_id for card_id in global_cards if card_id in canonical_cards]
        normalized_name_cards: set[int] = set()
        if len(names) == 1:
            normalized_name_cards = canonical_name_to_cards.get(_norm(names[0]), set())

        method = None
        card_id = None
        if len(global_cards) == 1 and global_cards[0] in canonical_cards:
            card_id = global_cards[0]
            method = "accepted_global_metacard_to_duad_card"
        elif len(global_cards) == 1 and global_cards[0] not in canonical_cards:
            external_only_meta.append(
                {
                    "idMetacard": meta,
                    "product_count": len(group),
                    "idProducts": [str(r["id_product"]) for r in group],
                    "names": names,
                    "reason": "accepted_global_metacard_maps_to_card_outside_canonical_DUAD_JA",
                    "global_card_ids": global_cards,
                    "global_evidence": meta_global_evidence.get(meta, []),
                }
            )
            continue
        elif len(global_cards) > 1:
            intersection = sorted(set(global_duad_cards) & set(normalized_name_cards))
            if len(intersection) == 1:
                card_id = intersection[0]
                method = "ambiguous_global_metacard_resolved_by_strict_normalized_name"
            else:
                ambiguous_meta.append(
                    {
                        "idMetacard": meta,
                        "product_count": len(group),
                        "idProducts": [str(r["id_product"]) for r in group],
                        "names": names,
                        "reason": "metacard_maps_to_multiple_canonical_cards",
                        "global_card_ids": global_cards,
                        "duad_card_ids": global_duad_cards,
                        "normalized_name_card_ids": sorted(normalized_name_cards),
                    }
                )
                continue
        elif len(normalized_name_cards) == 1:
            card_id = next(iter(normalized_name_cards))
            method = "strict_normalized_name_to_unique_duad_card"
        elif len(normalized_name_cards) == 0:
            external_only_meta.append(
                {
                    "idMetacard": meta,
                    "product_count": len(group),
                    "idProducts": [str(r["id_product"]) for r in group],
                    "names": names,
                    "reason": "no_global_metacard_identity_and_no_strict_normalized_DUAD_name",
                    "global_card_ids": global_cards,
                }
            )
            continue
        else:
            ambiguous_meta.append(
                {
                    "idMetacard": meta,
                    "product_count": len(group),
                    "idProducts": [str(r["id_product"]) for r in group],
                    "names": names,
                    "reason": "strict_normalized_name_matches_multiple_DUAD_cards",
                    "normalized_name_card_ids": sorted(normalized_name_cards),
                }
            )
            continue

        resolved_meta[meta] = {
            "idMetacard": meta,
            "card_id": int(card_id),
            "card_name": canonical_name_by_card[int(card_id)],
            "product_names": names,
            "resolution_method": method,
            "products": group,
            "global_evidence": meta_global_evidence.get(meta, []),
        }

    metas_by_card: dict[int, list[dict]] = defaultdict(list)
    for value in resolved_meta.values():
        metas_by_card[int(value["card_id"])].append(value)

    balanced_groups: list[dict] = []
    unbalanced_groups: list[dict] = []
    card_collisions: list[dict] = []
    consumed_cards: set[int] = set()
    for card_id, metas in sorted(metas_by_card.items()):
        if len(metas) != 1:
            card_collisions.append(
                {
                    "card_id": card_id,
                    "card_name": canonical_name_by_card[card_id],
                    "metacards": [m["idMetacard"] for m in metas],
                    "product_counts": [len(m["products"]) for m in metas],
                }
            )
            continue
        meta = metas[0]
        ext_products = sorted(meta["products"], key=lambda r: int(r["id_product"]))
        can_prints = sorted(prints_by_card[card_id], key=lambda r: int(r["print_id"]))
        payload = {
            "idMetacard": meta["idMetacard"],
            "card_id": card_id,
            "card_name": canonical_name_by_card[card_id],
            "product_names": meta["product_names"],
            "resolution_method": meta["resolution_method"],
            "product_count": len(ext_products),
            "print_count": len(can_prints),
            "products": [
                {"idProduct": str(r["id_product"]), "external_product_id": int(r["external_product_id"])}
                for r in ext_products
            ],
            "prints": [
                {
                    "print_id": int(r["print_id"]),
                    "collector_number": r["collector_number"],
                    "rarity": r["rarity"],
                    "variant": r["variant"],
                }
                for r in can_prints
            ],
        }
        consumed_cards.add(card_id)
        if len(ext_products) == len(can_prints):
            balanced_groups.append(payload)
        else:
            unbalanced_groups.append(payload)

    canonical_only_cards = []
    for card_id in sorted(canonical_cards - consumed_cards):
        canonical_only_cards.append(
            {
                "card_id": card_id,
                "card_name": canonical_name_by_card[card_id],
                "print_count": len(prints_by_card[card_id]),
                "prints": [
                    {
                        "print_id": int(r["print_id"]),
                        "collector_number": r["collector_number"],
                        "rarity": r["rarity"],
                        "variant": r["variant"],
                    }
                    for r in prints_by_card[card_id]
                ],
            }
        )

    balanced_products = sum(g["product_count"] for g in balanced_groups)
    balanced_prints = sum(g["print_count"] for g in balanced_groups)
    singleton_groups = [g for g in balanced_groups if g["product_count"] == 1]
    multi_groups = [g for g in balanced_groups if g["product_count"] > 1]
    external_only_products = sum(g["product_count"] for g in external_only_meta)
    ambiguous_products = sum(g["product_count"] for g in ambiguous_meta)
    unbalanced_products = sum(g["product_count"] for g in unbalanced_groups)

    report = {
        "status": "pass" if not failures else "fail",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "candidate": {
            "idExpansion": EXPANSION_ID,
            "products": len(products),
            "unique_metacards": len(unique_metas),
            "existing_accepted_links": len(existing_candidate_links),
        },
        "canonical": {
            "set_code": SET_CODE,
            "language": LANGUAGE,
            "prints": len(canonical_prints),
            "cards": len(canonical_cards),
        },
        "resolution_summary": {
            "resolved_metacards": len(resolved_meta),
            "balanced_groups": len(balanced_groups),
            "balanced_products": balanced_products,
            "balanced_prints": balanced_prints,
            "balanced_singleton_groups": len(singleton_groups),
            "balanced_singleton_products": sum(g["product_count"] for g in singleton_groups),
            "balanced_multiversion_groups": len(multi_groups),
            "balanced_multiversion_products": sum(g["product_count"] for g in multi_groups),
            "unbalanced_groups": len(unbalanced_groups),
            "unbalanced_products": unbalanced_products,
            "external_only_metacards": len(external_only_meta),
            "external_only_products": external_only_products,
            "ambiguous_metacards": len(ambiguous_meta),
            "ambiguous_products": ambiguous_products,
            "card_collisions": len(card_collisions),
            "canonical_only_cards": len(canonical_only_cards),
            "canonical_only_prints": sum(g["print_count"] for g in canonical_only_cards),
        },
        "balanced_group_size_histogram": dict(sorted(Counter(g["product_count"] for g in balanced_groups).items())),
        "balanced_groups": balanced_groups,
        "unbalanced_groups": unbalanced_groups,
        "external_only_metacards": external_only_meta,
        "ambiguous_metacards": ambiguous_meta,
        "card_collisions": card_collisions,
        "canonical_only_cards": canonical_only_cards,
        "failures": failures,
    }
    output = Path(os.getenv("YGO_DUAD_JP_DELTA_OUTPUT", "/tmp/yugioh-duad-jp-delta-v1.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
