from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
EXPANSION_CODE = "AGOV-JP"
SET_CODE = "AGOV"
LANGUAGE = "ja"
CANONICAL_CARD_NAME = "T.G. Over Dragonar"
EXPECTED_PAIRS = 3
EXPECTED_ACCEPTED_AFTER = 98
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_AGOV_JP_TG_OVER_TYPO_V1"
METHOD = "cardmarket_ocg_certified_typo_image_bijection_v1"
AUDIT_RUN_ID = 32191790248
AUDIT_HEAD_SHA = "66b29b8f05b229e2144930a043722a7107b46654"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_agov_jp_tg_over_typo_v1.csv"


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_tg_over_typo_apply_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]
    if len(rows) != EXPECTED_PAIRS:
        raise RuntimeError(f"manifest size drifted: {len(rows)}")
    if len({r["idProduct"] for r in rows}) != EXPECTED_PAIRS or len({int(r["print_id"]) for r in rows}) != EXPECTED_PAIRS:
        raise RuntimeError("manifest is not one-to-one")
    for row in rows:
        if float(row["minimum_relative_assignment_gap"]) < 0.03:
            raise RuntimeError(f"below-threshold certificate: {row['idProduct']}")
        for key in ("product_image_sha256", "canonical_image_sha256"):
            value = str(row[key]).casefold()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise RuntimeError(f"invalid {key}: {row['idProduct']}")
    return rows


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest = _manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game_id = int(cur.fetchone()["id"])
            cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
            capture = cur.fetchone()["capture"]

            wanted_products = [str(r["idProduct"]) for r in manifest]
            cur.execute(
                """SELECT id external_product_id,external_id id_product,name,metacard_external_id,expansion_external_id,last_seen_at
                   FROM external_catalog_products
                   WHERE source='cardmarket' AND game_id=%s AND product_group='single'
                     AND external_id=ANY(%s) AND last_seen_at=%s""",
                (game_id, wanted_products, capture),
            )
            products = {str(r["id_product"]): dict(r) for r in cur.fetchall()}

            wanted_prints = [int(r["print_id"]) for r in manifest]
            cur.execute(
                """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,c.name card_name,s.code set_code
                   FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE p.id=ANY(%s) AND c.game_id=%s""",
                (wanted_prints, game_id),
            )
            prints = {int(r["print_id"]): dict(r) for r in cur.fetchall()}

            cur.execute(
                """SELECT l.external_product_id,l.print_id,e.external_id id_product
                   FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND l.link_status=ANY(%s)""",
                (game_id, list(ACCEPTED)),
            )
            accepted = [dict(r) for r in cur.fetchall()]
            claimed_products = {str(r["id_product"]) for r in accepted}
            claimed_prints = {int(r["print_id"]) for r in accepted}

            errors = []
            proposal = []
            for cert in manifest:
                pid = str(cert["idProduct"])
                print_id = int(cert["print_id"])
                product = products.get(pid)
                print_row = prints.get(print_id)
                if not product or not print_row:
                    errors.append({"idProduct": pid, "print_id": print_id, "error": "current_identity_missing"})
                    continue
                checks = {
                    "region": str(product.get("expansion_external_id") or "") == EXPANSION_ID,
                    "market_name": str(product.get("name") or "") == str(cert["market_name"]),
                    "metacard_frozen": str(product.get("metacard_external_id") or "") == str(cert["idMetacard"]),
                    "canonical_name": str(print_row.get("card_name") or "") == CANONICAL_CARD_NAME == str(cert["canonical_card_name"]),
                    "collector": str(print_row.get("collector_number") or "") == str(cert["collector_number"]),
                    "variant": str(print_row.get("variant") or "") == str(cert["canonical_variant"]),
                    "rarity": str(print_row.get("rarity") or "") == str(cert["canonical_rarity"]),
                    "language": str(print_row.get("language") or "").casefold() == LANGUAGE,
                    "set": str(print_row.get("set_code") or "").upper() == SET_CODE,
                    "product_unclaimed": pid not in claimed_products,
                    "print_unclaimed": print_id not in claimed_prints,
                }
                failed = [key for key, ok in checks.items() if not ok]
                if failed:
                    errors.append({"idProduct": pid, "print_id": print_id, "error": "guard_failed", "failed": failed})
                    continue
                proposal.append({**cert, "external_product_id": int(product["external_product_id"]), "card_id": int(print_row["card_id"])})

            # The three certified products/prints must be the entire currently unclaimed physical surface for the canonical card.
            cur.execute(
                """SELECT e.external_id id_product,e.id external_product_id
                   FROM external_catalog_products e
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.external_id=ANY(%s)""",
                (game_id, EXPANSION_ID, capture, wanted_products),
            )
            residual_product_ids = {str(r["id_product"]) for r in cur.fetchall() if str(r["id_product"]) not in claimed_products}
            cur.execute(
                """SELECT p.id print_id FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                   WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",
                (game_id, CANONICAL_CARD_NAME, LANGUAGE, SET_CODE),
            )
            residual_print_ids = {int(r["print_id"]) for r in cur.fetchall() if int(r["print_id"]) not in claimed_prints}
            if residual_product_ids != set(wanted_products):
                errors.append({"error": "residual_product_surface_drift", "actual": sorted(residual_product_ids)})
            if residual_print_ids != set(wanted_prints):
                errors.append({"error": "residual_print_surface_drift", "actual": sorted(residual_print_ids)})
            if errors or len(proposal) != EXPECTED_PAIRS:
                raise RuntimeError(json.dumps({"errors": errors, "proposal_count": len(proposal)}, default=str))

            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "cardmarket_capture": str(capture),
                "mapping_method": METHOD,
                "proposed_exact_links": len(proposal),
                "proposal": proposal,
            }
            if not apply:
                conn.rollback()
                return report

            for row in proposal:
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical_images",
                    "identity_basis": [
                        "certified_cardmarket_expansion_AGOV-JP",
                        "known_cardmarket_naming_typo_Dragner_vs_Dragonar",
                        "complete_three_product_three_print_residual_surface",
                        "first_party_cardmarket_product_images",
                        "exact_canonical_print_images",
                        "global_minimum_bijection_consensus_pixel_dhash_ahash",
                        "minimum_assignment_gap_at_least_3_percent",
                        "global_one_to_one",
                    ],
                    "audit_workflow_run_id": AUDIT_RUN_ID,
                    "audit_head_sha": AUDIT_HEAD_SHA,
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "idProduct": str(row["idProduct"]),
                    "idMetacard": str(row["idMetacard"]),
                    "market_name": str(row["market_name"]),
                    "canonical_card_name": CANONICAL_CARD_NAME,
                    "collector_number": str(row["collector_number"]),
                    "canonical_variant": str(row["canonical_variant"]),
                    "canonical_rarity": str(row["canonical_rarity"]),
                    "product_image_sha256": str(row["product_image_sha256"]),
                    "canonical_image_sha256": str(row["canonical_image_sha256"]),
                    "minimum_relative_assignment_gap": float(row["minimum_relative_assignment_gap"]),
                }
                cur.execute(
                    """INSERT INTO external_catalog_print_links(external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                       VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO UPDATE SET
                         mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,evidence=EXCLUDED.evidence,updated_at=now()""",
                    (int(row["external_product_id"]), int(row["print_id"]), METHOD, Json(evidence)),
                )

            cur.execute(
                """SELECT count(*) n FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                     AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",
                (game_id, EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
            )
            total = int(cur.fetchone()["n"])
            if total != EXPECTED_ACCEPTED_AFTER:
                raise RuntimeError(f"post-apply count expected={EXPECTED_ACCEPTED_AFTER} actual={total}")
            report["accepted_agov_jp_ja_links_after"] = total
            report["production_writes"] = EXPECTED_PAIRS
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply frozen T.G. Over Dragonar/Dragner image-certified AGOV-JP mappings")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-agov-jp-tg-over-typo-apply-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
