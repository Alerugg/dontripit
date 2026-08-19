from __future__ import annotations

import argparse
import csv
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

GAME = "yugioh"
EXPANSION_ID = "6129"
EXPANSION_CODE = "DUAD-JP"
SET_CODE = "DUAD"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED_SINGLETON_BASELINE = 38
EXPECTED_IMAGE_PAIRS = 76
EXPECTED_ORDINAL_PAIRS = 2
EXPECTED_TARGET_PAIRS = 78
EXPECTED_ACCEPTED_AFTER = 116
IMAGE_METHOD = "cardmarket_ocg_certified_image_bijection_v2"
ORDINAL_METHOD = "cardmarket_ocg_certified_version_ordinal_v1"
CONFIRM = "APPLY_YUGIOH_CARDMARKET_DUAD_MULTIVERSION_V1"
IMAGE_AUDIT_RUN_ID = 32202085860
IMAGE_SOURCE_CONTROL_RUN_ID = 32201582727
ORDINAL_AUDIT_RUN_ID = 32202085871
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_duad_jp_image_bijection_v1.csv"
WAKE = (
    {
        "idProduct": "823713",
        "idMetacard": "448572",
        "print_id": 697205,
        "card_id": 72603,
        "card_name": "WAKE CUP! Mocha",
        "collector_number": "DUAD-JP028",
        "canonical_variant": "rarity-shortprint",
        "canonical_rarity": "shortprint",
        "ordinal": 1,
        "ordinal_role": "base_non_secret",
    },
    {
        "idProduct": "823714",
        "idMetacard": "448572",
        "print_id": 674606,
        "card_id": 72603,
        "card_name": "WAKE CUP! Mocha",
        "collector_number": "DUAD-JP028",
        "canonical_variant": "rarity-prismaticsecret",
        "canonical_rarity": "prismaticsecret",
        "ordinal": 2,
        "ordinal_role": "secret_or_prismaticsecret",
    },
)


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_duad_multiversion_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    if len(rows) != EXPECTED_IMAGE_PAIRS:
        raise RuntimeError({"image_manifest_count_drift": len(rows)})
    for row in rows:
        row["print_id"] = int(row["print_id"])
        row["card_id"] = int(row["card_id"])
        row["minimum_relative_assignment_gap"] = float(row["minimum_relative_assignment_gap"])
        if row["minimum_relative_assignment_gap"] < 0.03:
            raise RuntimeError({"below_threshold_image_pair": row["idProduct"]})
        for key in ("product_image_sha256", "canonical_image_sha256"):
            value = str(row.get(key) or "").casefold()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise RuntimeError({"invalid_image_hash": {"idProduct": row["idProduct"], "field": key}})
        row["mapping_method"] = IMAGE_METHOD
        row["evidence_kind"] = "image"
    target = rows + [{**row, "mapping_method": ORDINAL_METHOD, "evidence_kind": "ordinal"} for row in WAKE]
    if len(target) != EXPECTED_TARGET_PAIRS:
        raise RuntimeError({"target_pair_count_drift": len(target)})
    if len({str(r["idProduct"]) for r in target}) != EXPECTED_TARGET_PAIRS:
        raise RuntimeError("target Cardmarket products are not one-to-one")
    if len({int(r["print_id"]) for r in target}) != EXPECTED_TARGET_PAIRS:
        raise RuntimeError("target canonical prints are not one-to-one")
    return target


def _load_state(cur, target: list[dict]):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("Yu-Gi-Oh game row missing")
    game_id = int(game["id"])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Current Cardmarket capture missing")

    product_ids = [str(r["idProduct"]) for r in target]
    cur.execute(
        """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                  e.expansion_external_id,e.last_seen_at
           FROM external_catalog_products e
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.external_id=ANY(%s) AND e.last_seen_at=%s
           ORDER BY e.external_id::bigint""",
        (game_id, product_ids, capture),
    )
    products = {str(r["id_product"]): dict(r) for r in cur.fetchall()}

    print_ids = [int(r["print_id"]) for r in target]
    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                  c.name card_name,s.code set_code
           FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
           WHERE p.id=ANY(%s) AND c.game_id=%s
           ORDER BY p.id""",
        (print_ids, game_id),
    )
    prints = {int(r["print_id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        """SELECT l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.reviewed,
                  e.external_id id_product,e.expansion_external_id,p.language,s.code set_code
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           JOIN prints p ON p.id=l.print_id
           JOIN sets s ON s.id=p.set_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND l.link_status=ANY(%s)""",
        (game_id, list(ACCEPTED)),
    )
    accepted = [dict(r) for r in cur.fetchall()]
    return game_id, capture, products, prints, accepted


def _build(cur, target: list[dict]) -> dict:
    game_id, capture, products, prints, accepted = _load_state(cur, target)
    if len(products) != EXPECTED_TARGET_PAIRS or len(prints) != EXPECTED_TARGET_PAIRS:
        raise RuntimeError({"target_current_surface_missing": {"products": len(products), "prints": len(prints)}})

    target_product_ids = {str(r["idProduct"]) for r in target}
    target_print_ids = {int(r["print_id"]) for r in target}
    accepted_by_product: dict[str, list[dict]] = {}
    accepted_by_print: dict[int, list[dict]] = {}
    for row in accepted:
        accepted_by_product.setdefault(str(row["id_product"]), []).append(row)
        accepted_by_print.setdefault(int(row["print_id"]), []).append(row)

    proposal = []
    existing_same = []
    errors = []
    for cert in target:
        pid = str(cert["idProduct"])
        print_id = int(cert["print_id"])
        product = products[pid]
        pr = prints[print_id]
        checks = {
            "region": str(product.get("expansion_external_id") or "") == EXPANSION_ID,
            "metacard": str(product.get("metacard_external_id") or "") == str(cert["idMetacard"]),
            "strict_normalized_product_name": _norm(product.get("name")) == _norm(cert["card_name"]),
            "card_id": int(pr["card_id"]) == int(cert["card_id"]),
            "strict_normalized_canonical_name": _norm(pr.get("card_name")) == _norm(cert["card_name"]),
            "collector": str(pr.get("collector_number") or "") == str(cert["collector_number"]),
            "variant": str(pr.get("variant") or "") == str(cert["canonical_variant"]),
            "rarity": str(pr.get("rarity") or "") == str(cert["canonical_rarity"]),
            "language": str(pr.get("language") or "").casefold() == LANGUAGE,
            "set": str(pr.get("set_code") or "").upper() == SET_CODE,
        }
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            errors.append({"idProduct": pid, "print_id": print_id, "failed": failed})
            continue

        product_claims = accepted_by_product.get(pid, [])
        print_claims = accepted_by_print.get(print_id, [])
        same = [r for r in product_claims if int(r["print_id"]) == print_id]
        conflicting_product = [r for r in product_claims if int(r["print_id"]) != print_id]
        conflicting_print = [r for r in print_claims if str(r["id_product"]) != pid]
        if conflicting_product or conflicting_print or len(same) > 1:
            errors.append({
                "idProduct": pid,
                "print_id": print_id,
                "accepted_identity_conflict": True,
                "product_claims": conflicting_product,
                "print_claims": conflicting_print,
                "same_pair_count": len(same),
            })
            continue
        row = {**cert, "external_product_id": int(product["external_product_id"])}
        if same:
            if str(same[0].get("confidence") or "") != "exact" or not bool(same[0].get("reviewed")):
                errors.append({"idProduct": pid, "print_id": print_id, "existing_same_not_exact_reviewed": True})
            existing_same.append(row)
        else:
            proposal.append(row)

    if errors:
        raise RuntimeError(json.dumps({"target_validation_errors": errors}, default=str))
    if len(proposal) + len(existing_same) != EXPECTED_TARGET_PAIRS:
        raise RuntimeError("target accounting drift")

    cur.execute(
        """SELECT count(*) n,count(DISTINCT e.id) products,count(DISTINCT p.id) prints
           FROM external_catalog_print_links l
           JOIN external_catalog_products e ON e.id=l.external_product_id
           JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
             AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",
        (game_id, EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
    )
    counts = dict(cur.fetchone())
    expected_current = EXPECTED_SINGLETON_BASELINE + len(existing_same)
    if (int(counts["n"]), int(counts["products"]), int(counts["prints"])) != (expected_current, expected_current, expected_current):
        raise RuntimeError({"DUAD_current_accepted_surface_drift": {"expected": expected_current, "actual": counts}})

    methods = Counter(str(r["mapping_method"]) for r in target)
    if methods != Counter({IMAGE_METHOD: EXPECTED_IMAGE_PAIRS, ORDINAL_METHOD: EXPECTED_ORDINAL_PAIRS}):
        raise RuntimeError({"target_method_distribution_drift": dict(methods)})
    return {
        "game_id": game_id,
        "capture": capture,
        "proposal": proposal,
        "existing_same": existing_same,
        "accepted_before": expected_current,
    }


def _evidence(row: dict) -> dict:
    common = {
        "source": "cardmarket+yugioh_canonical_physical_identity",
        "idExpansion": EXPANSION_ID,
        "expansion_code": EXPANSION_CODE,
        "idProduct": str(row["idProduct"]),
        "idMetacard": str(row["idMetacard"]),
        "collector_number": str(row["collector_number"]),
        "canonical_variant": str(row["canonical_variant"]),
        "canonical_rarity": str(row["canonical_rarity"]),
        "global_one_to_one": True,
    }
    if row["evidence_kind"] == "image":
        return {
            **common,
            "identity_basis": [
                "certified_DUAD-JP_regional_expansion",
                "complete_2x2_metacard_physical_surface",
                "first_party_cardmarket_product_image",
                "exact_canonical_print_image",
                "global_minimum_bijection_consensus_pixel_dhash_ahash",
                "minimum_assignment_gap_at_least_3_percent",
            ],
            "audit_workflow_run_id": IMAGE_AUDIT_RUN_ID,
            "source_control_workflow_run_id": IMAGE_SOURCE_CONTROL_RUN_ID,
            "product_image_sha256": str(row["product_image_sha256"]),
            "canonical_image_sha256": str(row["canonical_image_sha256"]),
            "minimum_relative_assignment_gap": float(row["minimum_relative_assignment_gap"]),
        }
    return {
        **common,
        "identity_basis": [
            "certified_DUAD-JP_regional_expansion",
            "exact_2x2_metacard_physical_surface",
            "canonical_images_missing_2_of_2",
            "DUAD_specific_ordinal_contract_validated_by_38_image_certified_groups_76_pairs",
            "zero_ordinal_control_exceptions_across_five_base_rarity_classes_and_two_premium_classes",
        ],
        "audit_workflow_run_id": ORDINAL_AUDIT_RUN_ID,
        "ordinal": int(row["ordinal"]),
        "ordinal_role": str(row["ordinal_role"]),
    }


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    target = _load_manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            built = _build(cur, target)
            report = {
                "mode": "apply" if apply else "dry_run",
                "status": "pass",
                "production_writes": 0,
                "game": GAME,
                "cardmarket_capture": str(built["capture"]),
                "idExpansion": EXPANSION_ID,
                "canonical_set": SET_CODE,
                "language": LANGUAGE,
                "image_audit_run_id": IMAGE_AUDIT_RUN_ID,
                "ordinal_audit_run_id": ORDINAL_AUDIT_RUN_ID,
                "certified_image_pairs": EXPECTED_IMAGE_PAIRS,
                "certified_ordinal_pairs": EXPECTED_ORDINAL_PAIRS,
                "target_pairs": EXPECTED_TARGET_PAIRS,
                "already_accepted_same_pair": len(built["existing_same"]),
                "new_links_ready": len(built["proposal"]),
                "accepted_duad_ja_before": built["accepted_before"],
                "proposal": built["proposal"],
            }
            if not apply:
                conn.rollback()
                return report

            for row in built["proposal"]:
                cur.execute(
                    """INSERT INTO external_catalog_print_links(
                           external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                       ) VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (int(row["external_product_id"]), int(row["print_id"]), str(row["mapping_method"]), Json(_evidence(row))),
                )
                if cur.rowcount != 1:
                    raise RuntimeError({"expected_single_insert_failed": {"idProduct": row["idProduct"], "print_id": row["print_id"], "rowcount": cur.rowcount}})

            cur.execute(
                """SELECT count(*) n,count(DISTINCT e.id) products,count(DISTINCT p.id) prints
                   FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                     AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",
                (built["game_id"], EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
            )
            counts = dict(cur.fetchone())
            if (int(counts["n"]), int(counts["products"]), int(counts["prints"])) != (EXPECTED_ACCEPTED_AFTER, EXPECTED_ACCEPTED_AFTER, EXPECTED_ACCEPTED_AFTER):
                raise RuntimeError({"post_apply_DUAD_surface_failed": counts})
            report["accepted_duad_ja_after"] = EXPECTED_ACCEPTED_AFTER
            report["production_writes"] = len(built["proposal"])
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only certified DUAD-JP multiversion Cardmarket physical mappings")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-duad-multiversion-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
