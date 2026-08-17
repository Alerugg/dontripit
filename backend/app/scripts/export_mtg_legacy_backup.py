from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg2


TABLES = (
    "sets",
    "cards",
    "prints",
    "card_attributes",
    "print_attributes",
    "print_images",
    "print_identifiers",
    "print_field_provenance",
    "field_provenance_card",
    "field_provenance_print",
    "card_search_profiles",
    "print_search_profiles",
    "search_documents",
    "print_releases",
)


def _db_url() -> str:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL configured")
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url[len("postgresql+psycopg2://"):]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cur.fetchone()[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _query_for(name: str) -> str | None:
    if name == "sets":
        return "SELECT to_jsonb(t)::text FROM (SELECT * FROM sets WHERE game_id=%s ORDER BY id) t"
    if name == "cards":
        return "SELECT to_jsonb(t)::text FROM (SELECT * FROM cards WHERE game_id=%s ORDER BY id) t"
    if name == "prints":
        return """SELECT to_jsonb(t)::text FROM (
            SELECT p.* FROM prints p JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s ORDER BY p.id
        ) t"""
    if name in {"card_attributes", "card_search_profiles"}:
        table = name
        return f"""SELECT to_jsonb(t)::text FROM (
            SELECT x.* FROM {table} x JOIN cards c ON c.id=x.card_id
            WHERE c.game_id=%s ORDER BY x.card_id
        ) t"""
    if name in {"print_attributes", "print_images", "print_identifiers", "print_field_provenance", "print_releases"}:
        table = name
        return f"""SELECT to_jsonb(t)::text FROM (
            SELECT x.* FROM {table} x
            JOIN prints p ON p.id=x.print_id
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s ORDER BY x.print_id
        ) t"""
    if name == "print_search_profiles":
        return """SELECT to_jsonb(t)::text FROM (
            SELECT x.* FROM print_search_profiles x
            JOIN prints p ON p.id=x.print_id
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s ORDER BY x.print_id
        ) t"""
    if name == "search_documents":
        return "SELECT to_jsonb(t)::text FROM (SELECT * FROM search_documents WHERE game_id=%s ORDER BY id) t"
    if name == "field_provenance_card":
        return """SELECT to_jsonb(t)::text FROM (
            SELECT fp.* FROM field_provenance fp
            WHERE lower(fp.entity_type)='card'
              AND fp.entity_id IN (SELECT id FROM cards WHERE game_id=%s)
            ORDER BY fp.id
        ) t"""
    if name == "field_provenance_print":
        return """SELECT to_jsonb(t)::text FROM (
            SELECT fp.* FROM field_provenance fp
            WHERE lower(fp.entity_type)='print'
              AND fp.entity_id IN (
                SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
              )
            ORDER BY fp.id
        ) t"""
    return None


def run(*, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(_db_url())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            row = cur.fetchone()
            if not row:
                raise AssertionError("MTG game row missing")
            game_id = int(row[0])

            cur.execute("SELECT version_num FROM alembic_version")
            alembic_version = str(cur.fetchone()[0])
            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0])

            files = {}
            total_rows = 0
            for name in TABLES:
                real_table = "field_provenance" if name.startswith("field_provenance_") else name
                path = output_dir / f"{name}.jsonl"
                if not _table_exists(cur, real_table):
                    path.write_text("", encoding="utf-8")
                    files[path.name] = {"rows": 0, "bytes": 0, "sha256": _sha256(path), "table_missing": True}
                    continue

                query = _query_for(name)
                if query is None:
                    raise AssertionError(f"No backup query defined for {name}")
                cur.execute(query, (game_id,))
                rows = 0
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    while True:
                        batch = cur.fetchmany(1000)
                        if not batch:
                            break
                        for (payload,) in batch:
                            handle.write(str(payload) + "\n")
                            rows += 1
                total_rows += rows
                files[path.name] = {
                    "rows": rows,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "table_missing": False,
                }

            manifest = {
                "status": "pass",
                "mode": "read_only",
                "database_writes": 0,
                "game_id": game_id,
                "alembic_version": alembic_version,
                "database_bytes": database_bytes,
                "total_rows": total_rows,
                "files": files,
            }
            (output_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a compact read-only recovery backup of the current MTG catalog")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
