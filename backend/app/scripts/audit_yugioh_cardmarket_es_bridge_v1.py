from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_cardmarket_es_bridge_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _compact(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _collector_family(value: object) -> str:
    raw = str(value or "").upper().strip().replace(" ", "")
    # Modern western Yu-Gi-Oh collectors use an explicit language segment.
    # Only normalize EN <-> SP/ES here. JP and other regions are deliberately
    # excluded and must be resolved against their own Cardmarket products.
    raw = re.sub(r"-(?:EN|SP|ES)(?=\d)", "-XX", raw)
    return raw


def _rarity(value: object) -> str:
    raw = _compact(value)
    aliases = {
        "COMMON": "common",
        "RARE": "rare",
        "SUPERRARE": "super",
        "SUPER": "super",
        "ULTRARARE": "ultra",
        "ULTRA": "ultra",
        "SECRET RARE": "secret",
        "SECRETRARE": "secret",
        "SECRET": "secret",
        "ULTIMATERARE": "ultimate",
        "ULTIMATE": "ultimate",
        "GHOSTRARE": "ghost",
        "GHOST": "ghost",
        "STARLIGHTRARE": "starlight",
        "STARLIGHT": "starlight",
        "PRISMATICSECRETRARE": "prismaticsecret",
        "PRISMATICSECRET": "prismaticsecret",
        "PLATINUMSECRETRARE": "platinumsecret",
        "PLATINUMSECRET": "platinumsecret",
        "QUARTERCENTURYSECRETRARE": "quartercenturysecret",
        "QUARTERCENTURYSECRET": "quartercenturysecret",
        "COLLECTORSRARE": "collector",
        "COLLECTORRARE": "collector",
        "COMMONPARALLEL": "commonparallel",
        "PARALLELCOMMON": "commonparallel",
        "NORMALPARALLELRARE": "normalparallel",
        "NORMALPARALLEL": "normalparallel",
        "MILLENNIUMRARE": "millennium",
        "MILLENNIUMSUPERRARE": "millenniumsuper",
        "MILLENNIUMULTRARARE": "millenniumultra",
        "MILLENNIUMSECRETRARE": "millenniumsecret",
        "GOLDRARE": "gold",
        "PREMIUMGOLDRARE": "premiumgold",
        "SHATTERFOILRARE": "shatterfoil",
        "MOSAI CRARE": "mosaic",
        "MOSAICRARE": "mosaic",
    }
    return aliases.get(raw, raw.casefold())


def _variant(value: object) -> str:
    raw = str(value or "default").strip().casefold()
    if raw in {"", "default", "base"}:
        return "default"
    if raw.startswith("rarity-"):
        raw = raw[7:]
    return _rarity(raw)


def _identity(row: dict) -> tuple:
    return (
        int(row["card_id"]),
        str(row.get("set_code") or "").upper(),
        _collector_family(row.get("collector_number")),
        bool(row.get("is_foil")),
    )


def _physical_match(es: dict, en: dict) -> bool:
    if _rarity(es.get("rarity")) != _rarity(en.get("rarity")):
        return False
    es_variant = _variant(es.get("variant"))
    en_variant = _variant(en.get("variant"))
    # V3 localized variants are rarity-derived, while legacy EN sometimes uses
    # default. A default EN variant is acceptable only when rarity itself is an
    # exact normalized match; two non-default variant semantics must agree.
    if es_variant != "default" and en_variant != "default" and es_variant != en_variant:
        return False
    return True


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.is_foil,p.variant,
                       s.id AS set_id,s.code AS set_code,s.region AS set_region,s.name AS set_name
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es')
                ORDER BY p.id
                """,
                (game_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT l.print_id,e.id AS market_row_id,e.external_id AS id_product,e.name,e.website_path,
                       l.link_status,l.confidence,l.mapping_method
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status = ANY(%s)
                ORDER BY l.print_id,e.id
                """,
                (game_id, list(ACCEPTED)),
            )
            links = [dict(row) for row in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    en_rows = [row for row in rows if str(row.get("language") or "").lower() == "en"]
    es_rows = [row for row in rows if str(row.get("language") or "").lower() == "es"]
    en_by_identity: dict[tuple, list[dict]] = defaultdict(list)
    for row in en_rows:
        en_by_identity[_identity(row)].append(row)

    links_by_print: dict[int, list[dict]] = defaultdict(list)
    for link in links:
        links_by_print[int(link["print_id"])].append(link)

    buckets = Counter()
    proposals = []
    samples: dict[str, list[dict]] = defaultdict(list)
    product_languages_before: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        for link in links_by_print.get(int(row["print_id"]), []):
            product_languages_before[int(link["market_row_id"])].add(str(row.get("language") or "").lower())

    for es in es_rows:
        existing = links_by_print.get(int(es["print_id"]), [])
        candidates = en_by_identity.get(_identity(es), [])
        collector = str(es.get("collector_number") or "")
        normalized = _collector_family(collector)
        has_explicit_western_marker = "-XX" in normalized

        if not has_explicit_western_marker:
            buckets["collector_not_supported_by_v1"] += 1
            if len(samples["collector_not_supported_by_v1"]) < 20:
                samples["collector_not_supported_by_v1"].append(es)
            continue
        if not candidates:
            buckets["no_en_identity_sibling"] += 1
            if len(samples["no_en_identity_sibling"]) < 20:
                samples["no_en_identity_sibling"].append(es)
            continue

        physical = [en for en in candidates if _physical_match(es, en)]
        if not physical:
            buckets["physical_mismatch"] += 1
            if len(samples["physical_mismatch"]) < 20:
                samples["physical_mismatch"].append({"es": es, "en_candidates": candidates[:6]})
            continue
        if len(physical) != 1:
            buckets["ambiguous_en_physical_sibling"] += 1
            if len(samples["ambiguous_en_physical_sibling"]) < 20:
                samples["ambiguous_en_physical_sibling"].append({"es": es, "en_candidates": physical[:8]})
            continue

        en = physical[0]
        en_links = links_by_print.get(int(en["print_id"]), [])
        unique_market = {int(link["market_row_id"]): link for link in en_links}
        if not unique_market:
            buckets["en_sibling_without_cardmarket"] += 1
            if len(samples["en_sibling_without_cardmarket"]) < 20:
                samples["en_sibling_without_cardmarket"].append({"es": es, "en": en})
            continue
        if len(unique_market) != 1:
            buckets["en_sibling_cardmarket_ambiguous"] += 1
            if len(samples["en_sibling_cardmarket_ambiguous"]) < 20:
                samples["en_sibling_cardmarket_ambiguous"].append({"es": es, "en": en, "links": en_links})
            continue

        market_row_id, market_link = next(iter(unique_market.items()))
        existing_market_ids = {int(link["market_row_id"]) for link in existing}
        if existing_market_ids:
            if existing_market_ids == {market_row_id}:
                buckets["already_linked_same_product"] += 1
                continue
            buckets["existing_es_product_conflict"] += 1
            if len(samples["existing_es_product_conflict"]) < 20:
                samples["existing_es_product_conflict"].append(
                    {"es": es, "en": en, "expected": market_link, "existing": existing}
                )
            continue

        buckets["deterministic_proposal"] += 1
        proposals.append(
            {
                "es_print_id": int(es["print_id"]),
                "en_print_id": int(en["print_id"]),
                "card_id": int(es["card_id"]),
                "set_code": es.get("set_code"),
                "es_collector": es.get("collector_number"),
                "en_collector": en.get("collector_number"),
                "rarity": es.get("rarity"),
                "variant": es.get("variant"),
                "market_row_id": market_row_id,
                "id_product": str(market_link.get("id_product") or ""),
                "market_name": market_link.get("name"),
                "website_path": market_link.get("website_path"),
                "source_link_status": market_link.get("link_status"),
                "source_confidence": market_link.get("confidence"),
            }
        )

    products_after = {key: set(value) for key, value in product_languages_before.items()}
    for proposal in proposals:
        products_after.setdefault(int(proposal["market_row_id"]), set()).add("es")
    before_multi = sum(1 for langs in product_languages_before.values() if len(langs) > 1)
    after_multi = sum(1 for langs in products_after.values() if len(langs) > 1)

    # No proposed ES Print may point to competing products; no market Product may
    # bridge multiple logical Cards through this proposal surface.
    proposal_product_by_es: dict[int, set[int]] = defaultdict(set)
    product_cards: dict[int, set[int]] = defaultdict(set)
    for proposal in proposals:
        proposal_product_by_es[int(proposal["es_print_id"])].add(int(proposal["market_row_id"]))
        product_cards[int(proposal["market_row_id"])].add(int(proposal["card_id"]))
    gates = {
        "production_read_only": True,
        "no_existing_es_product_conflicts": buckets["existing_es_product_conflict"] == 0,
        "all_proposals_one_product_per_es_print": all(len(v) == 1 for v in proposal_product_by_es.values()),
        "all_proposed_products_one_logical_card": all(len(v) == 1 for v in product_cards.values()),
        "ja_never_considered": all(str(row.get("language") or "").lower() in {"en", "es"} for row in rows),
        "proposals_present": bool(proposals),
    }
    report = {
        "status": "pass" if all(gates.values()) else "blocked",
        "production_writes": 0,
        "rule": "same card + same set code + EN/SP collector normalization + same finish + normalized rarity/variant + unique EN Cardmarket product",
        "counts": {
            "en_prints": len(en_rows),
            "es_prints": len(es_rows),
            **dict(sorted(buckets.items())),
            "products_multilingual_before": before_multi,
            "products_multilingual_after_proposal": after_multi,
            "distinct_products_receiving_es": len({p["market_row_id"] for p in proposals}),
        },
        "gates": gates,
        "proposal_samples": proposals[:80],
        "bucket_samples": dict(samples),
    }
    output = os.getenv("YGO_CARDMARKET_ES_BRIDGE_AUDIT_OUTPUT", "/tmp/yugioh-cardmarket-es-bridge-v1.json")
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
