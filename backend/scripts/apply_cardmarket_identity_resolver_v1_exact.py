#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values


ACCEPTED_STATUSES = ("accepted", "mapped", "exact")
MAPPING_METHOD = "cardmarket_identity_resolver_v1_exact"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_certified_rows(proposed_path: Path, summary_path: Path) -> tuple[list[dict], dict]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    blockers = []
    if summary.get("status") != "pass":
        blockers.append(f"summary_status={summary.get('status')!r}")
    if summary.get("mode") != "READ_ONLY":
        blockers.append(f"summary_mode={summary.get('mode')!r}")
    if summary.get("production_writes") != 0:
        blockers.append(f"certifier_production_writes={summary.get('production_writes')!r}")
    if summary.get("transaction_read_only") is not True:
        blockers.append("certifier_transaction_read_only_not_true")
    forbidden = summary.get("forbidden_mismatches") or {}
    for key in ("duplicate_catalog_idProduct", "wrong_exact_gold_predictions", "cross_game_resolver_writes"):
        if int(forbidden.get(key) or 0) != 0:
            blockers.append(f"forbidden_mismatch_{key}={forbidden.get(key)!r}")
    if blockers:
        raise AssertionError({"reason": "resolver certification is not apply-safe", "blockers": blockers})

    rows: list[dict] = []
    seen_prints: dict[int, str] = {}
    with proposed_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {
            "print_id", "identity_key", "game", "card_name", "set_code", "collector_number",
            "language", "finish", "variant", "category", "method", "idProduct", "cardmarket_url",
            "future_auto_write_eligible", "review_required",
        }
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise AssertionError(f"proposed file missing columns: {missing}")
        for raw in reader:
            if raw.get("category") != "EXACT":
                continue
            if str(raw.get("future_auto_write_eligible") or "").lower() != "true":
                raise AssertionError(f"EXACT row not future-auto-write eligible: {raw.get('print_id')}")
            if str(raw.get("review_required") or "").lower() != "false":
                raise AssertionError(f"EXACT row unexpectedly requires review: {raw.get('print_id')}")
            print_id = int(raw["print_id"])
            id_product = str(raw["idProduct"] or "").strip()
            game = str(raw["game"] or "").strip().lower()
            if not id_product or not id_product.isdigit():
                raise AssertionError(f"invalid idProduct for print {print_id}: {id_product!r}")
            prior = seen_prints.get(print_id)
            if prior is not None and prior != id_product:
                raise AssertionError(f"multiple certified idProducts for print {print_id}: {prior}, {id_product}")
            seen_prints[print_id] = id_product
            rows.append({
                "print_id": print_id,
                "identity_key": str(raw["identity_key"]),
                "game": game,
                "card_name": str(raw["card_name"]),
                "set_code": str(raw["set_code"]),
                "collector_number": str(raw["collector_number"] or ""),
                "language": str(raw["language"] or ""),
                "finish": str(raw["finish"] or ""),
                "variant": str(raw["variant"] or "default"),
                "resolver_method": str(raw["method"]),
                "id_product": id_product,
                "cardmarket_url": str(raw["cardmarket_url"]),
            })

    expected = int(summary.get("exact_resolvable_pending_print_rows") or 0)
    if len(rows) != expected:
        raise AssertionError(f"EXACT row count mismatch proposed={len(rows)} certified_summary={expected}")
    if len(rows) > 250_000:
        raise AssertionError(f"refusing unexpectedly large exact apply: {len(rows)}")
    return rows, summary


def stage_rows(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE cm_identity_resolver_v1_stage (
                print_id BIGINT NOT NULL,
                identity_key TEXT NOT NULL,
                game TEXT NOT NULL,
                card_name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                collector_number TEXT NOT NULL,
                language TEXT NOT NULL,
                finish TEXT NOT NULL,
                variant TEXT NOT NULL,
                resolver_method TEXT NOT NULL,
                id_product TEXT NOT NULL,
                cardmarket_url TEXT NOT NULL,
                PRIMARY KEY (print_id, id_product)
            ) ON COMMIT DROP
        """)
        values = [(
            row["print_id"], row["identity_key"], row["game"], row["card_name"], row["set_code"],
            row["collector_number"], row["language"], row["finish"], row["variant"],
            row["resolver_method"], row["id_product"], row["cardmarket_url"],
        ) for row in rows]
        execute_values(cur, """
            INSERT INTO cm_identity_resolver_v1_stage
              (print_id,identity_key,game,card_name,set_code,collector_number,language,finish,variant,
               resolver_method,id_product,cardmarket_url)
            VALUES %s
        """, values, page_size=5000)
        cur.execute("ANALYZE cm_identity_resolver_v1_stage")


def preflight(conn, *, expected_rows: int) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT count(*) AS n, count(DISTINCT print_id) AS prints FROM cm_identity_resolver_v1_stage")
        stage = cur.fetchone()
        if int(stage["n"]) != expected_rows or int(stage["prints"]) != expected_rows:
            raise AssertionError({"reason": "stage cardinality mismatch", "stage": dict(stage), "expected": expected_rows})

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            LEFT JOIN prints p ON p.id=s.print_id
            LEFT JOIN cards c ON c.id=p.card_id
            LEFT JOIN games g ON g.id=c.game_id
            WHERE p.id IS NULL OR g.slug IS DISTINCT FROM s.game
        """)
        missing_or_cross_game_prints = int(cur.fetchone()["n"])

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            LEFT JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=g.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            WHERE e.id IS NULL
        """)
        missing_external_products = int(cur.fetchone()["n"])

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN external_catalog_print_links l ON l.print_id=s.print_id
            JOIN external_catalog_products e ON e.id=l.external_product_id
            JOIN games g ON g.id=e.game_id
            WHERE e.source='cardmarket'
              AND e.product_group='single'
              AND l.confidence='exact'
              AND l.link_status = ANY(%s)
              AND (e.external_id<>s.id_product OR g.slug<>s.game)
        """, (list(ACCEPTED_STATUSES),))
        conflicting_existing_target_links = int(cur.fetchone()["n"])

        # A Cardmarket idProduct may legitimately span language and finish, but it
        # must not already claim a different canonical card/set/collector/variant.
        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints tp ON tp.id=s.print_id
            JOIN cards tc ON tc.id=tp.card_id
            JOIN games tg ON tg.id=tc.game_id
            JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=tg.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            JOIN external_catalog_print_links l
              ON l.external_product_id=e.id
             AND l.confidence='exact'
             AND l.link_status = ANY(%s)
            JOIN prints ep ON ep.id=l.print_id
            JOIN cards ec ON ec.id=ep.card_id
            WHERE ec.game_id<>tc.game_id
               OR ep.card_id<>tp.card_id
               OR ep.set_id<>tp.set_id
               OR regexp_replace(lower(coalesce(ep.collector_number,'')), '[^a-z0-9]', '', 'g')
                  <> regexp_replace(lower(coalesce(tp.collector_number,'')), '[^a-z0-9]', '', 'g')
               OR lower(coalesce(ep.variant,'default'))<>lower(coalesce(tp.variant,'default'))
        """, (list(ACCEPTED_STATUSES),))
        incompatible_existing_product_links = int(cur.fetchone()["n"])

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=g.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            JOIN external_catalog_print_links l
              ON l.external_product_id=e.id
             AND l.print_id=s.print_id
             AND l.confidence='exact'
             AND l.link_status = ANY(%s)
        """, (list(ACCEPTED_STATUSES),))
        already_exact_same_pair = int(cur.fetchone()["n"])

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id
            JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=g.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            LEFT JOIN external_catalog_print_links l
              ON l.external_product_id=e.id AND l.print_id=s.print_id
            WHERE l.id IS NULL
               OR NOT (l.confidence='exact' AND l.link_status = ANY(%s))
        """, (list(ACCEPTED_STATUSES),))
        candidate_links = int(cur.fetchone()["n"])

        blockers = {
            "missing_or_cross_game_prints": missing_or_cross_game_prints,
            "missing_external_products": missing_external_products,
            "conflicting_existing_target_links": conflicting_existing_target_links,
            "incompatible_existing_product_links": incompatible_existing_product_links,
        }
        write_ready = all(value == 0 for value in blockers.values())
        return {
            "stage_rows": expected_rows,
            "already_exact_same_pair": already_exact_same_pair,
            "candidate_links": candidate_links,
            **blockers,
            "write_ready": write_ready,
        }


def apply(conn, *, source_sha: str | None) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO external_catalog_print_links
              (external_product_id,print_id,mapping_method,confidence,link_status,reviewed,evidence)
            SELECT e.id,
                   s.print_id,
                   %s,
                   'exact',
                   'accepted',
                   false,
                   jsonb_build_object(
                     'resolver','cardmarket_identity_resolver_v1',
                     'resolver_method',s.resolver_method,
                     'identity_key',s.identity_key,
                     'cardmarket_idProduct',s.id_product,
                     'cardmarket_url',s.cardmarket_url,
                     'set_code',s.set_code,
                     'collector_number',s.collector_number,
                     'language',s.language,
                     'finish',s.finish,
                     'variant',s.variant,
                     'certifier_sha',%s
                   )
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=g.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            LEFT JOIN external_catalog_print_links existing
              ON existing.external_product_id=e.id AND existing.print_id=s.print_id
            WHERE existing.id IS NULL
               OR NOT (existing.confidence='exact' AND existing.link_status = ANY(%s))
            ON CONFLICT (external_product_id,print_id) DO UPDATE SET
              mapping_method=EXCLUDED.mapping_method,
              confidence='exact',
              link_status='accepted',
              reviewed=false,
              evidence=EXCLUDED.evidence,
              updated_at=now()
        """, (MAPPING_METHOD, source_sha, list(ACCEPTED_STATUSES)))
        affected = int(cur.rowcount or 0)

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket'
             AND e.game_id=g.id
             AND e.product_group='single'
             AND e.external_id=s.id_product
            JOIN external_catalog_print_links l
              ON l.external_product_id=e.id
             AND l.print_id=s.print_id
             AND l.confidence='exact'
             AND l.link_status = ANY(%s)
        """, (list(ACCEPTED_STATUSES),))
        certified_pairs_after = int(cur.fetchone()[0])

        cur.execute("""
            SELECT count(*)
            FROM (
              SELECT s.print_id
              FROM cm_identity_resolver_v1_stage s
              JOIN external_catalog_print_links l ON l.print_id=s.print_id
              JOIN external_catalog_products e ON e.id=l.external_product_id
              WHERE e.source='cardmarket' AND e.product_group='single'
                AND l.confidence='exact' AND l.link_status = ANY(%s)
              GROUP BY s.print_id
              HAVING count(DISTINCT e.id)<>1
            ) q
        """, (list(ACCEPTED_STATUSES),))
        ambiguous_target_prints_after = int(cur.fetchone()[0])

    return {
        "affected_rows": affected,
        "certified_pairs_after": certified_pairs_after,
        "ambiguous_target_prints_after": ambiguous_target_prints_after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply ONLY certified EXACT Cardmarket Identity Resolver V1 links.")
    parser.add_argument("--proposed", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rows, summary = load_certified_rows(args.proposed, args.summary)
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        stage_rows(conn, rows)
        pre = preflight(conn, expected_rows=len(rows))
        report = {
            "mode": "apply" if args.apply else "dry_run",
            "mapping_method": MAPPING_METHOD,
            "certifier_sha": summary.get("resolver_git_sha"),
            "proposed_sha256": sha256(args.proposed),
            "summary_sha256": sha256(args.summary),
            "certified_exact_rows": len(rows),
            "preflight": pre,
            "production_writes": 0,
        }
        if not pre["write_ready"]:
            conn.rollback()
            args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2, sort_keys=True))
            return 2

        if args.apply:
            applied = apply(conn, source_sha=summary.get("resolver_git_sha"))
            if applied["certified_pairs_after"] != len(rows):
                raise AssertionError({
                    "reason": "postcondition certified pair count mismatch",
                    "expected": len(rows),
                    "actual": applied["certified_pairs_after"],
                })
            if applied["ambiguous_target_prints_after"] != 0:
                raise AssertionError({
                    "reason": "postcondition ambiguous target prints",
                    "count": applied["ambiguous_target_prints_after"],
                })
            report["apply"] = applied
            report["production_writes"] = applied["affected_rows"]
            conn.commit()
        else:
            conn.rollback()

        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
