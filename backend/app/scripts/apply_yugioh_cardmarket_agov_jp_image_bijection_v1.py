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
EXPECTED_PAIRS = 30
EXPECTED_ACCEPTED_AFTER = 89
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_AGOV_JP_IMAGE_BIJECTION_V1"
AUDIT_RUN_ID = 32189565029
AUDIT_HEAD_SHA = "cbd7df811fe3c995058e7d8930da4b5ba60e21a3"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_agov_jp_image_bijection_v1.csv"


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_agov_jp_image_bijection_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    if len(rows) != EXPECTED_PAIRS:
        raise RuntimeError(f"manifest pair count drifted: expected={EXPECTED_PAIRS} actual={len(rows)}")
    product_ids = [str(row["idProduct"]) for row in rows]
    print_ids = [int(row["print_id"]) for row in rows]
    if len(set(product_ids)) != EXPECTED_PAIRS or len(set(print_ids)) != EXPECTED_PAIRS:
        raise RuntimeError("manifest is not globally one-to-one")
    for row in rows:
        if float(row["minimum_relative_assignment_gap"]) < 0.03:
            raise RuntimeError(f"manifest contains below-threshold image assignment: {row['idProduct']}")
        for key in ("product_image_sha256", "canonical_image_sha256"):
            value = str(row.get(key) or "")
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.casefold()):
                raise RuntimeError(f"invalid {key} for {row['idProduct']}")
    return rows


def _load_state(cur, manifest: list[dict]):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("Yu-Gi-Oh game row not found")
    game_id = int(game["id"])
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("No current Cardmarket capture")

    wanted_products = [str(row["idProduct"]) for row in manifest]
    cur.execute(
        """
        SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.metacard_external_id,
               e.expansion_external_id,e.last_seen_at
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.external_id=ANY(%s) AND e.last_seen_at=%s
        ORDER BY e.external_id::bigint
        """,
        (game_id, wanted_products, capture),
    )
    products = {str(row["id_product"]): dict(row) for row in cur.fetchall()}

    wanted_prints = [int(row["print_id"]) for row in manifest]
    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
               c.name AS card_name,s.code AS set_code
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE p.id=ANY(%s) AND c.game_id=%s
        ORDER BY p.id
        """,
        (wanted_prints, game_id),
    )
    prints = {int(row["print_id"]): dict(row) for row in cur.fetchall()}

    cur.execute(
        """
        SELECT l.external_product_id,l.print_id,l.link_status,e.external_id AS id_product
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s)
        """,
        (game_id, list(ACCEPTED)),
    )
    accepted = [dict(row) for row in cur.fetchall()]
    return capture, products, prints, accepted


def _validate(manifest: list[dict], products: dict[str, dict], prints: dict[int, dict], accepted: list[dict]) -> list[dict]:
    claimed_product_rows: dict[str, list[dict]] = {}
    claimed_print_rows: dict[int, list[dict]] = {}
    for row in accepted:
        claimed_product_rows.setdefault(str(row["id_product"]), []).append(row)
        claimed_print_rows.setdefault(int(row["print_id"]), []).append(row)

    proposal = []
    errors = []
    for cert in manifest:
        pid = str(cert["idProduct"])
        print_id = int(cert["print_id"])
        product = products.get(pid)
        print_row = prints.get(print_id)
        if not product:
            errors.append({"idProduct": pid, "error": "current_cardmarket_product_missing"})
            continue
        if not print_row:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "canonical_print_missing"})
            continue

        checks = {
            "expansion": str(product.get("expansion_external_id") or "") == EXPANSION_ID,
            "metacard": str(product.get("metacard_external_id") or "") == str(cert["idMetacard"]),
            "market_name": str(product.get("name") or "") == str(cert["card_name"]),
            "canonical_name": str(print_row.get("card_name") or "") == str(cert["card_name"]),
            "collector_number": str(print_row.get("collector_number") or "") == str(cert["collector_number"]),
            "variant": str(print_row.get("variant") or "") == str(cert["canonical_variant"]),
            "rarity": str(print_row.get("rarity") or "") == str(cert["canonical_rarity"]),
            "language": str(print_row.get("language") or "").casefold() == LANGUAGE,
            "set_code": str(print_row.get("set_code") or "").upper() == SET_CODE,
        }
        failed = [key for key, value in checks.items() if not value]
        if failed:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "identity_guard_failed", "failed": failed})
            continue

        product_claims = claimed_product_rows.get(pid, [])
        print_claims = claimed_print_rows.get(print_id, [])
        exact_existing = [row for row in product_claims if int(row["print_id"]) == print_id]
        foreign_product_claims = [row for row in product_claims if int(row["print_id"]) != print_id]
        foreign_print_claims = [row for row in print_claims if str(row["id_product"]) != pid]
        if foreign_product_claims or foreign_print_claims:
            errors.append(
                {
                    "idProduct": pid,
                    "print_id": print_id,
                    "error": "global_one_to_one_conflict",
                    "foreign_product_claims": foreign_product_claims,
                    "foreign_print_claims": foreign_print_claims,
                }
            )
            continue
        if exact_existing:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "pair_already_accepted"})
            continue

        proposal.append(
            {
                **cert,
                "external_product_id": int(product["external_product_id"]),
                "card_id": int(print_row["card_id"]),
            }
        )

    if errors:
        raise RuntimeError(json.dumps({"manifest_validation_errors": errors}, default=str))
    if len(proposal) != EXPECTED_PAIRS:
        raise RuntimeError(f"proposal drifted: expected={EXPECTED_PAIRS} actual={len(proposal)}")
    return proposal


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest = _load_manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            capture, products, prints, accepted = _load_state(cur, manifest)
            proposal = _validate(manifest, products, prints, accepted)
            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "game": GAME,
                "cardmarket_capture": str(capture),
                "certified_region": {
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "canonical_set": SET_CODE,
                    "language": LANGUAGE,
                },
                "certification": {
                    "workflow_run_id": AUDIT_RUN_ID,
                    "head_sha": AUDIT_HEAD_SHA,
                    "method": "global_minimum_image_bijection_consensus_v1",
                    "metrics": ["pixel_mae", "dhash_distance", "ahash_distance"],
                    "minimum_required_relative_assignment_gap": 0.03,
                },
                "manifest_pairs": len(manifest),
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
                        "cardmarket_metacard_to_canonical_card",
                        "canonical_JA_AGOV_print",
                        "first_party_cardmarket_product_image",
                        "exact_canonical_print_image",
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
                    "collector_number": str(row["collector_number"]),
                    "canonical_variant": str(row["canonical_variant"]),
                    "canonical_rarity": str(row["canonical_rarity"]),
                    "product_image_sha256": str(row["product_image_sha256"]),
                    "canonical_image_sha256": str(row["canonical_image_sha256"]),
                    "minimum_relative_assignment_gap": float(row["minimum_relative_assignment_gap"]),
                }
                cur.execute(
                    """
                    INSERT INTO external_catalog_print_links(
                        external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                    ) VALUES(%s,%s,'cardmarket_ocg_certified_image_bijection_v1','exact','accepted',true,%s)
                    ON CONFLICT(external_product_id,print_id) DO UPDATE SET
                        mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,
                        evidence=EXCLUDED.evidence,updated_at=now()
                    """,
                    (int(row["external_product_id"]), int(row["print_id"]), Json(evidence)),
                )

            cur.execute(
                """
                SELECT count(*) AS n
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=(SELECT id FROM games WHERE slug=%s)
                  AND e.product_group='single' AND e.expansion_external_id=%s
                  AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))=%s
                  AND upper(coalesce(s.code,''))=%s
                """,
                (GAME, EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
            )
            accepted_after = int(cur.fetchone()["n"])
            if accepted_after != EXPECTED_ACCEPTED_AFTER:
                raise RuntimeError(
                    f"post-apply accepted AGOV-JP proof failed: expected={EXPECTED_ACCEPTED_AFTER} actual={accepted_after}"
                )
            report["accepted_agov_jp_ja_links_after"] = accepted_after
            report["production_writes"] = len(proposal)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only frozen image-bijection-certified AGOV-JP Cardmarket pairs")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-agov-jp-image-bijection-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
