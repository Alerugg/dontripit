from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
SET_CODE = "AGOV"
ACCEPTED = ("accepted", "mapped", "exact")

RARITY_TO_VARIANT = {
    "common": "rarity-common",
    "rare": "rarity-rare",
    "super rare": "rarity-super",
    "ultra rare": "rarity-ultra",
    "secret rare": "rarity-secret",
    "ultimate rare": "rarity-ultimate",
    "quarter century secret rare": "rarity-25thsecret",
    "25th anniversary secret rare": "rarity-25thsecret",
    "25th secret rare": "rarity-25thsecret",
}

VERSION_RE = re.compile(r"\s*\(V\.(\d+)\s*-\s*([^()]+?)\)\s*$", re.I)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def parse_market_name(value: str) -> dict:
    raw = str(value or "").strip()
    match = VERSION_RE.search(raw)
    if not match:
        return {
            "raw": raw,
            "base_name": raw,
            "version": None,
            "rarity_label": None,
            "expected_variant": None,
        }
    rarity_label = re.sub(r"\s+", " ", match.group(2).strip()).casefold()
    return {
        "raw": raw,
        "base_name": raw[: match.start()].strip(),
        "version": int(match.group(1)),
        "rarity_label": rarity_label,
        "expected_variant": RARITY_TO_VARIANT.get(rarity_label),
    }


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_residual_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) AS ts FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["ts"]

            cur.execute(
                """
                SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.metacard_external_id,
                       e.website_path,e.raw_json,e.last_seen_at
                FROM external_catalog_products e
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND e.last_seen_at=%s
                ORDER BY e.id
                """,
                (game_id, EXPANSION_ID, capture),
            )
            products = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,p.is_foil,p.print_key,
                       c.name AS card_name,s.code AS set_code
                FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s
                ORDER BY p.id
                """,
                (game_id, SET_CODE),
            )
            prints = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT l.external_product_id,l.print_id,e.external_id AS id_product,e.expansion_external_id,
                       e.metacard_external_id,p.card_id,p.language,s.code AS set_code,l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s)
                """,
                (game_id, list(ACCEPTED)),
            )
            accepted = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT e.metacard_external_id,p.card_id,count(DISTINCT l.id) AS evidence_links
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
                GROUP BY e.metacard_external_id,p.card_id
                ORDER BY e.metacard_external_id,p.card_id
                """,
                (game_id, list(ACCEPTED)),
            )
            meta_rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    claimed_products = {int(r["external_product_id"]) for r in accepted}
    claimed_prints = {int(r["print_id"]) for r in accepted}
    residual_products = [r for r in products if int(r["external_product_id"]) not in claimed_products]
    residual_prints = [r for r in prints if int(r["print_id"]) not in claimed_prints]

    meta_cards = defaultdict(set)
    meta_evidence = defaultdict(int)
    for row in meta_rows:
        meta = str(row["metacard_external_id"])
        card_id = int(row["card_id"])
        meta_cards[meta].add(card_id)
        meta_evidence[(meta, card_id)] += int(row["evidence_links"] or 0)

    prints_by_card = defaultdict(list)
    for row in residual_prints:
        prints_by_card[int(row["card_id"])].append(row)

    products_by_meta = defaultdict(list)
    for row in residual_products:
        if row.get("metacard_external_id") is not None:
            products_by_meta[str(row["metacard_external_id"])].append(row)

    classifications = Counter()
    suffix_hist = Counter()
    variant_hist = Counter(str(r.get("variant") or "") for r in residual_prints)
    proposal = []
    residual_detail = []

    for product in residual_products:
        parsed = parse_market_name(product["name"])
        if parsed["rarity_label"]:
            suffix_hist[parsed["rarity_label"]] += 1
        meta = str(product.get("metacard_external_id") or "")
        cards = meta_cards.get(meta, set())
        detail = {
            "idProduct": str(product["id_product"]),
            "external_product_id": int(product["external_product_id"]),
            "market_name": product["name"],
            "idMetacard": meta or None,
            "parsed": parsed,
            "metacard_card_ids": sorted(cards),
            "metacard_evidence_links": {str(cid): meta_evidence[(meta, cid)] for cid in sorted(cards)},
        }
        if len(cards) != 1:
            classifications["metacard_not_one_canonical_card"] += 1
            detail["classification"] = "metacard_not_one_canonical_card"
            residual_detail.append(detail)
            continue

        card_id = next(iter(cards))
        candidates = prints_by_card.get(card_id, [])
        detail["canonical_candidates_before_rarity"] = [
            {
                "print_id": int(r["print_id"]),
                "collector_number": r["collector_number"],
                "card_name": r["card_name"],
                "rarity": r["rarity"],
                "variant": r["variant"],
                "is_foil": bool(r["is_foil"]),
            }
            for r in candidates
        ]
        if norm(parsed["base_name"]) != norm(candidates[0]["card_name"] if candidates else ""):
            classifications["name_or_card_surface_mismatch"] += 1
            detail["classification"] = "name_or_card_surface_mismatch"
            residual_detail.append(detail)
            continue

        expected_variant = parsed["expected_variant"]
        if expected_variant:
            rarity_candidates = [r for r in candidates if str(r.get("variant") or "").casefold() == expected_variant]
        else:
            rarity_candidates = []
        detail["canonical_candidates_after_rarity"] = [
            {
                "print_id": int(r["print_id"]),
                "collector_number": r["collector_number"],
                "rarity": r["rarity"],
                "variant": r["variant"],
            }
            for r in rarity_candidates
        ]

        if parsed["rarity_label"] and not expected_variant:
            classifications["unrecognized_cardmarket_rarity_label"] += 1
            detail["classification"] = "unrecognized_cardmarket_rarity_label"
        elif expected_variant and len(rarity_candidates) == 1:
            print_row = rarity_candidates[0]
            classifications["rarity_exact_unique"] += 1
            detail["classification"] = "rarity_exact_unique"
            detail["proposed_print_id"] = int(print_row["print_id"])
            proposal.append(
                {
                    "external_product_id": int(product["external_product_id"]),
                    "idProduct": str(product["id_product"]),
                    "idMetacard": meta,
                    "print_id": int(print_row["print_id"]),
                    "card_id": card_id,
                    "card_name": print_row["card_name"],
                    "collector_number": print_row["collector_number"],
                    "market_name": product["name"],
                    "cardmarket_version": parsed["version"],
                    "cardmarket_rarity_label": parsed["rarity_label"],
                    "canonical_variant": print_row["variant"],
                    "canonical_rarity": print_row["rarity"],
                    "metacard_evidence_links": meta_evidence[(meta, card_id)],
                }
            )
        elif expected_variant:
            classifications["rarity_still_ambiguous_or_missing"] += 1
            detail["classification"] = "rarity_still_ambiguous_or_missing"
        else:
            classifications["no_explicit_cardmarket_rarity_suffix"] += 1
            detail["classification"] = "no_explicit_cardmarket_rarity_suffix"
        residual_detail.append(detail)

    product_ids = [int(r["external_product_id"]) for r in proposal]
    print_ids = [int(r["print_id"]) for r in proposal]
    duplicate_products = len(product_ids) - len(set(product_ids))
    duplicate_prints = len(print_ids) - len(set(print_ids))
    if duplicate_products or duplicate_prints:
        raise RuntimeError({"proposal_not_one_to_one": {"duplicate_products": duplicate_products, "duplicate_prints": duplicate_prints}})

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "idExpansion": EXPANSION_ID,
        "canonical_set": SET_CODE,
        "regional_products_total": len(products),
        "canonical_ja_prints_total": len(prints),
        "already_accepted_products_in_expansion": sum(1 for r in accepted if str(r.get("expansion_external_id") or "") == EXPANSION_ID),
        "residual_products": len(residual_products),
        "residual_prints": len(residual_prints),
        "classifications": dict(classifications),
        "cardmarket_rarity_suffix_histogram": dict(suffix_hist),
        "canonical_residual_variant_histogram": dict(variant_hist),
        "rarity_exact_unique_proposals": len(proposal),
        "proposal": proposal,
        "residual_detail": residual_detail,
    }
    output = os.getenv("YGO_AGOV_JP_RESIDUAL_OUTPUT", "/tmp/yugioh-agov-jp-residual-v1.json")
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
