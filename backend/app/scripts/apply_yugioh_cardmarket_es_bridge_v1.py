from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import Json, execute_values

from app.scripts.audit_yugioh_cardmarket_es_bridge_v1 import (
    ACCEPTED,
    _collector_family,
    _identity,
    _physical_match,
)


MAPPING_METHOD = "ygo_es_exact_en_cardmarket_product_v1"
EXPECTED_INITIAL_PROPOSALS = 20896


def _url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    return url


def _load_state(cur) -> tuple[int, list[dict], list[dict]]:
    cur.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
    game_id = int(cur.fetchone()[0])
    cur.execute(
        """
        SELECT p.id AS print_id,p.card_id,p.language,p.collector_number,p.rarity,p.is_foil,p.variant,
               s.id AS set_id,s.code AS set_code,s.region AS set_region,s.name AS set_name
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN sets s ON s.id=p.set_id
        WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('en','es')
        ORDER BY p.id
        """,
        (game_id,),
    )
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT l.id AS link_id,l.print_id,e.id AS market_row_id,e.external_id AS id_product,e.name,e.website_path,
               l.link_status,l.confidence,l.mapping_method
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id=l.external_product_id
        WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
          AND l.link_status = ANY(%s)
        ORDER BY l.print_id,e.id
        """,
        (game_id, list(ACCEPTED)),
    )
    columns = [desc[0] for desc in cur.description]
    links = [dict(zip(columns, row)) for row in cur.fetchall()]
    return game_id, rows, links


def _plan(rows: list[dict], links: list[dict]) -> dict:
    en_rows = [r for r in rows if str(r.get("language") or "").lower() == "en"]
    es_rows = [r for r in rows if str(r.get("language") or "").lower() == "es"]
    en_by_identity: dict[tuple, list[dict]] = defaultdict(list)
    for row in en_rows:
        en_by_identity[_identity(row)].append(row)
    links_by_print: dict[int, list[dict]] = defaultdict(list)
    for link in links:
        links_by_print[int(link["print_id"])].append(link)

    buckets = Counter()
    proposals = []
    for es in es_rows:
        existing = links_by_print.get(int(es["print_id"]), [])
        normalized = _collector_family(es.get("collector_number"))
        if "-XX" not in normalized:
            buckets["collector_not_supported_by_v1"] += 1
            continue
        candidates = en_by_identity.get(_identity(es), [])
        if not candidates:
            buckets["no_en_identity_sibling"] += 1
            continue
        physical = [en for en in candidates if _physical_match(es, en)]
        if not physical:
            buckets["physical_mismatch"] += 1
            continue
        if len(physical) != 1:
            buckets["ambiguous_en_physical_sibling"] += 1
            continue
        en = physical[0]
        unique_market = {
            int(link["market_row_id"]): link
            for link in links_by_print.get(int(en["print_id"]), [])
        }
        if not unique_market:
            buckets["en_sibling_without_cardmarket"] += 1
            continue
        if len(unique_market) != 1:
            buckets["en_sibling_cardmarket_ambiguous"] += 1
            continue
        market_row_id, market_link = next(iter(unique_market.items()))
        existing_market_ids = {int(link["market_row_id"]) for link in existing}
        if existing_market_ids:
            if existing_market_ids == {market_row_id}:
                buckets["already_linked_same_product"] += 1
                continue
            buckets["existing_es_product_conflict"] += 1
            continue
        proposals.append(
            {
                "external_product_id": market_row_id,
                "es_print_id": int(es["print_id"]),
                "en_print_id": int(en["print_id"]),
                "card_id": int(es["card_id"]),
                "set_code": es.get("set_code"),
                "es_collector": es.get("collector_number"),
                "en_collector": en.get("collector_number"),
                "rarity": es.get("rarity"),
                "variant": es.get("variant"),
                "id_product": str(market_link.get("id_product") or ""),
            }
        )
    buckets["deterministic_proposal"] = len(proposals)
    return {"proposals": proposals, "buckets": dict(sorted(buckets.items()))}


def _multilingual_product_count(cur, game_id: int) -> int:
    cur.execute(
        """
        WITH langs AS (
          SELECT l.external_product_id,COUNT(DISTINCT lower(p.language)) AS language_count
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN prints p ON p.id=l.print_id
          WHERE e.source='cardmarket' AND e.game_id=%s AND e.product_group='single'
            AND l.link_status = ANY(%s)
            AND lower(coalesce(p.language,'')) IN ('en','es')
          GROUP BY l.external_product_id
        )
        SELECT COUNT(*) FROM langs WHERE language_count>1
        """,
        (game_id, list(ACCEPTED)),
    )
    return int(cur.fetchone()[0] or 0)


def run(*, apply: bool, expected: int, output: str) -> dict:
    conn = psycopg2.connect(
        _url(),
        connect_timeout=30,
        application_name="dontripit_ygo_es_cardmarket_bridge_v1",
    )
    conn.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    committed = False
    try:
        with conn.cursor() as cur:
            game_id, rows, links = _load_state(cur)
            before = _plan(rows, links)
            before_multi = _multilingual_product_count(cur, game_id)
            if before["buckets"].get("existing_es_product_conflict", 0):
                raise AssertionError(f"Existing ES product conflicts: {before['buckets']}")
            if len(before["proposals"]) != expected:
                raise AssertionError(
                    f"Expected {expected} deterministic proposals, got {len(before['proposals'])}: {before['buckets']}"
                )

            writes = 0
            if apply:
                evidence_rows = []
                for proposal in before["proposals"]:
                    evidence_rows.append(
                        (
                            int(proposal["external_product_id"]),
                            int(proposal["es_print_id"]),
                            MAPPING_METHOD,
                            "exact",
                            "accepted",
                            False,
                            Json(
                                {
                                    "bridge_version": 1,
                                    "source_en_print_id": int(proposal["en_print_id"]),
                                    "card_id": int(proposal["card_id"]),
                                    "set_code": proposal.get("set_code"),
                                    "es_collector": proposal.get("es_collector"),
                                    "en_collector": proposal.get("en_collector"),
                                    "rarity": proposal.get("rarity"),
                                    "variant": proposal.get("variant"),
                                    "id_product": proposal.get("id_product"),
                                    "rule": "same-card-set-finish + EN/SP collector normalization + normalized rarity/variant + unique accepted EN Cardmarket product",
                                }
                            ),
                        )
                    )
                execute_values(
                    cur,
                    """
                    INSERT INTO external_catalog_print_links
                      (external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
                    VALUES %s
                    ON CONFLICT (external_product_id,print_id) DO UPDATE SET
                      mapping_method=EXCLUDED.mapping_method,
                      confidence=EXCLUDED.confidence,
                      link_status=EXCLUDED.link_status,
                      reviewed=EXCLUDED.reviewed,
                      evidence=EXCLUDED.evidence,
                      updated_at=now()
                    """,
                    evidence_rows,
                    page_size=1000,
                )
                writes = len(evidence_rows)

                _game_id2, rows2, links2 = _load_state(cur)
                after = _plan(rows2, links2)
                after_multi = _multilingual_product_count(cur, game_id)
                if after["proposals"]:
                    raise AssertionError(f"Second pass still has proposals: {len(after['proposals'])}")
                if after["buckets"].get("existing_es_product_conflict", 0):
                    raise AssertionError(f"Second pass created ES product conflicts: {after['buckets']}")
                if after["buckets"].get("already_linked_same_product", 0) < expected:
                    raise AssertionError(f"Second pass did not certify all links: {after['buckets']}")
                if after_multi - before_multi != expected:
                    raise AssertionError(
                        f"Expected {expected} new EN+ES products, got before={before_multi} after={after_multi}"
                    )
                conn.commit()
                committed = True
            else:
                after = None
                after_multi = before_multi + expected
                conn.rollback()

        report = {
            "status": "pass",
            "apply": bool(apply),
            "commit_confirmed": committed,
            "mapping_method": MAPPING_METHOD,
            "expected_initial_proposals": expected,
            "production_writes": writes if committed else 0,
            "before": {
                "proposal_count": len(before["proposals"]),
                "buckets": before["buckets"],
                "multilingual_products": before_multi,
            },
            "after": {
                "proposal_count": len(after["proposals"]) if after is not None else 0,
                "buckets": after["buckets"] if after is not None else None,
                "multilingual_products": after_multi,
            },
            "economics_untouched": True,
            "catalog_identity_untouched": True,
            "images_untouched": True,
            "ja_untouched": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-proposals", type=int, default=EXPECTED_INITIAL_PROPOSALS)
    parser.add_argument("--output", default="/tmp/yugioh-cardmarket-es-bridge-apply-v1.json")
    args = parser.parse_args()
    run(apply=args.apply, expected=args.expected_proposals, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
