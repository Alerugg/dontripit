from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

GAME = "yugioh"
ACCEPTED = ("accepted", "mapped", "exact")
METHODS = (
    "cardmarket_ocg_certified_public_super_secret_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v1",
    "cardmarket_ocg_certified_public_version_contract_v2",
)
EXPECTED = {
    "cardmarket_ocg_certified_public_super_secret_contract_v1": 20,
    "cardmarket_ocg_certified_public_version_contract_v1": 41,
    "cardmarket_ocg_certified_public_version_contract_v2": 40,
}
TOKENS = ("version", "ordinal", "rarity", "contract", "source", "public", "hash", "role", "manifest", "evidence", "run")


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE URL required")
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(v) for v in value[:30]]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in list(value.items())[:50]}
    return str(value)


def _contract_fields(ev: Any) -> dict:
    if not isinstance(ev, dict):
        return {}
    result = {}
    for key, value in ev.items():
        low = str(key).lower()
        if any(token in low for token in TOKENS):
            result[str(key)] = _jsonable(value)
    return result


def main() -> int:
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_ocg_public_contract_inventory_v5",
    )
    conn.set_session(readonly=True, autocommit=False)
    failures = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM games WHERE slug=%s LIMIT 1", (GAME,))
            game = cur.fetchone()
            if not game:
                raise RuntimeError("Yu-Gi-Oh game missing")
            gid = int(game["id"])
            cur.execute(
                "SELECT max(last_seen_at) capture FROM external_catalog_products "
                "WHERE source='cardmarket' AND game_id=%s AND product_group='single'",
                (gid,),
            )
            capture = cur.fetchone()["capture"]

            cur.execute(
                """
                SELECT l.mapping_method,l.external_product_id,l.print_id,l.evidence,
                       e.external_id id_product,e.metacard_external_id,e.expansion_external_id,e.name product_name,e.last_seen_at,
                       p.card_id,p.collector_number,p.rarity,p.variant,p.language,
                       c.name card_name,s.code set_code
                FROM external_catalog_print_links l
                JOIN external_catalog_products e ON e.id=l.external_product_id
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN sets s ON s.id=p.set_id
                WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                  AND l.link_status=ANY(%s) AND l.confidence='exact' AND l.reviewed=true
                  AND lower(coalesce(p.language,''))='ja'
                  AND l.mapping_method=ANY(%s)
                ORDER BY l.mapping_method,s.code,e.metacard_external_id,e.external_id::bigint
                """,
                (gid, list(ACCEPTED), list(METHODS)),
            )
            rows = [dict(r) for r in cur.fetchall()]

            by_method: dict[str, list[dict]] = defaultdict(list)
            for row in rows:
                by_method[str(row["mapping_method"])].append(row)

            method_reports = {}
            for method in METHODS:
                items = by_method.get(method, [])
                if len(items) != EXPECTED[method]:
                    failures.append(f"historical_count_drift:{method}")
                if len({int(r["external_product_id"]) for r in items}) != len(items):
                    failures.append(f"historical_product_collision:{method}")
                if len({int(r["print_id"]) for r in items}) != len(items):
                    failures.append(f"historical_print_collision:{method}")

                groups: dict[tuple[str, str, str, int], list[dict]] = defaultdict(list)
                for row in items:
                    groups[(
                        str(row.get("set_code") or "").upper(),
                        str(row.get("expansion_external_id") or ""),
                        str(row.get("metacard_external_id") or ""),
                        int(row["card_id"]),
                    )].append(row)

                group_reports = []
                arities = Counter()
                contract_keys = Counter()
                all_evidence_keys = Counter()
                for (set_code, expansion_id, meta, card_id), target in sorted(groups.items()):
                    cur.execute(
                        """
                        SELECT e.id external_product_id,e.external_id id_product,e.name,e.metacard_external_id
                        FROM external_catalog_products e
                        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
                          AND e.expansion_external_id=%s AND e.metacard_external_id=%s AND e.last_seen_at=%s
                        ORDER BY e.external_id::bigint
                        """,
                        (gid, expansion_id, meta, capture),
                    )
                    products = [dict(r) for r in cur.fetchall()]
                    cur.execute(
                        """
                        SELECT p.id print_id,p.collector_number,p.rarity,p.variant,c.name card_name
                        FROM prints p
                        JOIN cards c ON c.id=p.card_id
                        JOIN sets s ON s.id=p.set_id
                        WHERE c.game_id=%s AND upper(coalesce(s.code,''))=%s
                          AND lower(coalesce(p.language,''))='ja' AND p.card_id=%s
                        ORDER BY p.collector_number,p.id
                        """,
                        (gid, set_code, card_id),
                    )
                    prints = [dict(r) for r in cur.fetchall()]
                    arities[(len(products), len(prints), len(target))] += 1

                    target_items = []
                    group_contracts = []
                    for row in target:
                        fields = _contract_fields(row.get("evidence"))
                        for key in fields:
                            all_evidence_keys[key] += 1
                        if fields.get("contract_key") is not None:
                            contract_keys[str(fields["contract_key"])] += 1
                        group_contracts.append(fields)
                        target_items.append({
                            "idProduct": str(row["id_product"]),
                            "external_product_id": int(row["external_product_id"]),
                            "print_id": int(row["print_id"]),
                            "collector_number": str(row.get("collector_number") or ""),
                            "rarity": row.get("rarity"),
                            "variant": row.get("variant"),
                            "evidence_contract_fields": fields,
                        })

                    group_reports.append({
                        "set_code": set_code,
                        "idExpansion": expansion_id,
                        "idMetacard": meta,
                        "card_id": card_id,
                        "card_name": str(target[0].get("card_name") or ""),
                        "current_products": [
                            {
                                "ordinal": idx + 1,
                                "external_product_id": int(p["external_product_id"]),
                                "idProduct": str(p["id_product"]),
                                "name": str(p.get("name") or ""),
                            }
                            for idx, p in enumerate(products)
                        ],
                        "canonical_ja_prints": [
                            {
                                "print_id": int(p["print_id"]),
                                "collector_number": str(p.get("collector_number") or ""),
                                "rarity": p.get("rarity"),
                                "variant": p.get("variant"),
                            }
                            for p in prints
                        ],
                        "historical_target_pairs": target_items,
                        "distinct_contract_fields": list({json.dumps(x, sort_keys=True, default=str): x for x in group_contracts}.values()),
                    })

                method_reports[method] = {
                    "rows": len(items),
                    "groups": len(groups),
                    "arity_distribution": {str(k): v for k, v in sorted(arities.items())},
                    "evidence_keys": dict(all_evidence_keys.most_common()),
                    "contract_keys": dict(contract_keys.most_common()),
                    "sets": dict(Counter(str(r.get("set_code") or "") for r in items).most_common()),
                    "group_inventory": group_reports,
                }
            conn.rollback()
    finally:
        conn.close()

    report = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "read_only",
        "production_writes": 0,
        "game": GAME,
        "cardmarket_capture": str(capture),
        "methods": method_reports,
        "failures": failures,
        "contract": {
            "purpose": "recover frozen first-party public version/rarity contracts and compare them to current product/print geometry",
            "stored_mapping_pair_is_not_accepted_as_identity_proof": True,
            "writes_allowed": False,
        },
    }
    out = Path(os.getenv("YGO_OCG_PUBLIC_CONTRACT_INVENTORY_V5_OUTPUT", "/tmp/yugioh-ocg-public-contract-inventory-v5.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
