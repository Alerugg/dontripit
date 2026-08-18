from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5044"
EXPANSION_CODE = "POTE-JP"
SET_CODE = "POTE"
LANGUAGE = "ja"
ACCEPTED = ("accepted", "mapped", "exact")
EXPECTED_PAIRS = 39
EXPECTED_ACCEPTED_BEFORE = 87
EXPECTED_ACCEPTED_AFTER = 126
EXPECTED_GROUP_HISTOGRAM = {3: 6, 4: 4, 5: 1}
CONFIRM = "APPLY_YUGIOH_CARDMARKET_POTE_JP_VERSION_ORDINAL_V1"
METHOD = "cardmarket_ocg_certified_version_ordinal_v1"
MANIFEST = Path(__file__).resolve().parents[1] / "data" / "yugioh_pote_jp_version_ordinal_v1.csv"
MANIFEST_SHA256 = "2e26e56a09673a36f0f13fe0591edf69c0cd34029e4bd5c1f23601208faf0616"
CALIBRATION_RUN_ID = 32196149052
CALIBRATION_HEAD_SHA = "30599ba3e7346c1528b098738f3b2cd961e71025"
EXPECTED_SEQUENCE = {
    3: ("super", "secret", "prismaticsecret"),
    4: ("ultra", "secret", "ultimate", "prismaticsecret"),
    5: ("ultra", "secret", "ultimate", "prismaticsecret", "ghost"),
}
KURIKARA_METACARD = "408092"
KURIKARA_COLLECTOR = "POTE-JP031"


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_pote_jp_version_ordinal_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_manifest() -> list[dict]:
    raw = MANIFEST.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != MANIFEST_SHA256:
        raise RuntimeError(f"manifest sha256 drifted: expected={MANIFEST_SHA256} actual={digest}")
    rows = [dict(row) for row in csv.DictReader(raw.decode("utf-8").splitlines())]
    if len(rows) != EXPECTED_PAIRS:
        raise RuntimeError(f"manifest pair count drifted: expected={EXPECTED_PAIRS} actual={len(rows)}")
    products = [str(row["idProduct"]) for row in rows]
    prints = [int(row["print_id"]) for row in rows]
    if len(set(products)) != EXPECTED_PAIRS or len(set(prints)) != EXPECTED_PAIRS:
        raise RuntimeError("manifest is not globally one-to-one")

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        row["group_size"] = int(row["group_size"])
        row["ordinal"] = int(row["ordinal"])
        row["print_id"] = int(row["print_id"])
        row["canonical_rarity"] = str(row["canonical_rarity"]).casefold()
        groups[str(row["idMetacard"])].append(row)

    histogram = Counter()
    for meta, group in groups.items():
        group = sorted(group, key=lambda r: int(r["idProduct"]))
        size = len(group)
        histogram[size] += 1
        declared_sizes = {int(r["group_size"]) for r in group}
        if declared_sizes != {size}:
            raise RuntimeError(f"manifest group-size drift for metacard {meta}: {declared_sizes} vs {size}")
        ordinals = [int(r["ordinal"]) for r in group]
        if ordinals != list(range(1, size + 1)):
            raise RuntimeError(f"manifest ordinal drift for metacard {meta}: {ordinals}")
        sequence = tuple(str(r["canonical_rarity"]).casefold() for r in group)
        if sequence != EXPECTED_SEQUENCE.get(size):
            raise RuntimeError(f"manifest rarity sequence drift for metacard {meta}: {sequence}")
    if dict(histogram) != EXPECTED_GROUP_HISTOGRAM:
        raise RuntimeError(f"manifest group histogram drift: expected={EXPECTED_GROUP_HISTOGRAM} actual={dict(histogram)}")

    kuri = groups.get(KURIKARA_METACARD, [])
    if len(kuri) != 5 or {str(r["collector_number"]) for r in kuri} != {KURIKARA_COLLECTOR}:
        raise RuntimeError("Kurikara five-version certification surface drifted")
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

    wanted_products = [str(r["idProduct"]) for r in manifest]
    cur.execute(
        """SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id,
                  e.expansion_external_id,e.last_seen_at
           FROM external_catalog_products e
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.external_id=ANY(%s) AND e.last_seen_at=%s
           ORDER BY e.external_id::bigint""",
        (game_id, wanted_products, capture),
    )
    products = {str(r["id_product"]): dict(r) for r in cur.fetchall()}

    wanted_prints = [int(r["print_id"]) for r in manifest]
    cur.execute(
        """SELECT p.id print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                  c.name card_name,s.code set_code
           FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
           WHERE p.id=ANY(%s) AND c.game_id=%s
           ORDER BY p.id""",
        (wanted_prints, game_id),
    )
    prints = {int(r["print_id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        """SELECT l.id link_id,l.external_product_id,l.print_id,l.mapping_method,l.confidence,
                  l.link_status,l.reviewed,e.external_id id_product,e.metacard_external_id,
                  e.expansion_external_id,p.card_id,p.rarity,p.language,s.code set_code
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


def _validate_pote_calibration(cur, *, game_id: int, capture) -> dict:
    cur.execute(
        """SELECT e.external_id id_product,e.metacard_external_id,l.print_id,p.rarity
           FROM external_catalog_products e
           LEFT JOIN external_catalog_print_links l
             ON l.external_product_id=e.id AND l.link_status=ANY(%s)
           LEFT JOIN prints p ON p.id=l.print_id
           WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
             AND e.expansion_external_id=%s AND e.last_seen_at=%s
           ORDER BY e.metacard_external_id,e.external_id::bigint""",
        (list(ACCEPTED), game_id, EXPANSION_ID, capture),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if len(rows) != 126:
        raise RuntimeError(f"POTE-JP regional product surface drifted: expected=126 actual={len(rows)}")

    by_meta: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_meta[str(row.get("metacard_external_id") or "")].append(row)

    calibrated_counts = Counter()
    contradictions = []
    for meta, group in by_meta.items():
        group = sorted(group, key=lambda r: int(r["id_product"]))
        if len(group) not in (3, 4, 5) or not all(r.get("print_id") is not None for r in group):
            continue
        sequence = tuple(str(r.get("rarity") or "").casefold() for r in group)
        expected = EXPECTED_SEQUENCE[len(group)]
        if sequence != expected:
            contradictions.append({"idMetacard": meta, "size": len(group), "expected": expected, "actual": sequence})
        else:
            calibrated_counts[len(group)] += 1
    if contradictions:
        raise RuntimeError(json.dumps({"POTE_JP_ordinal_calibration_contradictions": contradictions}, default=str))
    if calibrated_counts[3] < 4 or calibrated_counts[4] < 3:
        raise RuntimeError(f"insufficient same-expansion ordinal controls: {dict(calibrated_counts)}")
    return {
        "regional_products": len(rows),
        "verified_3_version_controls": calibrated_counts[3],
        "verified_4_version_controls": calibrated_counts[4],
        "verified_5_version_controls": calibrated_counts[5],
        "three_version_sequence": EXPECTED_SEQUENCE[3],
        "four_version_sequence": EXPECTED_SEQUENCE[4],
        "five_version_sequence": EXPECTED_SEQUENCE[5],
        "calibration_run_id": CALIBRATION_RUN_ID,
        "calibration_head_sha": CALIBRATION_HEAD_SHA,
    }


def _build_proposal(cur, *, game_id: int, capture, manifest: list[dict], products, prints, accepted):
    calibration = _validate_pote_calibration(cur, game_id=game_id, capture=capture)
    accepted_by_product: dict[str, list[dict]] = defaultdict(list)
    accepted_by_print: dict[int, list[dict]] = defaultdict(list)
    for row in accepted:
        accepted_by_product[str(row["id_product"])].append(row)
        accepted_by_print[int(row["print_id"])].append(row)

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in manifest:
        groups[str(row["idMetacard"])].append(row)

    errors = []
    proposal = []
    existing = []
    for meta, certs in groups.items():
        certs = sorted(certs, key=lambda r: int(r["idProduct"]))
        product_names = set()
        card_ids = set()
        card_names = set()
        collectors = set()
        for cert in certs:
            pid = str(cert["idProduct"])
            print_id = int(cert["print_id"])
            product = products.get(pid)
            print_row = prints.get(print_id)
            if not product or not print_row:
                errors.append({"idMetacard": meta, "idProduct": pid, "print_id": print_id, "error": "current_identity_missing"})
                continue
            product_names.add(str(product.get("name") or ""))
            card_ids.add(int(print_row["card_id"]))
            card_names.add(str(print_row.get("card_name") or ""))
            collectors.add(str(print_row.get("collector_number") or ""))
            checks = {
                "region": str(product.get("expansion_external_id") or "") == EXPANSION_ID,
                "metacard": str(product.get("metacard_external_id") or "") == meta,
                "collector": str(print_row.get("collector_number") or "") == str(cert["collector_number"]),
                "rarity": str(print_row.get("rarity") or "").casefold() == str(cert["canonical_rarity"]).casefold(),
                "language": str(print_row.get("language") or "").casefold() == LANGUAGE,
                "set_code": str(print_row.get("set_code") or "").upper() == SET_CODE,
            }
            failed = [key for key, ok in checks.items() if not ok]
            if failed:
                errors.append({"idMetacard": meta, "idProduct": pid, "print_id": print_id, "error": "identity_guard_failed", "failed": failed})
                continue

            p_claims = accepted_by_product.get(pid, [])
            i_claims = accepted_by_print.get(print_id, [])
            exact_existing = [
                row for row in p_claims
                if int(row["print_id"]) == print_id
                and str(row.get("mapping_method") or "") == METHOD
                and str(row.get("confidence") or "") == "exact"
                and bool(row.get("reviewed"))
            ]
            if exact_existing:
                if any(int(row["print_id"]) != print_id for row in p_claims) or any(str(row["id_product"]) != pid for row in i_claims):
                    errors.append({"idProduct": pid, "print_id": print_id, "error": "existing_pair_has_competing_claim"})
                else:
                    existing.append({**cert, "external_product_id": int(product["external_product_id"]), "card_id": int(print_row["card_id"])})
                continue
            if p_claims:
                errors.append({"idProduct": pid, "print_id": print_id, "error": "product_already_claimed", "claims": [int(r["print_id"]) for r in p_claims]})
                continue
            if i_claims:
                errors.append({"idProduct": pid, "print_id": print_id, "error": "print_already_claimed", "claims": [str(r["id_product"]) for r in i_claims]})
                continue
            proposal.append({**cert, "external_product_id": int(product["external_product_id"]), "card_id": int(print_row["card_id"]), "card_name": str(print_row.get("card_name") or "")})

        if len(product_names) != 1 or len(card_names) != 1 or product_names != card_names:
            errors.append({"idMetacard": meta, "error": "product_and_canonical_card_name_disagree", "product_names": sorted(product_names), "card_names": sorted(card_names)})
        if len(card_ids) != 1:
            errors.append({"idMetacard": meta, "error": "manifest_group_spans_multiple_canonical_cards", "card_ids": sorted(card_ids)})
        if collectors != {str(certs[0]["collector_number"])}:
            errors.append({"idMetacard": meta, "error": "collector_surface_disagrees", "collectors": sorted(collectors)})

        cur.execute(
            """SELECT e.external_id id_product FROM external_catalog_products e
               WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                 AND e.expansion_external_id=%s AND e.last_seen_at=%s AND e.metacard_external_id=%s
               ORDER BY e.external_id::bigint""",
            (game_id, EXPANSION_ID, capture, meta),
        )
        actual_products = [str(r["id_product"]) for r in cur.fetchall()]
        expected_products = [str(r["idProduct"]) for r in certs]
        if actual_products != expected_products:
            errors.append({"idMetacard": meta, "error": "complete_metacard_product_surface_drift", "expected": expected_products, "actual": actual_products})

    if errors:
        raise RuntimeError(json.dumps({"version_ordinal_validation_errors": errors}, default=str))
    if len(proposal) + len(existing) != EXPECTED_PAIRS:
        raise RuntimeError(f"manifest coverage failed: proposal={len(proposal)} existing={len(existing)} expected={EXPECTED_PAIRS}")
    if len({str(r["idProduct"]) for r in proposal + existing}) != EXPECTED_PAIRS:
        raise RuntimeError("validated product IDs are not globally one-to-one")
    if len({int(r["print_id"]) for r in proposal + existing}) != EXPECTED_PAIRS:
        raise RuntimeError("validated print IDs are not globally one-to-one")
    return proposal, existing, calibration


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    manifest = _load_manifest()
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            game_id, capture, products, prints, accepted = _load_state(cur, manifest)
            cur.execute(
                """SELECT count(*) n FROM external_catalog_print_links l
                   JOIN external_catalog_products e ON e.id=l.external_product_id
                   JOIN prints p ON p.id=l.print_id JOIN sets s ON s.id=p.set_id
                   WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                     AND e.expansion_external_id=%s AND l.link_status=ANY(%s)
                     AND lower(coalesce(p.language,''))=%s AND upper(coalesce(s.code,''))=%s""",
                (game_id, EXPANSION_ID, list(ACCEPTED), LANGUAGE, SET_CODE),
            )
            accepted_before = int(cur.fetchone()["n"])
            if accepted_before not in (EXPECTED_ACCEPTED_BEFORE, EXPECTED_ACCEPTED_AFTER):
                raise RuntimeError(f"unexpected POTE-JP accepted surface: {accepted_before}")

            proposal, existing, calibration = _build_proposal(
                cur, game_id=game_id, capture=capture, manifest=manifest,
                products=products, prints=prints, accepted=accepted,
            )
            expected_proposal = EXPECTED_PAIRS if accepted_before == EXPECTED_ACCEPTED_BEFORE else 0
            expected_existing = 0 if accepted_before == EXPECTED_ACCEPTED_BEFORE else EXPECTED_PAIRS
            if len(proposal) != expected_proposal or len(existing) != expected_existing:
                raise RuntimeError(
                    f"idempotency surface failed: accepted={accepted_before} proposal={len(proposal)} existing={len(existing)}"
                )

            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "game": GAME,
                "cardmarket_capture": str(capture),
                "certified_region": {"idExpansion": EXPANSION_ID, "expansion_code": EXPANSION_CODE, "canonical_set": SET_CODE, "language": LANGUAGE},
                "mapping_method": METHOD,
                "manifest_sha256": MANIFEST_SHA256,
                "manifest_pairs": len(manifest),
                "accepted_pote_jp_before": accepted_before,
                "same_expansion_calibration": calibration,
                "external_five_version_evidence": {
                    "card": "Kurikara Divincarnate",
                    "collector_number": KURIKARA_COLLECTOR,
                    "verified_cardmarket_version": "V.5 - Holographic Rare",
                    "canonical_equivalence": "ghost",
                    "note": "Cardmarket official product/version surface independently verified before freezing this manifest",
                },
                "proposed_exact_links": len(proposal),
                "already_exact_idempotent_links": len(existing),
                "proposal": proposal,
            }
            if not apply:
                conn.rollback()
                return report

            writes = 0
            for row in proposal:
                evidence = {
                    "source": "cardmarket_official_product_ordinal+yugioh_canonical_physical_prints",
                    "identity_basis": [
                        "certified_cardmarket_expansion_POTE-JP",
                        "complete_metacard_product_surface",
                        "complete_canonical_print_surface",
                        "same_expansion_version_ordinal_calibration",
                        "ordinal_to_rarity_sequence",
                        "exact_collector_number",
                        "exact_JA_language",
                        "global_one_to_one",
                    ],
                    "calibration_workflow_run_id": CALIBRATION_RUN_ID,
                    "calibration_head_sha": CALIBRATION_HEAD_SHA,
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "idProduct": str(row["idProduct"]),
                    "idMetacard": str(row["idMetacard"]),
                    "collector_number": str(row["collector_number"]),
                    "product_ordinal": int(row["ordinal"]),
                    "group_size": int(row["group_size"]),
                    "canonical_rarity": str(row["canonical_rarity"]),
                    "manifest_sha256": MANIFEST_SHA256,
                }
                if str(row["idMetacard"]) == KURIKARA_METACARD:
                    evidence["five_version_external_proof"] = "Cardmarket official POTE-JP031 V.5 is Holographic Rare; canonical ghost is the fifth exact physical rarity"
                cur.execute(
                    """INSERT INTO external_catalog_print_links(
                           external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                       ) VALUES(%s,%s,%s,'exact','accepted',true,%s)
                       ON CONFLICT(external_product_id,print_id) DO NOTHING""",
                    (int(row["external_product_id"]), int(row["print_id"]), METHOD, Json(evidence)),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"atomic insert lost race for idProduct={row['idProduct']} print_id={row['print_id']}")
                writes += 1

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
                raise RuntimeError(f"post-apply POTE-JP count failed: expected={EXPECTED_ACCEPTED_AFTER} actual={total}")
            report["accepted_pote_jp_after"] = total
            report["production_writes"] = writes
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only frozen version-ordinal-certified POTE-JP Cardmarket pairs")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-pote-jp-version-ordinal-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
