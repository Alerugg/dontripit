from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5044"
EXPANSION_CODE = "POTE-JP"
SET_CODE = "POTE"
LANGUAGE = "ja"
EXPECTED_PAIRS = 24
EXPECTED_ACCEPTED_AFTER = 87
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_POTE_JP_IMAGE_BIJECTION_V1"
METHOD = "cardmarket_ocg_certified_image_bijection_v2"
AUDIT_RUN_ID = 32193580056
AUDIT_HEAD_SHA = "360dde32e298e50c2cbacf7641f0c23950361db5"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_pote_jp_image_bijection_v1.csv"


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_pote_jp_image_bijection_v1",
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
            value = str(row.get(key) or "").casefold()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise RuntimeError(f"invalid {key} for {row['idProduct']}")
    return rows


def _load_state(cur, manifest: list[dict]):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game = cur.fetchone()
    if not game:
        raise RuntimeError("Yu-Gi-Oh game row not found")
    game_id = int(game["id"])
    cur.execute("SELECT max(last_seen_at) capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("Current Cardmarket capture missing")

    wanted_products = [str(row["idProduct"]) for row in manifest]
    cur.execute(
        """
        SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
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
        SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
               c.name card_name,s.code set_code
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE p.id=ANY(%s) AND c.game_id=%s
        ORDER BY p.id
        """,
        (wanted_prints, game_id),
    )
    prints = {int(row["print_id"]): dict(row) for row in cur.fetchall()}

    cur.execute(
        """
        SELECT l.external_product_id,l.print_id,e.external_id id_product,e.metacard_external_id,
               e.expansion_external_id,p.card_id
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN prints p ON p.id=l.print_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s)
        """,
        (game_id, list(ACCEPTED)),
    )
    accepted = [dict(row) for row in cur.fetchall()]
    return game_id, capture, products, prints, accepted


def _validate_residual_groups(cur, *, game_id: int, capture, manifest: list[dict], accepted: list[dict]) -> None:
    claimed_product_rows = {int(row["external_product_id"]) for row in accepted}
    claimed_print_rows = {int(row["print_id"]) for row in accepted}

    groups: dict[str, list[dict]] = defaultdict(list)
    for cert in manifest:
        groups[str(cert["idMetacard"])].append(cert)

    errors = []
    for meta, certs in groups.items():
        card_names = {str(cert["card_name"]) for cert in certs}
        if len(card_names) != 1:
            errors.append({"idMetacard": meta, "error": "manifest_group_has_multiple_card_names"})
            continue
        card_name = next(iter(card_names))

        cur.execute(
            """
            SELECT e.id external_product_id,e.external_id id_product,e.name
            FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s
              AND e.metacard_external_id=%s
            ORDER BY e.external_id::bigint
            """,
            (game_id, EXPANSION_ID, capture, meta),
        )
        residual_products = [dict(row) for row in cur.fetchall() if int(row["external_product_id"]) not in claimed_product_rows]

        cur.execute(
            """
            SELECT p.id print_id
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))=%s
              AND upper(coalesce(s.code,''))=%s
            ORDER BY p.id
            """,
            (game_id, card_name, LANGUAGE, SET_CODE),
        )
        residual_prints = [dict(row) for row in cur.fetchall() if int(row["print_id"]) not in claimed_print_rows]

        expected_products = {str(cert["idProduct"]) for cert in certs}
        expected_prints = {int(cert["print_id"]) for cert in certs}
        actual_products = {str(row["id_product"]) for row in residual_products}
        actual_prints = {int(row["print_id"]) for row in residual_prints}
        if actual_products != expected_products:
            errors.append(
                {
                    "idMetacard": meta,
                    "card_name": card_name,
                    "error": "residual_product_surface_drift",
                    "expected": sorted(expected_products),
                    "actual": sorted(actual_products),
                }
            )
        if actual_prints != expected_prints:
            errors.append(
                {
                    "idMetacard": meta,
                    "card_name": card_name,
                    "error": "residual_print_surface_drift",
                    "expected": sorted(expected_prints),
                    "actual": sorted(actual_prints),
                }
            )
    if errors:
        raise RuntimeError(json.dumps({"residual_group_validation_errors": errors}, default=str))


def _build_proposal(cur, *, game_id: int, capture, manifest: list[dict], products, prints, accepted) -> list[dict]:
    _validate_residual_groups(cur, game_id=game_id, capture=capture, manifest=manifest, accepted=accepted)

    claimed_products = {str(row["id_product"]) for row in accepted}
    claimed_prints = {int(row["print_id"]) for row in accepted}
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
            "product_name": str(product.get("name") or "") == str(cert["card_name"]),
            "metacard": str(product.get("metacard_external_id") or "") == str(cert["idMetacard"]),
            "canonical_name": str(print_row.get("card_name") or "") == str(cert["card_name"]),
            "collector_number": str(print_row.get("collector_number") or "") == str(cert["collector_number"]),
            "variant": str(print_row.get("variant") or "") == str(cert["canonical_variant"]),
            "rarity": str(print_row.get("rarity") or "") == str(cert["canonical_rarity"]),
            "language": str(print_row.get("language") or "").casefold() == LANGUAGE,
            "set_code": str(print_row.get("set_code") or "").upper() == SET_CODE,
            "product_unclaimed": pid not in claimed_products,
            "print_unclaimed": print_id not in claimed_prints,
        }
        failed = [key for key, ok in checks.items() if not ok]
        if failed:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "identity_guard_failed", "failed": failed})
            continue
        proposal.append(
            {
                **cert,
                "external_product_id": int(product["external_product_id"]),
                "card_id": int(print_row["card_id"]),
            }
        )
    if errors:
        raise RuntimeError(json.dumps({"proposal_validation_errors": errors}, default=str))
    if len(proposal) != EXPECTED_PAIRS:
        raise RuntimeError(f"proposal count drifted: expected={EXPECTED_PAIRS} actual={len(proposal)}")
    if len({int(row["external_product_id"]) for row in proposal}) != EXPECTED_PAIRS:
        raise RuntimeError("proposal product IDs are not one-to-one")
    if len({int(row["print_id"]) for row in proposal}) != EXPECTED_PAIRS:
        raise RuntimeError("proposal print IDs are not one-to-one")
    return proposal


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest = _load_manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id, capture, products, prints, accepted = _load_state(cur, manifest)
            proposal = _build_proposal(
                cur,
                game_id=game_id,
                capture=capture,
                manifest=manifest,
                products=products,
                prints=prints,
                accepted=accepted,
            )
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
                    "method": "global_minimum_image_bijection_consensus_v2",
                    "metrics": ["pixel_mae", "dhash_distance", "ahash_distance"],
                    "minimum_required_relative_assignment_gap": 0.03,
                },
                "mapping_method": METHOD,
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
                        "certified_cardmarket_expansion_POTE-JP",
                        "cardmarket_metacard_to_canonical_card",
                        "complete_unclaimed_metacard_product_surface",
                        "complete_unclaimed_canonical_print_surface",
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
                    ) VALUES(%s,%s,%s,'exact','accepted',true,%s)
                    ON CONFLICT(external_product_id,print_id) DO UPDATE SET
                        mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,
                        evidence=EXCLUDED.evidence,updated_at=now()
                    """,
                    (int(row["external_product_id"]), int(row["print_id"]), METHOD, Json(evidence)),
                )

            cur.execute(
                """
                SELECT count(*) n
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                  AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s
                """,
                (game_id, EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
            )
            total = int(cur.fetchone()["n"])
            if total != EXPECTED_ACCEPTED_AFTER:
                raise RuntimeError(f"post-apply POTE-JP count failed: expected={EXPECTED_ACCEPTED_AFTER} actual={total}")
            report["accepted_pote_jp_ja_links_after"] = total
            report["production_writes"] = len(proposal)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only frozen image-bijection-certified POTE-JP Cardmarket pairs")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-pote-jp-image-bijection-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
