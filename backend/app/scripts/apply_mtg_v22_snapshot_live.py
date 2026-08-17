from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Iterator

import psycopg2
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError


EXPECTED_SCHEMA = "mtg-canonical-v2.2"
EXPECTED_PRELIVE = {"sets": 298, "cards": 1183, "prints": 1588}
EXPECTED_COUNTS = {
    "sets": 986,
    "cards": 37624,
    "prints": 161275,
    "card_attributes": 37624,
    "print_attributes": 161275,
    "print_identifiers": 161275,
    "print_images": 168435,
}
EXPECTED_FINISHES = {"etched": 1218, "foil": 65936, "nonfoil": 94121}
REQUIRED_MTG_SCHEMA_REVISION = "20260808_25"


def _assert_supported_schema_revision(revision: str) -> None:
    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    try:
        ancestry = {
            item.revision
            for item in scripts.iterate_revisions(str(revision), "base")
        }
    except ResolutionError as exc:
        raise AssertionError(f"Unknown Alembic revision: {revision}") from exc

    if REQUIRED_MTG_SCHEMA_REVISION not in ancestry:
        raise AssertionError(
            f"Alembic revision {revision} does not include required MTG schema "
            f"{REQUIRED_MTG_SCHEMA_REVISION}"
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AssertionError(f"{path.name}:{line_number} is not an object")
            yield value


def _verify_snapshot(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise AssertionError(f"Snapshot manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Snapshot manifest is not PASS")
    if manifest.get("snapshot_schema_version") != EXPECTED_SCHEMA:
        raise AssertionError(f"Unexpected snapshot schema: {manifest.get('snapshot_schema_version')}")

    gates = manifest.get("gates") or {}
    for key in (
        "duplicate_paper_scryfall_ids",
        "exact_print_key_collisions",
        "natural_exact_print_collisions",
        "unknown_finishes",
        "missing_scryfall_ids",
    ):
        if gates.get(key) != 0:
            raise AssertionError(f"Snapshot gate {key} is not zero: {gates.get(key)!r}")
    if gates.get("pricing_payload_persisted") is not False:
        raise AssertionError("Snapshot unexpectedly persists pricing")
    if gates.get("raw_source_payload_persisted") is not False:
        raise AssertionError("Snapshot unexpectedly persists raw source payload")

    counts = manifest.get("counts") or {}
    if int(counts.get("logical_cards") or -1) != EXPECTED_COUNTS["cards"]:
        raise AssertionError("Snapshot Card count changed from certified expectation")
    if int(counts.get("exact_prints") or -1) != EXPECTED_COUNTS["prints"]:
        raise AssertionError("Snapshot Print count changed from certified expectation")
    if int(counts.get("sets") or -1) != EXPECTED_COUNTS["sets"]:
        raise AssertionError("Snapshot Set count changed from certified expectation")
    if {str(k): int(v) for k, v in (manifest.get("finish_counts") or {}).items()} != EXPECTED_FINISHES:
        raise AssertionError("Snapshot finish counts changed from certified expectation")

    for name, metadata in (manifest.get("files") or {}).items():
        path = root / name
        if not path.exists():
            raise AssertionError(f"Snapshot file missing: {name}")
        if _sha256(path) != metadata.get("sha256"):
            raise AssertionError(f"Snapshot SHA256 mismatch: {name}")
        with path.open("r", encoding="utf-8") as handle:
            rows = sum(1 for _ in handle)
        if rows != int(metadata.get("rows") or -1):
            raise AssertionError(f"Snapshot row count mismatch: {name}")
    return manifest


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cur.fetchone()[0])


def _scalar(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def _catalog_counts_by_game(cur) -> dict:
    cur.execute("""
        SELECT g.slug,
               (SELECT COUNT(*) FROM sets s WHERE s.game_id=g.id) AS sets,
               (SELECT COUNT(*) FROM cards c WHERE c.game_id=g.id) AS cards,
               (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=g.id) AS prints
        FROM games g ORDER BY g.slug
    """)
    return {
        str(slug): {"sets": int(sets), "cards": int(cards), "prints": int(prints)}
        for slug, sets, cards, prints in cur.fetchall()
    }


def _economics_counts(cur, game_id: int) -> dict[str, int]:
    result = {
        "prices": _scalar(cur, "SELECT COUNT(*) FROM prices WHERE game_id=%s", (game_id,)),
        "price_snapshots_card": 0,
        "price_snapshots_print": 0,
        "price_daily_ohlc_card": 0,
        "price_daily_ohlc_print": 0,
    }
    if _table_exists(cur, "price_snapshots"):
        result["price_snapshots_card"] = _scalar(cur, """
            SELECT COUNT(*) FROM price_snapshots
            WHERE entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)
        """, (game_id,))
        result["price_snapshots_print"] = _scalar(cur, """
            SELECT COUNT(*) FROM price_snapshots
            WHERE entity_type='print' AND entity_id IN (
              SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
            )
        """, (game_id,))
    if _table_exists(cur, "price_daily_ohlc"):
        result["price_daily_ohlc_card"] = _scalar(cur, """
            SELECT COUNT(*) FROM price_daily_ohlc
            WHERE entity_type='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)
        """, (game_id,))
        result["price_daily_ohlc_print"] = _scalar(cur, """
            SELECT COUNT(*) FROM price_daily_ohlc
            WHERE entity_type='print' AND entity_id IN (
              SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
            )
        """, (game_id,))
    return result


def _copy_rows(cur, *, table: str, columns: list[str], rows: Iterable[list[object]]) -> int:
    fd, temp_name = tempfile.mkstemp(prefix=f"mtg-{table}-", suffix=".tsv")
    os.close(fd)
    path = Path(temp_name)
    count = 0
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(
                handle,
                delimiter="\t",
                quotechar='"',
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
                doublequote=True,
            )
            for row in rows:
                writer.writerow(["\\N" if value is None else value for value in row])
                count += 1
        with path.open("r", encoding="utf-8", newline="") as handle:
            col_sql = ",".join(columns)
            cur.copy_expert(
                f"COPY {table} ({col_sql}) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', NULL '\\N')",
                handle,
            )
        return count
    finally:
        path.unlink(missing_ok=True)


def _delete_legacy_mtg(cur, game_id: int) -> dict[str, int]:
    deleted: dict[str, int] = {}

    if _table_exists(cur, "field_provenance"):
        cur.execute("""
            DELETE FROM field_provenance
            WHERE lower(entity_type)='print' AND entity_id IN (
              SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
            )
        """, (game_id,))
        deleted["field_provenance_print"] = cur.rowcount
        cur.execute("""
            DELETE FROM field_provenance
            WHERE lower(entity_type)='card' AND entity_id IN (SELECT id FROM cards WHERE game_id=%s)
        """, (game_id,))
        deleted["field_provenance_card"] = cur.rowcount

    if _table_exists(cur, "search_documents"):
        cur.execute("DELETE FROM search_documents WHERE game_id=%s", (game_id,))
        deleted["search_documents"] = cur.rowcount

    for table in ("print_field_provenance", "print_identifiers", "print_images"):
        if not _table_exists(cur, table):
            continue
        cur.execute(f"""
            DELETE FROM {table}
            WHERE print_id IN (
              SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
            )
        """, (game_id,))
        deleted[table] = cur.rowcount

    # These tables are ON DELETE CASCADE today, but explicit cleanup keeps the
    # operation deterministic even if a future migration changes FK actions.
    for table in ("print_attributes", "print_search_profiles", "print_releases"):
        if not _table_exists(cur, table):
            continue
        cur.execute(f"""
            DELETE FROM {table}
            WHERE print_id IN (
              SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s
            )
        """, (game_id,))
        deleted[table] = cur.rowcount

    cur.execute("DELETE FROM prints p USING cards c WHERE p.card_id=c.id AND c.game_id=%s", (game_id,))
    deleted["prints"] = cur.rowcount

    for table in ("card_attributes", "card_search_profiles"):
        if not _table_exists(cur, table):
            continue
        cur.execute(f"DELETE FROM {table} WHERE card_id IN (SELECT id FROM cards WHERE game_id=%s)", (game_id,))
        deleted[table] = cur.rowcount

    cur.execute("DELETE FROM cards WHERE game_id=%s", (game_id,))
    deleted["cards"] = cur.rowcount

    if _table_exists(cur, "products"):
        # Preflight requires zero MTG products. Refuse to silently discard any.
        product_count = _scalar(cur, "SELECT COUNT(*) FROM products WHERE game_id=%s", (game_id,))
        if product_count:
            raise AssertionError(f"MTG products appeared after preflight: {product_count}")

    cur.execute("DELETE FROM sets WHERE game_id=%s", (game_id,))
    deleted["sets"] = cur.rowcount
    return deleted


def _insert_sets(cur, root: Path, game_id: int) -> dict[str, int]:
    rows = []
    for row in _jsonl(root / "sets.jsonl"):
        rows.append((game_id, row["code"], row["name"], row.get("release_date")))
    cur.executemany(
        "INSERT INTO sets (game_id,code,name,release_date) VALUES (%s,%s,%s,%s)",
        rows,
    )
    cur.execute("SELECT code,id FROM sets WHERE game_id=%s", (game_id,))
    mapping = {str(code): int(id_) for code, id_ in cur.fetchall()}
    if len(mapping) != EXPECTED_COUNTS["sets"]:
        raise AssertionError(f"Set map mismatch: {len(mapping)}")
    return mapping


def _insert_cards(cur, root: Path, game_id: int) -> dict[str, int]:
    count = _copy_rows(
        cur,
        table="cards",
        columns=["game_id", "name", "card_key", "oracle_id"],
        rows=(
            [game_id, row["name"], row["card_key"], row.get("oracle_id")]
            for row in _jsonl(root / "cards.jsonl")
        ),
    )
    if count != EXPECTED_COUNTS["cards"]:
        raise AssertionError(f"Copied Card rows mismatch: {count}")
    cur.execute("SELECT card_key,id FROM cards WHERE game_id=%s", (game_id,))
    mapping = {str(key): int(id_) for key, id_ in cur.fetchall()}
    if len(mapping) != EXPECTED_COUNTS["cards"]:
        raise AssertionError(f"Card map mismatch: {len(mapping)}")
    return mapping


def _insert_card_attributes(cur, root: Path, card_ids: dict[str, int]) -> int:
    def rows():
        for row in _jsonl(root / "card_attributes.jsonl"):
            yield [
                card_ids[row["card_key"]],
                json.dumps(row["attributes"], ensure_ascii=False, separators=(",", ":")),
                row["source"],
                row.get("source_version"),
            ]
    return _copy_rows(
        cur,
        table="card_attributes",
        columns=["card_id", "attributes_json", "source", "source_version"],
        rows=rows(),
    )


def _insert_prints(cur, root: Path, card_ids: dict[str, int], set_ids: dict[str, int], game_id: int) -> dict[str, int]:
    def rows():
        for row in _jsonl(root / "prints.jsonl"):
            yield [
                set_ids[row["set_code"]],
                card_ids[row["card_key"]],
                row["collector_number"],
                row.get("language"),
                row.get("rarity"),
                "true" if row["is_foil"] else "false",
                row["variant"],
                row["print_key"],
                row["scryfall_id"],
            ]
    count = _copy_rows(
        cur,
        table="prints",
        columns=[
            "set_id", "card_id", "collector_number", "language", "rarity",
            "is_foil", "variant", "print_key", "scryfall_id",
        ],
        rows=rows(),
    )
    if count != EXPECTED_COUNTS["prints"]:
        raise AssertionError(f"Copied Print rows mismatch: {count}")
    cur.execute("""
        SELECT p.print_key,p.id
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
    """, (game_id,))
    mapping = {str(key): int(id_) for key, id_ in cur.fetchall()}
    if len(mapping) != EXPECTED_COUNTS["prints"]:
        raise AssertionError(f"Print map mismatch: {len(mapping)}")
    return mapping


def _insert_print_attributes(cur, root: Path, print_ids: dict[str, int]) -> int:
    def rows():
        for row in _jsonl(root / "print_attributes.jsonl"):
            yield [
                print_ids[row["print_key"]],
                json.dumps(row["attributes"], ensure_ascii=False, separators=(",", ":")),
                row["source"],
                row.get("source_version"),
            ]
    return _copy_rows(
        cur,
        table="print_attributes",
        columns=["print_id", "attributes_json", "source", "source_version"],
        rows=rows(),
    )


def _insert_print_identifiers(cur, root: Path, print_ids: dict[str, int]) -> int:
    return _copy_rows(
        cur,
        table="print_identifiers",
        columns=["print_id", "source", "external_id"],
        rows=(
            [print_ids[row["print_key"]], row["source"], row["external_id"]]
            for row in _jsonl(root / "print_identifiers.jsonl")
        ),
    )


def _insert_print_images(cur, root: Path, print_ids: dict[str, int]) -> int:
    return _copy_rows(
        cur,
        table="print_images",
        columns=["print_id", "url", "is_primary", "source"],
        rows=(
            [
                print_ids[row["print_key"]],
                row["url"],
                "true" if row["is_primary"] else "false",
                row.get("source"),
            ]
            for row in _jsonl(root / "print_images.jsonl")
        ),
    )


def _validate_live(cur, game_id: int, before_other_games: dict) -> dict:
    counts = {
        "sets": _scalar(cur, "SELECT COUNT(*) FROM sets WHERE game_id=%s", (game_id,)),
        "cards": _scalar(cur, "SELECT COUNT(*) FROM cards WHERE game_id=%s", (game_id,)),
        "prints": _scalar(cur, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "card_attributes": _scalar(cur, "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=%s", (game_id,)),
        "print_attributes": _scalar(cur, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "print_identifiers": _scalar(cur, "SELECT COUNT(*) FROM print_identifiers pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "print_images": _scalar(cur, "SELECT COUNT(*) FROM print_images i JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
    }
    if counts != EXPECTED_COUNTS:
        raise AssertionError(f"Live MTG counts mismatch: {counts} != {EXPECTED_COUNTS}")

    cur.execute("""
        SELECT p.variant, COUNT(*)
        FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s GROUP BY p.variant ORDER BY p.variant
    """, (game_id,))
    finishes = {str(variant): int(count) for variant, count in cur.fetchall()}
    if finishes != EXPECTED_FINISHES:
        raise AssertionError(f"Live finish counts mismatch: {finishes}")

    oracle_null = _scalar(cur, "SELECT COUNT(*) FROM cards WHERE game_id=%s AND oracle_id IS NULL", (game_id,))
    if oracle_null != 71:
        raise AssertionError(f"Expected 71 fallback logical Cards, found {oracle_null}")
    missing_print_keys = _scalar(cur, """
        SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND (p.print_key IS NULL OR p.scryfall_id IS NULL)
    """, (game_id,))
    if missing_print_keys:
        raise AssertionError(f"MTG Prints missing exact identity fields: {missing_print_keys}")

    cur.execute("SELECT slug,id FROM games")
    slug_ids = {str(slug): int(id_) for slug, id_ in cur.fetchall()}
    after_all = _catalog_counts_by_game(cur)
    after_other = {slug: value for slug, value in after_all.items() if slug != "mtg"}
    if after_other != before_other_games:
        raise AssertionError("A non-MTG catalog changed during MTG bootstrap")

    economics = _economics_counts(cur, game_id)
    if sum(economics.values()) != 0:
        raise AssertionError(f"Economics unexpectedly appeared during bootstrap: {economics}")

    return {
        "counts": counts,
        "finish_counts": finishes,
        "fallback_cards_without_oracle": oracle_null,
        "economics": economics,
        "non_mtg_catalogs_unchanged": True,
    }


def run(*, snapshot_dir: Path, output_path: Path) -> dict:
    manifest = _verify_snapshot(snapshot_dir)
    report: dict = {
        "status": "started",
        "snapshot_schema_version": manifest["snapshot_schema_version"],
        "snapshot_source": manifest["source"],
        "expected_counts": EXPECTED_COUNTS,
        "expected_finishes": EXPECTED_FINISHES,
    }

    conn = psycopg2.connect(_db_url())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '30s'")
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit-mtg-v22-bootstrap'))")

            cur.execute("SELECT version_num FROM alembic_version")
            revision = str(cur.fetchone()[0])
            _assert_supported_schema_revision(revision)

            cur.execute("SELECT id FROM games WHERE slug='mtg'")
            found = cur.fetchone()
            if not found:
                raise AssertionError("MTG game row missing")
            game_id = int(found[0])

            before_all = _catalog_counts_by_game(cur)
            prelive = before_all.get("mtg")
            if prelive != EXPECTED_PRELIVE:
                raise AssertionError(f"Live MTG changed since certified preflight: {prelive} != {EXPECTED_PRELIVE}")
            before_other = {slug: value for slug, value in before_all.items() if slug != "mtg"}

            economics = _economics_counts(cur, game_id)
            if sum(economics.values()) != 0:
                raise AssertionError(f"Refusing rebuild because MTG economics now exist: {economics}")

            if _table_exists(cur, "products"):
                product_count = _scalar(cur, "SELECT COUNT(*) FROM products WHERE game_id=%s", (game_id,))
                if product_count:
                    raise AssertionError(f"Refusing rebuild because MTG products now exist: {product_count}")

            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes_before = int(cur.fetchone()[0])

            deleted = _delete_legacy_mtg(cur, game_id)
            if deleted.get("cards") != EXPECTED_PRELIVE["cards"] or deleted.get("prints") != EXPECTED_PRELIVE["prints"] or deleted.get("sets") != EXPECTED_PRELIVE["sets"]:
                raise AssertionError(f"Legacy deletion counts changed unexpectedly: {deleted}")

            set_ids = _insert_sets(cur, snapshot_dir, game_id)
            card_ids = _insert_cards(cur, snapshot_dir, game_id)
            loaded = {
                "sets": len(set_ids),
                "cards": len(card_ids),
                "card_attributes": _insert_card_attributes(cur, snapshot_dir, card_ids),
            }
            print_ids = _insert_prints(cur, snapshot_dir, card_ids, set_ids, game_id)
            loaded["prints"] = len(print_ids)
            loaded["print_attributes"] = _insert_print_attributes(cur, snapshot_dir, print_ids)
            loaded["print_identifiers"] = _insert_print_identifiers(cur, snapshot_dir, print_ids)
            loaded["print_images"] = _insert_print_images(cur, snapshot_dir, print_ids)
            if loaded != EXPECTED_COUNTS:
                raise AssertionError(f"COPY row counts mismatch: {loaded}")

            validation = _validate_live(cur, game_id, before_other)
            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes_precommit = int(cur.fetchone()[0])

            report.update(
                {
                    "status": "validated_precommit",
                    "database_bytes_before": database_bytes_before,
                    "database_bytes_precommit": database_bytes_precommit,
                    "deleted": deleted,
                    "loaded": loaded,
                    "validation": validation,
                    "database_writes_transactional": True,
                }
            )
        conn.commit()

        with conn.cursor() as cur:
            post_validation = _validate_live(
                cur,
                game_id,
                {slug: value for slug, value in _catalog_counts_by_game(cur).items() if slug != "mtg"},
            )
            # The second validation above verifies MTG counts/economics. The
            # original transaction already proved non-MTG catalogs unchanged.
            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes_after = int(cur.fetchone()[0])
        conn.rollback()

        report["status"] = "pass"
        report["database_bytes_after"] = database_bytes_after
        report["database_size_delta_bytes"] = database_bytes_after - report["database_bytes_before"]
        report["postcommit_mtg_validation"] = {
            "counts": post_validation["counts"],
            "finish_counts": post_validation["finish_counts"],
            "fallback_cards_without_oracle": post_validation["fallback_cards_without_oracle"],
            "economics": post_validation["economics"],
        }
    except Exception as exc:
        conn.rollback()
        report["status"] = "rolled_back"
        report["error"] = f"{type(exc).__name__}: {exc}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        conn.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply certified MTG V2.2 snapshot to live Neon in one transaction")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output_path=args.output)


if __name__ == "__main__":
    main()
