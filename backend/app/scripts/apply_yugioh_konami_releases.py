from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values


SOURCE = "konami_neuron"
EXPECTED_GAME = "yugioh"
EXPECTED_MEMBERSHIPS = 39362
EXPECTED_UNIQUE_PRINTS = 39250
EXPECTED_MATCHED_PRODUCTS = 836


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("audit payload must be an object")
    return payload


def _audit_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_plan(conn, payload: dict, *, audit_sha256: str) -> dict:
    memberships = payload.get("exact_memberships") or []
    products = payload.get("official_products") or []
    if len(memberships) != EXPECTED_MEMBERSHIPS:
        raise AssertionError({"expected_memberships": EXPECTED_MEMBERSHIPS, "actual": len(memberships)})

    pairs = {(int(row["print_id"]), str(row["pid"])) for row in memberships}
    if len(pairs) != EXPECTED_MEMBERSHIPS:
        raise AssertionError("exact membership list contains duplicate print/pid pairs")

    unique_prints = {print_id for print_id, _ in pairs}
    unique_pids = {pid for _, pid in pairs}
    if len(unique_prints) != EXPECTED_UNIQUE_PRINTS:
        raise AssertionError({"expected_unique_prints": EXPECTED_UNIQUE_PRINTS, "actual": len(unique_prints)})
    if len(unique_pids) != EXPECTED_MATCHED_PRODUCTS:
        raise AssertionError({"expected_matched_products": EXPECTED_MATCHED_PRODUCTS, "actual": len(unique_pids)})

    product_by_pid = {str(row["pid"]): row for row in products}
    missing_product_metadata = sorted(pid for pid in unique_pids if pid not in product_by_pid)
    if missing_product_metadata:
        raise AssertionError({"missing_product_metadata": missing_product_metadata[:20]})

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM games WHERE slug=%s", (EXPECTED_GAME,))
        game_row = cur.fetchone()
        if not game_row:
            raise AssertionError("Yu-Gi-Oh game row is missing")
        game_id = int(game_row[0])

        print_ids = sorted(unique_prints)
        cur.execute(
            """
            SELECT p.id,p.collector_number,p.rarity,c.game_id
            FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE p.id=ANY(%s)
            """,
            (print_ids,),
        )
        current = {
            int(print_id): {
                "collector_number": str(collector_number or ""),
                "rarity": str(rarity or ""),
                "game_id": int(card_game_id),
            }
            for print_id, collector_number, rarity, card_game_id in cur.fetchall()
        }

        missing_prints = sorted(unique_prints - set(current))
        cross_game = sorted(print_id for print_id, row in current.items() if row["game_id"] != game_id)
        if missing_prints or cross_game:
            raise AssertionError({"missing_prints": missing_prints[:20], "cross_game": cross_game[:20]})

        identity_mismatch = []
        for row in memberships:
            print_id = int(row["print_id"])
            db_row = current[print_id]
            if db_row["collector_number"] != str(row.get("collector_number") or "") or db_row["rarity"] != str(row.get("rarity") or ""):
                identity_mismatch.append(
                    {
                        "print_id": print_id,
                        "db_collector": db_row["collector_number"],
                        "audit_collector": row.get("collector_number"),
                        "db_rarity": db_row["rarity"],
                        "audit_rarity": row.get("rarity"),
                    }
                )
        if identity_mismatch:
            raise AssertionError({"identity_mismatch_count": len(identity_mismatch), "sample": identity_mismatch[:20]})

        cur.execute(
            """
            SELECT external_id,name FROM catalog_releases
            WHERE game_id=%s AND source=%s AND external_id=ANY(%s)
            """,
            (game_id, SOURCE, sorted(unique_pids)),
        )
        existing_releases = {str(pid): str(name) for pid, name in cur.fetchall()}

        cur.execute(
            """
            SELECT pr.print_id,cr.external_id
            FROM print_releases pr
            JOIN catalog_releases cr ON cr.id=pr.release_id
            WHERE cr.game_id=%s AND cr.source=%s AND cr.external_id=ANY(%s)
            """,
            (game_id, SOURCE, sorted(unique_pids)),
        )
        existing_pairs = {(int(print_id), str(pid)) for print_id, pid in cur.fetchall()}

    release_rows = []
    for pid in sorted(unique_pids):
        product = product_by_pid[pid]
        release_rows.append(
            {
                "external_id": pid,
                "name": str(product.get("product_name") or "").strip(),
                "release_date": product.get("release_date"),
                "metadata_json": {
                    "source": SOURCE,
                    "pid": pid,
                    "official_rows_seen": int(product.get("official_rows_seen") or 0),
                    "matched_prints": int(product.get("matched_prints") or 0),
                    "audit_sha256": audit_sha256,
                    "identity_basis": ["konami_card_id", "collector_number", "rarity", "official_product_pid"],
                },
            }
        )
        if not release_rows[-1]["name"]:
            raise AssertionError({"empty_product_name": pid})

    membership_rows = []
    for row in memberships:
        membership_rows.append(
            {
                "print_id": int(row["print_id"]),
                "pid": str(row["pid"]),
                "source_print_id": str(row.get("konami_id") or "") or None,
                "metadata_json": {
                    "source": SOURCE,
                    "collector_number": row.get("collector_number"),
                    "rarity": row.get("rarity"),
                    "product_name": row.get("product_name"),
                    "audit_sha256": audit_sha256,
                    "identity_basis": ["collector_number", "rarity", "official_product_pid"],
                },
            }
        )

    return {
        "game_id": game_id,
        "release_rows": release_rows,
        "membership_rows": membership_rows,
        "existing_release_count": len(existing_releases),
        "existing_membership_count": len(existing_pairs & pairs),
        "new_release_count": len(unique_pids - set(existing_releases)),
        "new_membership_count": len(pairs - existing_pairs),
        "official_products_in_audit": len(products),
        "exact_products": len(unique_pids),
        "exact_memberships": len(pairs),
        "exact_unique_prints": len(unique_prints),
    }


def apply_plan(conn, plan: dict) -> dict:
    game_id = int(plan["game_id"])
    with conn.cursor() as cur:
        release_values = [
            (
                game_id,
                SOURCE,
                row["external_id"],
                row["name"],
                None,
                "official_product",
                row["release_date"],
                "en",
                "global",
                Json(row["metadata_json"]),
            )
            for row in plan["release_rows"]
        ]
        execute_values(
            cur,
            """
            INSERT INTO catalog_releases
              (game_id,source,external_id,name,code,release_type,release_date,language,region,metadata_json)
            VALUES %s
            ON CONFLICT (game_id,source,external_id) DO UPDATE SET
              name=EXCLUDED.name,
              release_type=EXCLUDED.release_type,
              release_date=EXCLUDED.release_date,
              language=EXCLUDED.language,
              region=EXCLUDED.region,
              metadata_json=EXCLUDED.metadata_json
            """,
            release_values,
            page_size=1000,
        )

        cur.execute(
            "SELECT external_id,id FROM catalog_releases WHERE game_id=%s AND source=%s AND external_id=ANY(%s)",
            (game_id, SOURCE, [row["external_id"] for row in plan["release_rows"]]),
        )
        release_ids = {str(pid): int(release_id) for pid, release_id in cur.fetchall()}
        if len(release_ids) != EXPECTED_MATCHED_PRODUCTS:
            raise AssertionError({"release_ids": len(release_ids), "expected": EXPECTED_MATCHED_PRODUCTS})

        membership_values = [
            (
                row["print_id"],
                release_ids[row["pid"]],
                row["source_print_id"],
                "official_product_print",
                Json(row["metadata_json"]),
            )
            for row in plan["membership_rows"]
        ]
        execute_values(
            cur,
            """
            INSERT INTO print_releases
              (print_id,release_id,source_print_id,appearance_type,metadata_json)
            VALUES %s
            ON CONFLICT (print_id,release_id) DO UPDATE SET
              source_print_id=EXCLUDED.source_print_id,
              appearance_type=EXCLUDED.appearance_type,
              metadata_json=EXCLUDED.metadata_json
            """,
            membership_values,
            page_size=2000,
        )

        cur.execute(
            """
            SELECT count(*),count(DISTINCT pr.print_id),count(DISTINCT cr.id)
            FROM print_releases pr
            JOIN catalog_releases cr ON cr.id=pr.release_id
            WHERE cr.game_id=%s AND cr.source=%s
            """,
            (game_id, SOURCE),
        )
        membership_count, print_count, release_count = map(int, cur.fetchone())
        if membership_count != EXPECTED_MEMBERSHIPS or print_count != EXPECTED_UNIQUE_PRINTS or release_count != EXPECTED_MATCHED_PRODUCTS:
            raise AssertionError(
                {
                    "membership_count": membership_count,
                    "print_count": print_count,
                    "release_count": release_count,
                    "expected": [EXPECTED_MEMBERSHIPS, EXPECTED_UNIQUE_PRINTS, EXPECTED_MATCHED_PRODUCTS],
                }
            )

    return {
        "mode": "apply",
        "source": SOURCE,
        "release_count": release_count,
        "membership_count": membership_count,
        "unique_print_count": print_count,
        "new_release_count": plan["new_release_count"],
        "new_membership_count": plan["new_membership_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely persist exact Konami official product memberships from a certified audit.")
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    actual_sha = _audit_digest(args.audit_json)
    if actual_sha != args.expected_sha256:
        raise SystemExit(f"audit json sha256 mismatch: expected {args.expected_sha256}, got {actual_sha}")

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    payload = _load(args.audit_json)
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        plan = build_plan(conn, payload, audit_sha256=actual_sha)
        report = {
            "mode": "dry_run",
            "source": SOURCE,
            "audit_sha256": actual_sha,
            **{k: v for k, v in plan.items() if k not in {"release_rows", "membership_rows", "game_id"}},
        }
        if args.apply:
            applied = apply_plan(conn, plan)
            conn.commit()
            report = {**report, **applied, "audit_sha256": actual_sha}
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
