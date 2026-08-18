from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


GAME = "yugioh"
EXPANSION_ID = "5421"
EXPANSION_CODE = "AGOV-JP"
CANONICAL_SET_CODE = "AGOV"
EXPECTED_PROPOSALS = 59
ACCEPTED = ("accepted", "mapped", "exact")
CONFIRM = "APPLY_YUGIOH_CARDMARKET_AGOV_JP_UNIQUE_V1"


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(url, connect_timeout=30, application_name="dontripit_ygo_agov_jp_unique_v1")
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _load_state(cur):
    cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
    game_id = int(cur.fetchone()["id"])
    cur.execute("SELECT max(last_seen_at) AS capture FROM external_catalog_products WHERE source='cardmarket'")
    capture = cur.fetchone()["capture"]
    if capture is None:
        raise RuntimeError("No current Cardmarket capture")

    cur.execute(
        """
        SELECT e.id AS external_product_id,e.external_id AS id_product,e.name,e.metacard_external_id,e.expansion_external_id
        FROM external_catalog_products e
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND e.last_seen_at=%s AND e.expansion_external_id=%s
        ORDER BY e.id
        """,
        (game_id, capture, EXPANSION_ID),
    )
    products = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.collector_number,p.rarity,p.variant,p.language,p.print_key,
               c.name AS card_name,s.id AS set_id,s.code AS set_code,s.name AS set_name,s.region AS set_region
        FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,''))='ja' AND upper(coalesce(s.code,''))=%s
        ORDER BY p.id
        """,
        (game_id, CANONICAL_SET_CODE),
    )
    prints = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT e.metacard_external_id,p.card_id,count(DISTINCT l.id) AS evidence_links
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN prints p ON p.id=l.print_id JOIN cards c ON c.id=p.card_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status=ANY(%s) AND e.metacard_external_id IS NOT NULL
        GROUP BY e.metacard_external_id,p.card_id
        ORDER BY e.metacard_external_id,p.card_id
        """,
        (game_id, list(ACCEPTED)),
    )
    meta_card_rows = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT l.external_product_id,l.print_id,l.link_status,e.external_id AS id_product
        FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single' AND l.link_status=ANY(%s)
        """,
        (game_id, list(ACCEPTED)),
    )
    accepted_links = [dict(row) for row in cur.fetchall()]
    return capture, products, prints, meta_card_rows, accepted_links


def build_proposal(products, prints, meta_card_rows, accepted_links):
    metacard_cards = defaultdict(set)
    metacard_evidence = defaultdict(int)
    for row in meta_card_rows:
        meta = str(row["metacard_external_id"])
        metacard_cards[meta].add(int(row["card_id"]))
        metacard_evidence[(meta, int(row["card_id"]))] += int(row["evidence_links"] or 0)

    products_by_meta = defaultdict(list)
    for product in products:
        if product.get("metacard_external_id") is not None:
            products_by_meta[str(product["metacard_external_id"])].append(product)
    prints_by_card = defaultdict(list)
    for print_row in prints:
        prints_by_card[int(print_row["card_id"])].append(print_row)

    claimed_products = {int(row["external_product_id"]) for row in accepted_links}
    claimed_prints = {int(row["print_id"]) for row in accepted_links}
    proposal = []
    rejected = defaultdict(int)

    for meta, meta_products in sorted(products_by_meta.items()):
        cards = metacard_cards.get(meta, set())
        if len(cards) != 1:
            rejected["metacard_not_one_canonical_card"] += len(meta_products)
            continue
        card_id = next(iter(cards))
        candidates = prints_by_card.get(card_id, [])
        if len(meta_products) != 1 or len(candidates) != 1:
            rejected["regional_variant_ambiguity"] += len(meta_products)
            continue
        product = meta_products[0]
        print_row = candidates[0]
        if norm(product["name"]) != norm(print_row["card_name"]):
            rejected["name_mismatch"] += 1
            continue
        if int(product["external_product_id"]) in claimed_products:
            rejected["product_already_claimed"] += 1
            continue
        if int(print_row["print_id"]) in claimed_prints:
            rejected["print_already_claimed"] += 1
            continue
        proposal.append(
            {
                "external_product_id": int(product["external_product_id"]),
                "idProduct": str(product["id_product"]),
                "idExpansion": EXPANSION_ID,
                "expansion_code": EXPANSION_CODE,
                "idMetacard": meta,
                "print_id": int(print_row["print_id"]),
                "card_id": card_id,
                "card_name": print_row["card_name"],
                "collector_number": print_row["collector_number"],
                "rarity": print_row["rarity"],
                "variant": print_row["variant"],
                "language": print_row["language"],
                "set_code": print_row["set_code"],
                "western_metacard_evidence_links": metacard_evidence[(meta, card_id)],
            }
        )

    product_ids = [row["external_product_id"] for row in proposal]
    print_ids = [row["print_id"] for row in proposal]
    if len(product_ids) != len(set(product_ids)) or len(print_ids) != len(set(print_ids)):
        raise RuntimeError("Proposal is not globally one-to-one")
    if len(proposal) != EXPECTED_PROPOSALS:
        raise RuntimeError(
            f"AGOV-JP deterministic proposal drifted: expected={EXPECTED_PROPOSALS} actual={len(proposal)} rejected={dict(rejected)}"
        )
    return proposal, dict(rejected)


def run(*, apply: bool, confirm: str = ""):
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")
    conn = _connect(readonly=not apply)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            capture, products, prints, meta_rows, accepted = _load_state(cur)
            proposal, rejected = build_proposal(products, prints, meta_rows, accepted)
            report = {
                "mode": "apply" if apply else "dry_run",
                "production_writes": 0,
                "cardmarket_capture": str(capture),
                "game": GAME,
                "certified_region": "ocg_japan",
                "idExpansion": EXPANSION_ID,
                "expansion_code": EXPANSION_CODE,
                "canonical_set_code": CANONICAL_SET_CODE,
                "regional_products": len(products),
                "canonical_ja_prints": len(prints),
                "proposed_exact_links": len(proposal),
                "rejected": rejected,
                "proposal": proposal,
            }
            if not apply:
                conn.rollback()
                return report

            for row in proposal:
                evidence = {
                    "source": "cardmarket+yugioh_canonical_physical",
                    "identity_basis": [
                        "certified_cardmarket_expansion_AGOV-JP",
                        "cardmarket_metacard_to_single_canonical_card_via_existing_accepted_links",
                        "canonical_JA_AGOV_set",
                        "single_cardmarket_product_for_metacard_in_expansion",
                        "single_canonical_JA_print_for_card_in_AGOV",
                        "exact_normalized_card_name",
                        "global_one_to_one",
                    ],
                    "idExpansion": EXPANSION_ID,
                    "expansion_code": EXPANSION_CODE,
                    "idProduct": row["idProduct"],
                    "idMetacard": row["idMetacard"],
                    "collector_number": row["collector_number"],
                    "rarity": row["rarity"],
                    "variant": row["variant"],
                    "western_metacard_evidence_links": row["western_metacard_evidence_links"],
                }
                cur.execute(
                    """
                    INSERT INTO external_catalog_print_links(
                        external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence
                    ) VALUES(%s,%s,'cardmarket_ocg_certified_unique_physical_v1','exact','accepted',true,%s)
                    ON CONFLICT(external_product_id,print_id) DO UPDATE SET
                        mapping_method=EXCLUDED.mapping_method,confidence='exact',link_status='accepted',reviewed=true,
                        evidence=EXCLUDED.evidence,updated_at=now()
                    """,
                    (row["external_product_id"], row["print_id"], Json(evidence)),
                )

            cur.execute(
                """
                SELECT count(*) AS linked
                FROM external_catalog_print_links l JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                WHERE e.source='cardmarket' AND e.game_id=(SELECT id FROM games WHERE slug='yugioh')
                  AND e.product_group='single' AND e.expansion_external_id=%s
                  AND l.link_status=ANY(%s) AND lower(coalesce(p.language,''))='ja'
                """,
                (EXPANSION_ID, list(ACCEPTED)),
            )
            linked = int(cur.fetchone()["linked"])
            if linked != EXPECTED_PROPOSALS:
                raise RuntimeError(f"Post-apply AGOV-JP accepted-link proof failed: {linked}")
            report["accepted_ja_links_after"] = linked
            report["production_writes"] = len(proposal)
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only deterministic metacard-certified unique-print AGOV-JP Cardmarket links")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/yugioh-cardmarket-agov-jp-unique-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
