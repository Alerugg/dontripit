from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor


ACCEPTED = ("accepted", "mapped", "exact")
CERTIFIED_REGIONAL_EXPANSIONS = {
    ("yugioh", "5421"): {"code": "AGOV-JP", "expected_language": "ja", "region": "ocg_japan"},
    ("onepiece", "6606"): {"code": "OP16-JP", "expected_language": "ja", "region": "asia_region_legal"},
}
CONFIRM = "APPLY_CARDMARKET_CERTIFIED_REGION_LANGUAGE_INTEGRITY_V1"


def _connect(*, readonly: bool):
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_cardmarket_certified_region_language_integrity_v1",
    )
    conn.set_session(readonly=readonly, autocommit=False)
    return conn


def _find_mismatches(cur):
    conditions = []
    params: list[str] = []
    for (game, expansion_id), cfg in CERTIFIED_REGIONAL_EXPANSIONS.items():
        conditions.append(
            "(g.slug=%s AND e.expansion_external_id=%s AND lower(coalesce(p.language,''))<>%s)"
        )
        params.extend([game, expansion_id, cfg["expected_language"]])
    cur.execute(
        f"""
        SELECT l.id AS link_id,l.external_product_id,l.print_id,l.mapping_method,l.confidence,l.link_status,l.reviewed,l.evidence,
               e.external_id AS id_product,e.expansion_external_id,e.name AS market_name,g.slug AS game,
               p.language,p.collector_number,p.rarity,p.variant,c.name AS card_name,s.code AS set_code,s.name AS set_name
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        JOIN games g ON g.id=e.game_id
        JOIN prints p ON p.id=l.print_id
        JOIN cards c ON c.id=p.card_id
        JOIN sets s ON s.id=p.set_id
        WHERE e.source='cardmarket' AND e.product_group='single'
          AND l.link_status=ANY(%s)
          AND ({' OR '.join(conditions)})
        ORDER BY g.slug,e.expansion_external_id,e.external_id,l.id
        """,
        [list(ACCEPTED), *params],
    )
    return [dict(row) for row in cur.fetchall()]


def _cardmarket_source_id(cur):
    cur.execute("SELECT id FROM price_sources WHERE name='cardmarket' LIMIT 1")
    row = cur.fetchone()
    return int(row["id"]) if row else None


def run(*, apply: bool, confirm: str = "") -> dict:
    if apply and confirm != CONFIRM:
        raise RuntimeError(f"--apply requires --confirm {CONFIRM}")

    conn = _connect(readonly=not apply)
    report = {
        "mode": "apply" if apply else "dry_run",
        "certified_regional_expansions": {
            f"{game}:{expansion}": cfg
            for (game, expansion), cfg in CERTIFIED_REGIONAL_EXPANSIONS.items()
        },
        "production_writes": 0,
    }
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            before = _find_mismatches(cur)
            report["accepted_language_mismatches_before"] = len(before)
            report["mismatches_before"] = before
            if not apply:
                conn.rollback()
                return report

            affected_print_ids = sorted({int(row["print_id"]) for row in before})
            for row in before:
                evidence = dict(row.get("evidence") or {})
                key = (str(row["game"]), str(row["expansion_external_id"]))
                cfg = CERTIFIED_REGIONAL_EXPANSIONS[key]
                evidence["certified_region_language_integrity_v1"] = {
                    "status": "quarantined",
                    "reason": "certified_regional_cardmarket_product_linked_to_wrong_physical_language",
                    "cardmarket_expansion_external_id": str(row["expansion_external_id"]),
                    "certified_expansion_code": cfg["code"],
                    "certified_region": cfg["region"],
                    "expected_print_language": cfg["expected_language"],
                    "actual_print_language": str(row.get("language") or ""),
                    "idProduct": str(row["id_product"]),
                }
                cur.execute(
                    """
                    UPDATE external_catalog_print_links
                    SET link_status='quarantined',confidence='candidate',reviewed=false,evidence=%s,updated_at=now()
                    WHERE id=%s
                    """,
                    (Json(evidence), int(row["link_id"])),
                )

            deleted_prices = 0
            source_id = _cardmarket_source_id(cur)
            if source_id is not None and affected_print_ids:
                cur.execute(
                    "DELETE FROM price_snapshots WHERE source_id=%s AND entity_type='print' AND entity_id=ANY(%s)",
                    (source_id, affected_print_ids),
                )
                deleted_prices = int(cur.rowcount or 0)

            after = _find_mismatches(cur)
            if after:
                raise RuntimeError(f"Certified regional language mismatches remain after apply: {after[:20]}")

            report.update(
                {
                    "quarantined_links": len(before),
                    "affected_print_ids": affected_print_ids,
                    "deleted_cardmarket_print_price_snapshots": deleted_prices,
                    "accepted_language_mismatches_after": 0,
                    "production_writes": len(before) + deleted_prices,
                }
            )
            conn.commit()
            return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce physical-language integrity for certified regional Cardmarket expansions")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--report", type=Path, default=Path("/tmp/cardmarket-certified-region-language-integrity-v1.json"))
    args = parser.parse_args()
    payload = run(apply=args.apply, confirm=args.confirm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
