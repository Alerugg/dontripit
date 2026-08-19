from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_collector_number, normalize_variant

GAME = "onepiece"
JP_BASE = "https://www.onepiece-cardgame.com/cardlist/"
SET_TOKEN = "OP16"
LANGUAGE = "ja"
IDENTIFIER_SOURCE = "onepiece_official_jp"
IMAGE_SOURCE = "onepiece_official_jp"
EXPECTED_PHYSICAL = 149
EXPECTED_LOGICAL = 119
EXPECTED_TARGET_VARIANTS = {"default", "p1"}
TARGET_COLLECTOR = "OP16-119"
CONFIRM = "APPLY_ONEPIECE_JP_OP16_PHYSICAL_V1"


def _norm_set(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_onepiece_jp_op16_physical_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_official_jp() -> dict:
    connector = OnePieceV2Connector()
    timeout = float(os.getenv("ONEPIECE_HTTP_TIMEOUT", "30"))
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}
    index = requests.get(JP_BASE, timeout=timeout, headers=headers)
    index.raise_for_status()
    options = connector._parse_official_series_options(index.text)
    if not options:
        raise RuntimeError("official Japanese One Piece cardlist returned zero series options")

    direct = [(series_id, label) for series_id, label in options if SET_TOKEN in _norm_set(label)]
    candidates = direct or options
    selected = []
    raw_entries = []
    for series_id, label in candidates:
        response = requests.get(f"{JP_BASE}?series={series_id}", timeout=timeout, headers=headers)
        response.raise_for_status()
        entries = connector._parse_official_cards_page(response.text, base_url=JP_BASE)
        matches = [row for row in entries if _norm_set(row.get("set_code")) == SET_TOKEN]
        if matches:
            selected.append({"series_id": str(series_id), "label": label, "entries": len(matches)})
            raw_entries.extend(matches)
        if matches and not direct:
            break

    if not raw_entries:
        raise RuntimeError("official Japanese OP16 surface not found")

    by_key: dict[tuple[str, str], dict] = {}
    duplicate_drift = []
    for row in raw_entries:
        collector = str(row.get("collector_number") or "").upper().strip()
        variant = str(row.get("variant") or "default").lower().strip()
        key = (collector, variant)
        normalized = {
            "source_print_id": str(row.get("print_id") or "").upper().strip(),
            "collector_number": collector,
            "collector_norm": normalize_collector_number(collector),
            "variant": normalize_variant(variant),
            "rarity": str(row.get("rarity") or "").strip() or None,
            "image_url": str(row.get("image_url") or "").strip(),
        }
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = normalized
        elif existing != normalized:
            duplicate_drift.append({"key": key, "first": existing, "other": normalized})

    physical = sorted(by_key.values(), key=lambda row: (row["collector_number"], row["variant"]))
    logical = {row["collector_number"] for row in physical}
    if duplicate_drift:
        raise RuntimeError(json.dumps({"official_duplicate_identity_drift": duplicate_drift}, default=str))
    if len(physical) != EXPECTED_PHYSICAL or len(logical) != EXPECTED_LOGICAL:
        raise RuntimeError(
            {
                "official_OP16_surface_drift": {
                    "expected_physical": EXPECTED_PHYSICAL,
                    "actual_physical": len(physical),
                    "expected_logical": EXPECTED_LOGICAL,
                    "actual_logical": len(logical),
                }
            }
        )
    missing_images = [row for row in physical if not row["image_url"]]
    if missing_images:
        raise RuntimeError({"official_OP16_missing_images": len(missing_images)})
    hosts = {urlparse(row["image_url"]).netloc.lower() for row in physical}
    if hosts != {"www.onepiece-cardgame.com"}:
        raise RuntimeError({"unexpected_official_JP_image_hosts": sorted(hosts)})
    if len({row["source_print_id"] for row in physical}) != EXPECTED_PHYSICAL:
        raise RuntimeError("official Japanese source print IDs are not unique")
    target = [row for row in physical if row["collector_number"] == TARGET_COLLECTOR]
    if len(target) != 2 or {row["variant"] for row in target} != EXPECTED_TARGET_VARIANTS:
        raise RuntimeError({"OP16_119_JP_variant_drift": target})
    return {"selected_series": selected, "physical": physical}


def _load_state(cur, official: dict) -> dict:
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("One Piece game row missing")
    game_id = int(game["id"])

    cur.execute("SELECT id,code,name,region FROM sets WHERE game_id=%s", (game_id,))
    candidate_sets = [dict(row) for row in cur.fetchall() if _norm_set(row["code"]) == SET_TOKEN]
    if len(candidate_sets) != 1:
        raise RuntimeError({"OP16_set_identity_not_unique": candidate_sets})
    set_row = candidate_sets[0]
    set_id = int(set_row["id"])

    cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (game_id,))
    game_cards_before = int(cur.fetchone()["n"])
    cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (game_id,))
    game_sets_before = int(cur.fetchone()["n"])

    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.rarity,p.variant,p.print_key,c.name card_name
           FROM prints p JOIN cards c ON c.id=p.card_id
           WHERE p.set_id=%s AND lower(coalesce(p.language,''))='en'
           ORDER BY p.collector_number,p.variant,p.id""",
        (set_id,),
    )
    en_prints = [dict(row) for row in cur.fetchall()]
    if len(en_prints) != EXPECTED_PHYSICAL:
        raise RuntimeError({"OP16_EN_physical_baseline_drift": len(en_prints)})

    card_ids_by_collector: dict[str, set[int]] = defaultdict(set)
    en_keys = set()
    for row in en_prints:
        collector = str(row["collector_number"] or "").upper().strip()
        card_ids_by_collector[collector].add(int(row["card_id"]))
        en_keys.add((collector, normalize_variant(row["variant"])))
    if len(card_ids_by_collector) != EXPECTED_LOGICAL:
        raise RuntimeError({"OP16_EN_logical_baseline_drift": len(card_ids_by_collector)})
    non_unique = {collector: sorted(ids) for collector, ids in card_ids_by_collector.items() if len(ids) != 1}
    if non_unique:
        raise RuntimeError({"OP16_EN_collector_card_collisions": non_unique})

    official_rows = official["physical"]
    official_keys = {(row["collector_number"], row["variant"]) for row in official_rows}
    if official_keys != en_keys:
        raise RuntimeError(
            {
                "OP16_JP_EN_physical_key_mismatch": {
                    "only_jp": sorted(official_keys - en_keys),
                    "only_en": sorted(en_keys - official_keys),
                }
            }
        )

    expected = []
    for row in official_rows:
        card_id = next(iter(card_ids_by_collector[row["collector_number"]]))
        print_key = ":".join(
            [
                "onepiece",
                str(set_row["code"]).strip().lower(),
                row["collector_norm"],
                LANGUAGE,
                row["variant"],
            ]
        )
        expected.append({**row, "card_id": card_id, "set_id": set_id, "print_key": print_key})

    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.language,p.rarity,p.variant,p.print_key
           FROM prints p
           WHERE p.set_id=%s AND lower(coalesce(p.language,''))=%s
           ORDER BY p.collector_number,p.variant,p.id""",
        (set_id, LANGUAGE),
    )
    ja_prints = [dict(row) for row in cur.fetchall()]
    if len(ja_prints) not in (0, EXPECTED_PHYSICAL):
        raise RuntimeError({"partial_OP16_JA_surface_blocked": len(ja_prints)})

    ja_by_key = {
        (str(row["collector_number"] or "").upper().strip(), normalize_variant(row["variant"])): row
        for row in ja_prints
    }
    expected_by_key = {(row["collector_number"], row["variant"]): row for row in expected}
    if ja_prints and set(ja_by_key) != set(expected_by_key):
        raise RuntimeError(
            {
                "existing_OP16_JA_key_drift": {
                    "only_existing": sorted(set(ja_by_key) - set(expected_by_key)),
                    "only_expected": sorted(set(expected_by_key) - set(ja_by_key)),
                }
            }
        )

    existing_exact = []
    proposal = []
    for key, target in expected_by_key.items():
        existing = ja_by_key.get(key)
        if existing is None:
            proposal.append(target)
            continue
        failures = []
        if int(existing["card_id"]) != int(target["card_id"]): failures.append("card_id")
        if str(existing.get("rarity") or "") != str(target.get("rarity") or ""): failures.append("rarity")
        if str(existing.get("print_key") or "") != target["print_key"]: failures.append("print_key")
        if str(existing.get("language") or "").lower() != LANGUAGE: failures.append("language")
        if failures:
            raise RuntimeError({"existing_OP16_JA_identity_drift": {"key": key, "failures": failures}})
        existing_exact.append({**target, "print_id": int(existing["print_id"])})

    expected_external_ids = [row["source_print_id"] for row in expected]
    cur.execute(
        """SELECT pi.print_id,pi.external_id,p.collector_number,p.language,p.variant
           FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id
           WHERE pi.source=%s AND pi.external_id=ANY(%s)""",
        (IDENTIFIER_SOURCE, expected_external_ids),
    )
    identifier_claims = [dict(row) for row in cur.fetchall()]
    claim_by_external = defaultdict(list)
    for row in identifier_claims:
        claim_by_external[str(row["external_id"])].append(row)
    for row in existing_exact:
        claims = claim_by_external.get(row["source_print_id"], [])
        if len(claims) != 1 or int(claims[0]["print_id"]) != int(row["print_id"]):
            raise RuntimeError({"existing_OP16_JA_identifier_drift": {"source_print_id": row["source_print_id"], "claims": claims}})
    if not existing_exact and identifier_claims:
        raise RuntimeError({"preexisting_OP16_JP_identifier_claims_without_JA_surface": identifier_claims})

    if existing_exact:
        print_ids = [int(row["print_id"]) for row in existing_exact]
        cur.execute(
            """SELECT print_id,url,is_primary,source FROM print_images WHERE print_id=ANY(%s) ORDER BY print_id,id""",
            (print_ids,),
        )
        image_rows = [dict(row) for row in cur.fetchall()]
        images_by_print = defaultdict(list)
        for image in image_rows:
            images_by_print[int(image["print_id"])].append(image)
        for row in existing_exact:
            images = images_by_print.get(int(row["print_id"]), [])
            exact_primary = [img for img in images if bool(img["is_primary"]) and str(img["url"]) == row["image_url"] and str(img.get("source") or "") == IMAGE_SOURCE]
            if len(exact_primary) != 1:
                raise RuntimeError({"existing_OP16_JA_image_drift": {"print_id": row["print_id"], "images": images}})

    return {
        "game_id": game_id,
        "set_id": set_id,
        "set_code": str(set_row["code"]),
        "set_name": str(set_row["name"]),
        "set_region": str(set_row["region"]),
        "game_cards_before": game_cards_before,
        "game_sets_before": game_sets_before,
        "expected": expected,
        "proposal": proposal,
        "existing_exact": existing_exact,
    }


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    official = _load_official_jp()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            state = _load_state(cur, official)
            report = {
                "mode": "apply" if apply else "dry_run",
                "status": "pass",
                "production_writes": 0,
                "source": IDENTIFIER_SOURCE,
                "official_base_url": JP_BASE,
                "selected_series": official["selected_series"],
                "set_id": state["set_id"],
                "set_code": state["set_code"],
                "set_name": state["set_name"],
                "set_region": state["set_region"],
                "language": LANGUAGE,
                "official_physical": EXPECTED_PHYSICAL,
                "official_logical_collectors": EXPECTED_LOGICAL,
                "en_physical_baseline": EXPECTED_PHYSICAL,
                "game_cards_before": state["game_cards_before"],
                "game_sets_before": state["game_sets_before"],
                "existing_exact_ja": len(state["existing_exact"]),
                "new_ja_prints_ready": len(state["proposal"]),
                "op16_119_expected": [row for row in state["expected"] if row["collector_number"] == TARGET_COLLECTOR],
                "created_print_ids": [],
                "print_rows_written": 0,
                "image_rows_written": 0,
                "identifier_rows_written": 0,
            }
            if not apply:
                conn.rollback()
                return report

            created_ids = []
            for row in state["proposal"]:
                cur.execute(
                    """INSERT INTO prints(set_id,card_id,collector_number,language,rarity,is_foil,variant,print_key)
                       VALUES(%s,%s,%s,%s,%s,false,%s,%s)
                       RETURNING id""",
                    (
                        row["set_id"], row["card_id"], row["collector_number"], LANGUAGE,
                        row["rarity"], row["variant"], row["print_key"],
                    ),
                )
                print_id = int(cur.fetchone()["id"])
                created_ids.append(print_id)
                cur.execute(
                    """INSERT INTO print_images(print_id,url,is_primary,source)
                       VALUES(%s,%s,true,%s)""",
                    (print_id, row["image_url"], IMAGE_SOURCE),
                )
                cur.execute(
                    """INSERT INTO print_identifiers(print_id,source,external_id)
                       VALUES(%s,%s,%s)""",
                    (print_id, IDENTIFIER_SOURCE, row["source_print_id"]),
                )

            cur.execute("SELECT count(*) n FROM cards WHERE game_id=%s", (state["game_id"],))
            cards_after = int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) n FROM sets WHERE game_id=%s", (state["game_id"],))
            sets_after = int(cur.fetchone()["n"])
            if cards_after != state["game_cards_before"] or sets_after != state["game_sets_before"]:
                raise RuntimeError(
                    {
                        "logical_catalog_mutated": {
                            "cards_before": state["game_cards_before"], "cards_after": cards_after,
                            "sets_before": state["game_sets_before"], "sets_after": sets_after,
                        }
                    }
                )

            cur.execute(
                """SELECT count(*) n,count(DISTINCT card_id) cards
                   FROM prints WHERE set_id=%s AND lower(coalesce(language,''))=%s""",
                (state["set_id"], LANGUAGE),
            )
            counts = dict(cur.fetchone())
            if int(counts["n"]) != EXPECTED_PHYSICAL or int(counts["cards"]) != EXPECTED_LOGICAL:
                raise RuntimeError({"post_apply_OP16_JA_surface_failed": counts})

            report["created_print_ids"] = created_ids
            report["print_rows_written"] = len(created_ids)
            report["image_rows_written"] = len(created_ids)
            report["identifier_rows_written"] = len(created_ids)
            report["production_writes"] = len(created_ids) * 3
            report["game_cards_after"] = cards_after
            report["game_sets_after"] = sets_after
            report["ja_physical_after"] = EXPECTED_PHYSICAL
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply official Japanese One Piece OP16 physical prints without creating logical Cards")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/onepiece-jp-op16-physical-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
