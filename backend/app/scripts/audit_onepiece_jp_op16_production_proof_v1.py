from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_variant

JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
SET_TOKEN = "OP16"
EXPECTED_PHYSICAL = 149
EXPECTED_LOGICAL = 119
EXPECTED_GAME_CARDS = 2665
EXPECTED_GAME_SETS = 59
EXPECTED_ONLY_JP = {("OP16-042", "p1")}
EXPECTED_ONLY_EN = {("OP16-011", "p1")}
TARGET_COLLECTOR = "OP16-119"


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _official_surface() -> dict[tuple[str, str], dict]:
    connector = OnePieceV2Connector()
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}
    index = requests.get(JP_BASE, timeout=timeout, headers=headers)
    index.raise_for_status()
    options = connector._parse_official_series_options(index.text)
    direct = [(series_id, label) for series_id, label in options if SET_TOKEN in _norm_set(label)]
    if not direct:
        raise RuntimeError("official Japanese OP16 series option missing")

    rows = []
    for series_id, _label in direct:
        response = requests.get(f"{JP_BASE}?series={series_id}", timeout=timeout, headers=headers)
        response.raise_for_status()
        rows.extend(
            row
            for row in connector._parse_official_cards_page(response.text, base_url=JP_BASE)
            if _norm_set(row.get("set_code")) == SET_TOKEN
        )

    by_key = {}
    for row in rows:
        collector = str(row.get("collector_number") or "").upper().strip()
        variant = normalize_variant(row.get("variant"))
        key = (collector, variant)
        normalized = {
            "external_id": str(row.get("print_id") or "").upper().strip(),
            "rarity": str(row.get("rarity") or "").strip() or None,
            "image_url": str(row.get("image_url") or "").strip(),
        }
        existing = by_key.get(key)
        if existing is not None and existing != normalized:
            raise RuntimeError({"official_duplicate_identity_drift": {"key": key, "first": existing, "other": normalized}})
        by_key[key] = normalized

    if len(by_key) != EXPECTED_PHYSICAL or len({collector for collector, _ in by_key}) != EXPECTED_LOGICAL:
        raise RuntimeError({"official_surface_drift": {"physical": len(by_key), "logical": len({c for c, _ in by_key})}})
    if any(not row["image_url"].startswith("https://www.onepiece-cardgame.com/") for row in by_key.values()):
        raise RuntimeError("official Japanese OP16 image host drift")
    return by_key


def main() -> int:
    official = _official_surface()
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_onepiece_jp_op16_proof_v1")
    conn.set_session(readonly=True, autocommit=False)
    failures = []
    report = {"production_writes": 0}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug='onepiece' LIMIT 1")
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
            cards = int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
            sets = int(cur.fetchone()["n"])
            if cards != EXPECTED_GAME_CARDS:
                failures.append(f"onepiece_cards_{cards}_expected_{EXPECTED_GAME_CARDS}")
            if sets != EXPECTED_GAME_SETS:
                failures.append(f"onepiece_sets_{sets}_expected_{EXPECTED_GAME_SETS}")

            cur.execute("SELECT id,code FROM sets WHERE game_id=%s", (game_id,))
            matches = [dict(row) for row in cur.fetchall() if _norm_set(row["code"]) == SET_TOKEN]
            if len(matches) != 1:
                raise RuntimeError({"OP16_set_not_unique": matches})
            set_id = int(matches[0]["id"])

            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.variant,p.rarity,p.print_key
                   FROM prints p WHERE p.set_id=%s
                   ORDER BY p.language,p.collector_number,p.variant,p.id""",
                (set_id,),
            )
            prints = [dict(row) for row in cur.fetchall()]
            language_counts = Counter(str(row.get("language") or "").lower() for row in prints)
            if language_counts != Counter({"en": 149, "ja": 149}):
                failures.append(f"language_counts_{dict(language_counts)}")

            en = [row for row in prints if str(row.get("language") or "").lower() == "en"]
            ja = [row for row in prints if str(row.get("language") or "").lower() == "ja"]
            if len({int(row["card_id"]) for row in en}) != EXPECTED_LOGICAL:
                failures.append("EN_logical_card_count")
            if len({int(row["card_id"]) for row in ja}) != EXPECTED_LOGICAL:
                failures.append("JA_logical_card_count")

            en_keys = {(str(row["collector_number"]).upper(), normalize_variant(row["variant"])) for row in en}
            ja_keys = {(str(row["collector_number"]).upper(), normalize_variant(row["variant"])) for row in ja}
            if ja_keys - en_keys != EXPECTED_ONLY_JP:
                failures.append(f"regional_only_jp_{sorted(ja_keys-en_keys)}")
            if en_keys - ja_keys != EXPECTED_ONLY_EN:
                failures.append(f"regional_only_en_{sorted(en_keys-ja_keys)}")
            if ja_keys != set(official):
                failures.append("JA_keys_do_not_match_live_official_surface")

            ja_by_key = {(str(row["collector_number"]).upper(), normalize_variant(row["variant"])): row for row in ja}
            ja_ids = [int(row["print_id"]) for row in ja]
            cur.execute(
                """SELECT pi.print_id,pi.url,pi.is_primary,pi.source
                   FROM print_images pi WHERE pi.print_id=ANY(%s) ORDER BY pi.print_id,pi.id""",
                (ja_ids,),
            )
            images = [dict(row) for row in cur.fetchall()]
            primary_by_print = {}
            for row in images:
                if bool(row["is_primary"]):
                    primary_by_print.setdefault(int(row["print_id"]), []).append(row)

            cur.execute(
                """SELECT pi.print_id,pi.external_id
                   FROM print_identifiers pi
                   WHERE pi.source='onepiece_official_jp' AND pi.print_id=ANY(%s)""",
                (ja_ids,),
            )
            identifiers = [dict(row) for row in cur.fetchall()]
            id_by_print = {int(row["print_id"]): str(row["external_id"]) for row in identifiers}
            if len(identifiers) != EXPECTED_PHYSICAL or len(id_by_print) != EXPECTED_PHYSICAL:
                failures.append(f"official_identifier_count_{len(identifiers)}")
            if len(set(id_by_print.values())) != EXPECTED_PHYSICAL:
                failures.append("official_identifier_external_id_not_unique")

            exact_official_matches = 0
            mismatch_rows = []
            for key, expected in official.items():
                db = ja_by_key.get(key)
                if db is None:
                    mismatch_rows.append({"key": key, "reason": "missing_db_print"})
                    continue
                print_id = int(db["print_id"])
                primaries = primary_by_print.get(print_id, [])
                exact_primary = [
                    row for row in primaries
                    if str(row.get("source") or "") == "onepiece_official_jp"
                    and str(row.get("url") or "") == expected["image_url"]
                ]
                if len(exact_primary) != 1:
                    mismatch_rows.append({"key": key, "reason": "image", "primaries": primaries})
                    continue
                if id_by_print.get(print_id) != expected["external_id"]:
                    mismatch_rows.append({"key": key, "reason": "identifier", "actual": id_by_print.get(print_id), "expected": expected["external_id"]})
                    continue
                if str(db.get("rarity") or "") != str(expected.get("rarity") or ""):
                    mismatch_rows.append({"key": key, "reason": "rarity", "actual": db.get("rarity"), "expected": expected.get("rarity")})
                    continue
                exact_official_matches += 1
            if mismatch_rows:
                failures.append(f"official_exact_mismatch_{len(mismatch_rows)}")

            cur.execute(
                """SELECT count(*) n FROM print_identifiers
                   WHERE source='punk_records' AND print_id=ANY(%s)""",
                (ja_ids,),
            )
            punk_on_ja = int(cur.fetchone()["n"])
            if punk_on_ja:
                failures.append(f"punk_records_identifiers_on_ja_{punk_on_ja}")

            cur.execute(
                """SELECT count(*) n FROM search_documents
                   WHERE doc_type='print' AND object_id=ANY(%s)""",
                (ja_ids,),
            )
            search_docs = int(cur.fetchone()["n"])
            if search_docs != EXPECTED_PHYSICAL:
                failures.append(f"JA_search_docs_{search_docs}")

            cur.execute(
                """SELECT count(*) n
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   WHERE e.source='cardmarket' AND l.print_id=ANY(%s)
                     AND l.link_status IN ('accepted','mapped','exact')""",
                (ja_ids,),
            )
            cardmarket_links = int(cur.fetchone()["n"])
            if cardmarket_links:
                failures.append(f"false_cardmarket_links_on_JA_{cardmarket_links}")

            cur.execute(
                """SELECT count(*) n
                   FROM price_snapshots ps JOIN price_sources src ON src.id=ps.source_id
                   WHERE src.name='cardmarket' AND ps.entity_type='print' AND ps.entity_id=ANY(%s)""",
                (ja_ids,),
            )
            cardmarket_price_rows = int(cur.fetchone()["n"])
            if cardmarket_price_rows:
                failures.append(f"false_cardmarket_price_rows_on_JA_{cardmarket_price_rows}")

            target = [row for row in ja if str(row["collector_number"]).upper() == TARGET_COLLECTOR]
            target_proof = []
            for row in sorted(target, key=lambda x: str(x["variant"])):
                print_id = int(row["print_id"])
                target_proof.append(
                    {
                        "print_id": print_id,
                        "card_id": int(row["card_id"]),
                        "variant": str(row["variant"]),
                        "rarity": row["rarity"],
                        "external_id": id_by_print.get(print_id),
                        "image_url": (primary_by_print.get(print_id) or [{}])[0].get("url"),
                    }
                )
            if len(target_proof) != 2 or {row["variant"] for row in target_proof} != {"default", "p1"}:
                failures.append("OP16_119_variant_proof_failed")

            report.update(
                {
                    "status": "pass" if not failures else "fail",
                    "failures": failures,
                    "onepiece_cards": cards,
                    "onepiece_sets": sets,
                    "op16_language_counts": dict(language_counts),
                    "ja_physical": len(ja),
                    "ja_logical_cards": len({int(row["card_id"]) for row in ja}),
                    "ja_exact_live_official_matches": exact_official_matches,
                    "ja_primary_image_rows": sum(len(rows) for rows in primary_by_print.values()),
                    "ja_official_identifiers": len(identifiers),
                    "ja_search_documents": search_docs,
                    "punk_records_identifiers_on_ja": punk_on_ja,
                    "accepted_cardmarket_links_on_ja": cardmarket_links,
                    "cardmarket_price_rows_on_ja": cardmarket_price_rows,
                    "regional_variant_delta": {
                        "only_jp": sorted(ja_keys - en_keys),
                        "only_en": sorted(en_keys - ja_keys),
                    },
                    "op16_119_ja": target_proof,
                    "mismatch_rows": mismatch_rows,
                }
            )
            conn.rollback()
    finally:
        conn.close()

    out = Path(os.getenv("ONEPIECE_JP_OP16_PROOF_OUTPUT", "/tmp/onepiece-jp-op16-production-proof-v1.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
