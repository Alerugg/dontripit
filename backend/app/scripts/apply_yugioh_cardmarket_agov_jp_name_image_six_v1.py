from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
EXPANSION_CODE = "AGOV-JP"
SET_CODE = "AGOV"
LANGUAGE = "ja"
EXPECTED_PAIRS = 6
EXPECTED_ACCEPTED_AFTER = 95
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_AGOV_JP_NAME_IMAGE_SIX_V1"
NAME_AUDIT_RUN = 32190878893
IMAGE_AUDIT_RUN = 32190973563
IMAGE_AUDIT_HEAD = "2190bf522e2c866dd1714c2b0921ad3b13473d9b"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_agov_jp_name_image_six_v1.csv"

METHOD_BY_TYPE = {
    "name_image_bijection": "cardmarket_ocg_certified_name_image_bijection_v1",
    "name_singleton": "cardmarket_ocg_certified_name_singleton_v1",
}


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_name_image_six_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    if len(rows) != EXPECTED_PAIRS:
        raise RuntimeError(f"manifest size drift: expected={EXPECTED_PAIRS} actual={len(rows)}")
    if len({r["idProduct"] for r in rows}) != EXPECTED_PAIRS or len({int(r["print_id"]) for r in rows}) != EXPECTED_PAIRS:
        raise RuntimeError("manifest is not globally one-to-one")
    types = Counter(r["certification_type"] for r in rows)
    if types != Counter({"name_image_bijection": 3, "name_singleton": 3}):
        raise RuntimeError(f"unexpected certification-type distribution: {dict(types)}")
    for row in rows:
        if row["certification_type"] not in METHOD_BY_TYPE:
            raise RuntimeError(f"unknown certification type {row['certification_type']}")
        for key in ("product_image_sha256", "canonical_image_sha256"):
            value = str(row.get(key) or "").casefold()
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise RuntimeError(f"invalid {key} for {row['idProduct']}")
        gap = str(row.get("minimum_relative_assignment_gap") or "").strip()
        if row["certification_type"] == "name_image_bijection":
            if not gap or float(gap) < 0.03:
                raise RuntimeError(f"below-threshold image bijection {row['idProduct']}")
        elif gap:
            raise RuntimeError(f"singleton must not fabricate assignment gap: {row['idProduct']}")
    return rows


def _load_state(cur, manifest: list[dict]):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game_id = int(cur.fetchone()["id"])
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]

    pids = [str(r["idProduct"]) for r in manifest]
    cur.execute(
        """
        SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.metacard_external_id,
               e.expansion_external_id,e.last_seen_at
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.external_id=ANY(%s) AND e.last_seen_at=%s
        """,
        (game_id, pids, capture),
    )
    products = {str(r["id_product"]): dict(r) for r in cur.fetchall()}

    print_ids = [int(r["print_id"]) for r in manifest]
    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
               c.name AS card_name,s.code AS set_code
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE p.id=ANY(%s) AND c.game_id=%s
        """,
        (print_ids, game_id),
    )
    prints = {int(r["print_id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        """
        SELECT l.external_product_id,l.print_id,e.external_id AS id_product,e.name AS market_name,
               p.card_id,c.name AS card_name,l.mapping_method
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s)
        """,
        (game_id, list(ACCEPTED)),
    )
    accepted = [dict(r) for r in cur.fetchall()]

    return game_id, capture, products, prints, accepted


def _validate_groups(cur, *, game_id: int, capture, manifest: list[dict], accepted: list[dict]):
    claimed_product_ids = {int(r["external_product_id"]) for r in accepted}
    claimed_print_ids = {int(r["print_id"]) for r in accepted}
    groups = defaultdict(list)
    for cert in manifest:
        groups[cert["card_name"]].append(cert)

    errors = []
    for card_name, certs in groups.items():
        cur.execute(
            """
            SELECT e.id,e.external_id
            FROM external_catalog_products e
            WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
              AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.name=%s
            ORDER BY e.external_id::bigint
            """,
            (game_id, EXPANSION_ID, capture, card_name),
        )
        residual_products = [dict(r) for r in cur.fetchall() if int(r["id"]) not in claimed_product_ids]

        cur.execute(
            """
            SELECT p.id,p.card_id
            FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
            WHERE c.game_id=%s AND c.name=%s AND lower(coalesce(p.language,''))=%s
              AND upper(coalesce(s.code,''))=%s
            ORDER BY p.id
            """,
            (game_id, card_name, LANGUAGE, SET_CODE),
        )
        residual_prints = [dict(r) for r in cur.fetchall() if int(r["id"]) not in claimed_print_ids]

        expected = len(certs)
        if len(residual_products) != expected or len(residual_prints) != expected:
            errors.append(
                {
                    "card_name": card_name,
                    "error": "residual_name_surface_drift",
                    "expected": expected,
                    "residual_products": [str(r["external_id"]) for r in residual_products],
                    "residual_prints": [int(r["id"]) for r in residual_prints],
                }
            )
        if {str(r["external_id"]) for r in residual_products} != {str(c["idProduct"]) for c in certs}:
            errors.append({"card_name": card_name, "error": "residual_product_set_differs_from_manifest"})
        if {int(r["id"]) for r in residual_prints} != {int(c["print_id"]) for c in certs}:
            errors.append({"card_name": card_name, "error": "residual_print_set_differs_from_manifest"})
    if errors:
        raise RuntimeError(json.dumps({"group_validation_errors": errors}, default=str))


def _build_proposal(cur, *, game_id: int, capture, manifest: list[dict], products, prints, accepted) -> list[dict]:
    _validate_groups(cur, game_id=game_id, capture=capture, manifest=manifest, accepted=accepted)
    claims_by_product = defaultdict(list)
    claims_by_print = defaultdict(list)
    for row in accepted:
        claims_by_product[str(row["id_product"])].append(row)
        claims_by_print[int(row["print_id"])].append(row)

    proposal = []
    errors = []
    for cert in manifest:
        pid = str(cert["idProduct"])
        print_id = int(cert["print_id"])
        product = products.get(pid)
        print_row = prints.get(print_id)
        if not product or not print_row:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "missing_current_identity_row"})
            continue
        checks = {
            "expansion": str(product.get("expansion_external_id") or "") == EXPANSION_ID,
            "product_name": str(product.get("name") or "") == str(cert["card_name"]),
            "metacard_stable_value": str(product.get("metacard_external_id") or "") == str(cert["idMetacard"]),
            "card_name": str(print_row.get("card_name") or "") == str(cert["card_name"]),
            "collector_number": str(print_row.get("collector_number") or "") == str(cert["collector_number"]),
            "variant": str(print_row.get("variant") or "") == str(cert["canonical_variant"]),
            "rarity": str(print_row.get("rarity") or "") == str(cert["canonical_rarity"]),
            "language": str(print_row.get("language") or "").casefold() == LANGUAGE,
            "set_code": str(print_row.get("set_code") or "").upper() == SET_CODE,
            "product_unclaimed": not claims_by_product.get(pid),
            "print_unclaimed": not claims_by_print.get(print_id),
        }
        failed = [key for key, ok in checks.items() if not ok]
        if failed:
            errors.append({"idProduct": pid, "print_id": print_id, "error": "identity_guard_failed", "failed": failed})
            continue
        proposal.append({**cert, "external_product_id": int(product["external_product_id"]), "card_id": int(print_row["card_id"])})
    if errors:
        raise RuntimeError(json.dumps({"proposal_validation_errors": errors}, default=str))
    if len(proposal) != EXPECTED_PAIRS:
        raise RuntimeError(f"proposal count drift: expected={EXPECTED_PAIRS} actual={len(proposal)}")
    return proposal


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest = _load_manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id, capture, products, prints, accepted = _load_state(cur, manifest)
            proposal = _build_proposal(cur, game_id=game_id, capture=capture, manifest=manifest, products=products, prints=prints, accepted=accepted)
            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "cardmarket_capture": str(capture),
                "manifest_pairs": len(manifest),
                "proposed_exact_links": len(proposal),
                "certification_type_counts": dict(Counter(r["certification_type"] for r in proposal)),
                "proposal": proposal,
            }
            if not apply:
                conn.rollback()
                return report

            for row in proposal:
                cert_type = str(row["certification_type"])
                method = METHOD_BY_TYPE[cert_type]
                identity_basis = [
                    "certified_cardmarket_expansion_AGOV-JP",
                    "exact_cardmarket_name_unique_in_residual_AGOV_surface",
                    "unique_canonical_card_name_in_residual_AGOV_surface",
                    "balanced_residual_product_print_surface",
                    "first_party_cardmarket_product_image",
                    "exact_canonical_print_image",
                    "global_one_to_one",
                ]
                if cert_type == "name_image_bijection":
                    identity_basis += [
                        "global_minimum_bijection_consensus_pixel_dhash_ahash",
                        "minimum_assignment_gap_at_least_3_percent",
                    ]
                else:
                    identity_basis += ["singleton_one_product_one_print_no_competing_assignment"]
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical_images",
                    "identity_basis": identity_basis,
                    "name_audit_workflow_run_id": NAME_AUDIT_RUN,
                    "image_audit_workflow_run_id": IMAGE_AUDIT_RUN,
                    "image_audit_head_sha": IMAGE_AUDIT_HEAD,
                    "certification_type": cert_type,
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "idProduct": str(row["idProduct"]),
                    "idMetacard": str(row["idMetacard"]),
                    "collector_number": str(row["collector_number"]),
                    "canonical_variant": str(row["canonical_variant"]),
                    "canonical_rarity": str(row["canonical_rarity"]),
                    "product_image_sha256": str(row["product_image_sha256"]),
                    "canonical_image_sha256": str(row["canonical_image_sha256"]),
                    "minimum_relative_assignment_gap": (
                        float(row["minimum_relative_assignment_gap"])
                        if str(row.get("minimum_relative_assignment_gap") or "").strip()
                        else None
                    ),
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
                    (int(row["external_product_id"]), int(row["print_id"]), method, Json(evidence)),
                )

            cur.execute(
                """
                SELECT count(*) AS n
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
                raise RuntimeError(f"post-apply AGOV-JP count failed: expected={EXPECTED_ACCEPTED_AFTER} actual={total}")
            report["accepted_agov_jp_ja_links_after"] = total
            report["production_writes"] = len(proposal)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply six frozen AGOV-JP name/image-certified residual identities")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-agov-jp-name-image-six-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
