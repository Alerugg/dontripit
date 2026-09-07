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
    blockers: list[str] = []
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
        execute_values(
            cur,
            """
            INSERT INTO cm_identity_resolver_v1_stage
              (print_id,identity_key,game,card_name,set_code,collector_number,language,finish,variant,
               resolver_method,id_product,cardmarket_url)
            VALUES %s
            """,
            [(
                row["print_id"], row["identity_key"], row["game"], row["card_name"], row["set_code"],
                row["collector_number"], row["language"], row["finish"], row["variant"],
                row["resolver_method"], row["id_product"], row["cardmarket_url"],
            ) for row in rows],
            page_size=5000,
        )
        cur.execute("ANALYZE cm_identity_resolver_v1_stage")


def build_quarantine(conn) -> None:
    """Quarantine contradictions; never weaken the resolver to make an apply pass.

    Cardmarket idProduct is language-agnostic and, for MTG, finish-agnostic. MTG
    foil/nonfoil/etched therefore may legitimately share one product. For the
    other games our canonical variant carries physical printing information and
    remains part of the compatibility signature.
    """
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE cm_identity_resolver_v1_quarantine_prints AS
            SELECT DISTINCT s.print_id, 'existing_exact_target_conflict'::text AS reason
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
        cur.execute("CREATE INDEX ON cm_identity_resolver_v1_quarantine_prints (print_id)")

        cur.execute("""
            CREATE TEMP TABLE cm_identity_resolver_v1_quarantine_products AS
            WITH staged_products AS (
              SELECT DISTINCT e.id AS external_product_id
              FROM cm_identity_resolver_v1_stage s
              JOIN prints p ON p.id=s.print_id
              JOIN cards c ON c.id=p.card_id
              JOIN games g ON g.id=c.game_id AND g.slug=s.game
              JOIN external_catalog_products e
                ON e.source='cardmarket'
               AND e.game_id=g.id
               AND e.product_group='single'
               AND e.external_id=s.id_product
            ),
            claims AS (
              SELECT e.id AS external_product_id,
                     g.slug AS game,
                     p.card_id,
                     p.set_id,
                     regexp_replace(lower(coalesce(p.collector_number,'')), '[^a-z0-9]', '', 'g') AS collector_key,
                     CASE WHEN g.slug='mtg' THEN '*' ELSE lower(coalesce(p.variant,'default')) END AS physical_variant
              FROM cm_identity_resolver_v1_stage s
              JOIN prints p ON p.id=s.print_id
              JOIN cards c ON c.id=p.card_id
              JOIN games g ON g.id=c.game_id AND g.slug=s.game
              JOIN external_catalog_products e
                ON e.source='cardmarket'
               AND e.game_id=g.id
               AND e.product_group='single'
               AND e.external_id=s.id_product

              UNION ALL

              SELECT e.id AS external_product_id,
                     g.slug AS game,
                     p.card_id,
                     p.set_id,
                     regexp_replace(lower(coalesce(p.collector_number,'')), '[^a-z0-9]', '', 'g') AS collector_key,
                     CASE WHEN g.slug='mtg' THEN '*' ELSE lower(coalesce(p.variant,'default')) END AS physical_variant
              FROM staged_products sp
              JOIN external_catalog_products e ON e.id=sp.external_product_id
              JOIN games g ON g.id=e.game_id
              JOIN external_catalog_print_links l ON l.external_product_id=e.id
              JOIN prints p ON p.id=l.print_id
              WHERE l.confidence='exact'
                AND l.link_status = ANY(%s)
            ),
            signature_counts AS (
              SELECT external_product_id,
                     count(DISTINCT concat_ws('|', game, card_id::text, set_id::text, collector_key, physical_variant)) AS signature_count
              FROM claims
              GROUP BY external_product_id
            )
            SELECT e.id AS external_product_id,
                   e.external_id AS id_product,
                   g.slug AS game,
                   sc.signature_count,
                   'idProduct_multiple_physical_signatures'::text AS reason
            FROM signature_counts sc
            JOIN external_catalog_products e ON e.id=sc.external_product_id
            JOIN games g ON g.id=e.game_id
            WHERE sc.signature_count>1
        """, (list(ACCEPTED_STATUSES),))
        cur.execute("CREATE UNIQUE INDEX ON cm_identity_resolver_v1_quarantine_products (external_product_id)")


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

        build_quarantine(conn)

        cur.execute("SELECT count(DISTINCT print_id) AS n FROM cm_identity_resolver_v1_quarantine_prints")
        quarantined_target_conflict_prints = int(cur.fetchone()["n"])
        cur.execute("SELECT count(*) AS n FROM cm_identity_resolver_v1_quarantine_products")
        quarantined_incompatible_products = int(cur.fetchone()["n"])

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            WHERE qp.print_id IS NOT NULL OR qprod.external_product_id IS NOT NULL
        """)
        quarantined_rows = int(cur.fetchone()["n"])
        safe_rows = expected_rows - quarantined_rows

        cur.execute("""
            SELECT count(*) AS n
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            JOIN external_catalog_print_links l
              ON l.external_product_id=e.id AND l.print_id=s.print_id
             AND l.confidence='exact' AND l.link_status = ANY(%s)
            WHERE qp.print_id IS NULL AND qprod.external_product_id IS NULL
        """, (list(ACCEPTED_STATUSES),))
        already_exact_same_pair = int(cur.fetchone()["n"])
        candidate_links = safe_rows - already_exact_same_pair

        cur.execute("""
            SELECT s.game, count(*) AS rows
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            WHERE qp.print_id IS NOT NULL OR qprod.external_product_id IS NOT NULL
            GROUP BY s.game
            ORDER BY s.game
        """)
        quarantined_rows_by_game = {str(row["game"]): int(row["rows"]) for row in cur.fetchall()}

        cur.execute("""
            SELECT s.print_id, s.game, s.card_name, s.set_code, s.collector_number, s.variant,
                   s.id_product,
                   CASE WHEN qp.print_id IS NOT NULL THEN qp.reason ELSE qprod.reason END AS reason,
                   qprod.signature_count
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            WHERE qp.print_id IS NOT NULL OR qprod.external_product_id IS NOT NULL
            ORDER BY s.game, s.id_product, s.print_id
            LIMIT 100
        """)
        quarantine_samples = [dict(row) for row in cur.fetchall()]

        hard_blockers = {
            "missing_or_cross_game_prints": missing_or_cross_game_prints,
            "missing_external_products": missing_external_products,
        }
        write_ready = all(value == 0 for value in hard_blockers.values()) and safe_rows > 0
        return {
            "stage_rows": expected_rows,
            "safe_rows": safe_rows,
            "already_exact_same_pair": already_exact_same_pair,
            "candidate_links": candidate_links,
            "quarantined_rows": quarantined_rows,
            "quarantined_rows_by_game": quarantined_rows_by_game,
            "quarantined_target_conflict_prints": quarantined_target_conflict_prints,
            "quarantined_incompatible_products": quarantined_incompatible_products,
            "quarantine_samples": quarantine_samples,
            **hard_blockers,
            "write_ready": write_ready,
        }


def apply(conn, *, source_sha: str | None, expected_safe_rows: int) -> dict:
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
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            LEFT JOIN external_catalog_print_links existing
              ON existing.external_product_id=e.id AND existing.print_id=s.print_id
            WHERE qp.print_id IS NULL
              AND qprod.external_product_id IS NULL
              AND (existing.id IS NULL OR NOT (existing.confidence='exact' AND existing.link_status = ANY(%s)))
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
            SELECT count(*)
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            JOIN external_catalog_print_links l
              ON l.external_product_id=e.id AND l.print_id=s.print_id
             AND l.confidence='exact' AND l.link_status = ANY(%s)
            WHERE qp.print_id IS NULL AND qprod.external_product_id IS NULL
        """, (list(ACCEPTED_STATUSES),))
        safe_pairs_after = int(cur.fetchone()[0])

        cur.execute("""
            SELECT count(*)
            FROM (
              SELECT s.print_id
              FROM cm_identity_resolver_v1_stage s
              JOIN prints p ON p.id=s.print_id
              JOIN cards c ON c.id=p.card_id
              JOIN games g ON g.id=c.game_id AND g.slug=s.game
              JOIN external_catalog_products target
                ON target.source='cardmarket' AND target.game_id=g.id AND target.product_group='single' AND target.external_id=s.id_product
              LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
              LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=target.id
              JOIN external_catalog_print_links l ON l.print_id=s.print_id
              JOIN external_catalog_products e ON e.id=l.external_product_id
              WHERE qp.print_id IS NULL AND qprod.external_product_id IS NULL
                AND e.source='cardmarket' AND e.product_group='single'
                AND l.confidence='exact' AND l.link_status = ANY(%s)
              GROUP BY s.print_id
              HAVING count(DISTINCT e.id)<>1
            ) q
        """, (list(ACCEPTED_STATUSES),))
        ambiguous_safe_prints_after = int(cur.fetchone()[0])

        cur.execute("""
            SELECT count(*)
            FROM cm_identity_resolver_v1_stage s
            JOIN prints p ON p.id=s.print_id
            JOIN cards c ON c.id=p.card_id
            JOIN games g ON g.id=c.game_id AND g.slug=s.game
            JOIN external_catalog_products e
              ON e.source='cardmarket' AND e.game_id=g.id AND e.product_group='single' AND e.external_id=s.id_product
            LEFT JOIN cm_identity_resolver_v1_quarantine_prints qp ON qp.print_id=s.print_id
            LEFT JOIN cm_identity_resolver_v1_quarantine_products qprod ON qprod.external_product_id=e.id
            JOIN external_catalog_print_links l ON l.external_product_id=e.id AND l.print_id=s.print_id
            WHERE (qp.print_id IS NOT NULL OR qprod.external_product_id IS NOT NULL)
              AND l.mapping_method=%s
        """, (MAPPING_METHOD,))
        applied_to_quarantined = int(cur.fetchone()[0])

    if safe_pairs_after != expected_safe_rows:
        raise AssertionError({"reason": "safe exact pair postcondition mismatch", "expected": expected_safe_rows, "actual": safe_pairs_after})
    if ambiguous_safe_prints_after:
        raise AssertionError({"reason": "ambiguous safe prints after apply", "count": ambiguous_safe_prints_after})
    if applied_to_quarantined:
        raise AssertionError({"reason": "quarantined rows received resolver mapping", "count": applied_to_quarantined})

    return {
        "affected_rows": affected,
        "safe_pairs_after": safe_pairs_after,
        "ambiguous_safe_prints_after": ambiguous_safe_prints_after,
        "applied_to_quarantined": applied_to_quarantined,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply certified EXACT Cardmarket identities, quarantining contradictory physical claims.")
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
            applied = apply(conn, source_sha=summary.get("resolver_git_sha"), expected_safe_rows=int(pre["safe_rows"]))
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
