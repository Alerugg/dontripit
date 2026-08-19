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
EXPANSION_ID = "6129"
SET_CODE = "DUAD"
LANGUAGE = "ja"
IMAGE_AUDIT_RUN = 32201582727
PREMIUM_RARITIES = {"secret", "prismaticsecret"}
WAKE_PRODUCTS = (823713, 823714)
WAKE_PRINTS = (674606, 697205)
WAKE_METACARD = "448572"

# Frozen from the 38 strong 2x2 image-bijection controls emitted by run 32201582727.
# Each product/print pair was chosen independently by pixel MAE, dHash and aHash with >=3% gap.
CONTROL_PAIRS = {
    823673: 667517,
    823674: 695586,
    823675: 687619,
    823676: 696199,
    823678: 675121,
    823679: 677503,
    823680: 685182,
    823681: 698362,
    823682: 669605,
    823683: 684817,
    823684: 667588,
    823685: 702195,
    823686: 672924,
    823687: 668040,
    823689: 676763,
    823690: 686743,
    823692: 668722,
    823693: 678254,
    823695: 686556,
    823696: 674365,
    823698: 695748,
    823699: 674921,
    823700: 685563,
    823701: 682340,
    823702: 691362,
    823703: 695374,
    823704: 697714,
    823705: 702442,
    823708: 702572,
    823709: 692210,
    823710: 676113,
    823711: 696090,
    823715: 672336,
    823716: 702394,
    823717: 666950,
    823718: 683024,
    823719: 680984,
    823720: 676054,
    823721: 676159,
    823722: 695274,
    823723: 676257,
    823724: 696577,
    823725: 671783,
    823726: 692164,
    823729: 701693,
    823730: 674891,
    823733: 682798,
    823734: 689292,
    823737: 693135,
    823738: 673864,
    823739: 667743,
    823740: 674762,
    823741: 682251,
    823742: 691528,
    823743: 686056,
    823744: 676891,
    823750: 678499,
    823751: 676025,
    823753: 683501,
    823754: 681643,
    823757: 686168,
    823758: 694494,
    823766: 689457,
    823767: 695390,
    823769: 674056,
    823770: 667786,
    823775: 669043,
    823776: 679937,
    823777: 681323,
    823778: 684247,
    823782: 697691,
    823783: 668257,
    823786: 680225,
    823787: 702767,
    823788: 671803,
    823789: 669994,
}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _connect():
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_duad_ordinal_contract_v1")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def main() -> int:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            all_product_ids = [str(x) for x in sorted(set(CONTROL_PAIRS) | set(WAKE_PRODUCTS))]
            cur.execute(
                """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,e.expansion_external_id,e.last_seen_at
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.external_id=ANY(%s) AND e.last_seen_at=%s
                   ORDER BY e.external_id::bigint""",
                (game_id, all_product_ids, capture),
            )
            products = {int(r["id_product"]): dict(r) for r in cur.fetchall()}

            all_print_ids = sorted(set(CONTROL_PAIRS.values()) | set(WAKE_PRINTS))
            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,s.code set_code
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE p.id=ANY(%s) AND c.game_id=%s""",
                (all_print_ids, game_id),
            )
            prints = {int(r["print_id"]): dict(r) for r in cur.fetchall()}

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status IN ('accepted','mapped','exact')
                     AND (e.external_id=ANY(%s) OR l.print_id=ANY(%s))""",
                (game_id, [str(x) for x in WAKE_PRODUCTS], list(WAKE_PRINTS)),
            )
            wake_claims = [dict(r) for r in cur.fetchall()]
            conn.rollback()
    finally:
        conn.close()

    if len(CONTROL_PAIRS) != 76:
        raise RuntimeError({"frozen_control_pair_count_drift": len(CONTROL_PAIRS)})
    if len(products) != 78:
        raise RuntimeError({"current_product_surface_missing": {"expected": 78, "actual": len(products)}})
    if len(prints) != 78:
        raise RuntimeError({"current_print_surface_missing": {"expected": 78, "actual": len(prints)}})
    if any(str(r.get("expansion_external_id") or "") != EXPANSION_ID for r in products.values()):
        raise RuntimeError("regional expansion drift in frozen controls")
    if any(str(r.get("language") or "").casefold() != LANGUAGE for r in prints.values()):
        raise RuntimeError("non-JA print in DUAD ordinal proof")
    if any(str(r.get("set_code") or "").upper() != SET_CODE for r in prints.values()):
        raise RuntimeError("non-DUAD print in DUAD ordinal proof")
    if wake_claims:
        raise RuntimeError({"wake_surface_already_claimed": wake_claims})

    controls_by_meta: dict[str, list[dict]] = defaultdict(list)
    for id_product, print_id in sorted(CONTROL_PAIRS.items()):
        product = products[id_product]
        print_row = prints[print_id]
        if _norm(product.get("name")) != _norm(print_row.get("card_name")):
            raise RuntimeError({"control_normalized_name_drift": {"idProduct": id_product, "print_id": print_id}})
        controls_by_meta[str(product.get("metacard_external_id") or "")].append(
            {
                "idProduct": id_product,
                "print_id": print_id,
                "rarity": str(print_row.get("rarity") or "").casefold(),
                "card_id": int(print_row["card_id"]),
                "card_name": print_row["card_name"],
            }
        )

    if len(controls_by_meta) != 38:
        raise RuntimeError({"control_group_count_drift": len(controls_by_meta)})

    control_failures = []
    base_rarity_counts = Counter()
    premium_rarity_counts = Counter()
    ordinal_sequences = Counter()
    control_examples = []
    for meta, rows in sorted(controls_by_meta.items(), key=lambda kv: min(x["idProduct"] for x in kv[1])):
        rows = sorted(rows, key=lambda x: x["idProduct"])
        if len(rows) != 2 or len({x["card_id"] for x in rows}) != 1:
            control_failures.append({"idMetacard": meta, "reason": "not_exact_2x2_same_card", "rows": rows})
            continue
        lower, higher = rows
        lower_is_premium = lower["rarity"] in PREMIUM_RARITIES
        higher_is_premium = higher["rarity"] in PREMIUM_RARITIES
        ordinal_sequences[(lower["rarity"], higher["rarity"])] += 1
        base_rarity_counts[lower["rarity"]] += 1
        premium_rarity_counts[higher["rarity"]] += 1
        if lower_is_premium or not higher_is_premium:
            control_failures.append({"idMetacard": meta, "reason": "ordinal_premium_direction_violation", "rows": rows})
        control_examples.append({"idMetacard": meta, "card_name": lower["card_name"], "lower": lower, "higher": higher})

    if control_failures:
        raise RuntimeError({"DUAD_ordinal_contract_control_failure": control_failures})
    if len(base_rarity_counts) < 5:
        raise RuntimeError({"insufficient_base_rarity_diversity": dict(base_rarity_counts)})
    if set(premium_rarity_counts) != PREMIUM_RARITIES:
        raise RuntimeError({"premium_control_classes_drift": dict(premium_rarity_counts)})

    wake_products = sorted((products[x] for x in WAKE_PRODUCTS), key=lambda r: int(r["id_product"]))
    wake_print_rows = [prints[x] for x in WAKE_PRINTS]
    if {str(r.get("metacard_external_id") or "") for r in wake_products} != {WAKE_METACARD}:
        raise RuntimeError("WAKE Cardmarket metacard drift")
    if len({int(r["card_id"]) for r in wake_print_rows}) != 1:
        raise RuntimeError("WAKE canonical prints no longer share one card")
    canonical_name = str(wake_print_rows[0]["card_name"])
    if any(_norm(r.get("name")) != _norm(canonical_name) for r in wake_products):
        raise RuntimeError({"WAKE_normalized_name_drift": [r.get("name") for r in wake_products], "canonical": canonical_name})

    wake_base = [r for r in wake_print_rows if str(r.get("rarity") or "").casefold() not in PREMIUM_RARITIES]
    wake_premium = [r for r in wake_print_rows if str(r.get("rarity") or "").casefold() in PREMIUM_RARITIES]
    if len(wake_base) != 1 or len(wake_premium) != 1:
        raise RuntimeError({"WAKE_not_one_base_one_premium": [(r["print_id"], r["rarity"]) for r in wake_print_rows]})

    lower_product, higher_product = wake_products
    certified_pairs = [
        {
            "idProduct": str(lower_product["id_product"]),
            "external_product_id": int(lower_product["external_product_id"]),
            "idMetacard": WAKE_METACARD,
            "print_id": int(wake_base[0]["print_id"]),
            "card_id": int(wake_base[0]["card_id"]),
            "card_name": canonical_name,
            "collector_number": wake_base[0]["collector_number"],
            "canonical_variant": wake_base[0]["variant"],
            "canonical_rarity": wake_base[0]["rarity"],
            "ordinal": 1,
            "ordinal_role": "base_non_secret",
        },
        {
            "idProduct": str(higher_product["id_product"]),
            "external_product_id": int(higher_product["external_product_id"]),
            "idMetacard": WAKE_METACARD,
            "print_id": int(wake_premium[0]["print_id"]),
            "card_id": int(wake_premium[0]["card_id"]),
            "card_name": canonical_name,
            "collector_number": wake_premium[0]["collector_number"],
            "canonical_variant": wake_premium[0]["variant"],
            "canonical_rarity": wake_premium[0]["rarity"],
            "ordinal": 2,
            "ordinal_role": "secret_or_prismaticsecret",
        },
    ]

    report = {
        "status": "pass",
        "production_writes": 0,
        "cardmarket_capture": str(capture),
        "idExpansion": EXPANSION_ID,
        "canonical_set": SET_CODE,
        "language": LANGUAGE,
        "image_control_run_id": IMAGE_AUDIT_RUN,
        "image_control_groups": len(controls_by_meta),
        "image_control_pairs": len(CONTROL_PAIRS),
        "control_failures": 0,
        "control_rule": "within each DUAD 2-product metacard group, lower idProduct is non-secret base physical rarity and higher idProduct is Secret/Prismatic Secret",
        "base_rarity_counts": dict(sorted(base_rarity_counts.items())),
        "premium_rarity_counts": dict(sorted(premium_rarity_counts.items())),
        "ordinal_sequences": {" | ".join(k): v for k, v in sorted(ordinal_sequences.items())},
        "wake_images_available": False,
        "wake_reason_for_ordinal_fallback": "both canonical WAKE CUP! Mocha physical prints currently lack print_images; image assignment is impossible",
        "wake_certified_pairs": certified_pairs,
    }
    out = Path(os.getenv("YGO_DUAD_JP_ORDINAL_CONTRACT_OUTPUT", "/tmp/yugioh-duad-jp-ordinal-contract-v1.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
