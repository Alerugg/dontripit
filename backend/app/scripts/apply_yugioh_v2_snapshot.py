from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.scripts.verify_yugioh_v2_snapshot_manifest import FROZEN_COUNTS, run as verify_manifest


LEGACY_EXPECTED = {
    "sets": 5463,
    "cards": 2010,
    "prints": 6216,
    "print_images": 6221,
    "search_documents": 13689,
    "card_attributes": 0,
    "print_attributes": 0,
    "catalog_releases": 0,
    "print_releases": 0,
    "card_search_profiles": 0,
    "print_search_profiles": 0,
}

TARGET = {
    "sets": 646,
    "cards": 14479,
    "prints": 44226,
    "card_attributes": 14479,
    "print_attributes": 44226,
    "print_images": 44226,
    "catalog_releases": 1032,
    "print_releases": 44226,
    "cards_without_print_evidence": 490,
    "deduplicated_source_print_rows": 52,
    "no_hyphen_family_fallback_rows": 12,
    "source_card_aliases_merged": 1,
    "excluded_source_print_rows": 9,
    "noisy_rarity_source_rows": 206,
}

EXPECTED_FILES = {
    "sets.jsonl": TARGET["sets"],
    "cards.jsonl": TARGET["cards"],
    "card_attributes.jsonl": TARGET["card_attributes"],
    "artwork_candidates.jsonl": 14644,
    "source_conflicts.jsonl": TARGET["excluded_source_print_rows"],
    "prints.jsonl": TARGET["prints"],
    "print_attributes.jsonl": TARGET["print_attributes"],
    "representative_print_images.jsonl": TARGET["print_images"],
    "catalog_releases.jsonl": TARGET["catalog_releases"],
    "print_releases.jsonl": TARGET["print_releases"],
}

ALLOWED_REBUILDABLE_FK_TABLES = {
    "prints",
    "print_images",
    "print_identifiers",
    "card_attributes",
    "print_attributes",
    "print_releases",
}

NOISY_RARITIES = (
    "2",
    "3",
    "European & Oceanian debut",
    "European debut",
    "New",
    "New artwork",
    "Oceanian debut",
    "Reprint",
    "force-SMW",
)


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSONL {path.name}:{line_number}: {exc}") from exc


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _null(value):
    return r"\N" if value is None else value


def _json_value(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _copy(cursor, sql: str, rows) -> int:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    count = 0
    for row in rows:
        writer.writerow([_null(value) for value in row])
        count += 1
    buffer.seek(0)
    cursor.copy_expert(sql, buffer)
    return count


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table_name}",))
    return bool(cursor.fetchone()[0])


def _safe_ident(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError(f"Unsafe identifier: {value!r}")
    return '"' + value + '"'


def _scalar(cursor, sql: str, params=()) -> int:
    cursor.execute(sql, params)
    return int(cursor.fetchone()[0] or 0)


def _legacy_counts(cursor, game_id: int) -> dict:
    queries = {
        "sets": "SELECT COUNT(*) FROM sets WHERE game_id=%s",
        "cards": "SELECT COUNT(*) FROM cards WHERE game_id=%s",
        "prints": "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        "print_images": "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        "card_attributes": "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=%s",
        "print_attributes": "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        "catalog_releases": "SELECT COUNT(*) FROM catalog_releases WHERE game_id=%s",
        "print_releases": "SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s",
        "search_documents": "SELECT COUNT(*) FROM search_documents WHERE game_id=%s",
        "card_search_profiles": "SELECT COUNT(*) FROM card_search_profiles WHERE game_id=%s",
        "print_search_profiles": "SELECT COUNT(*) FROM print_search_profiles WHERE game_id=%s",
    }
    output = {}
    for table_name, sql in queries.items():
        if not _table_exists(cursor, table_name):
            output[table_name] = 0
            continue
        output[table_name] = _scalar(cursor, sql, (game_id,))
    return output


def _core_ids(cursor, game_id: int) -> dict[str, list[int]]:
    cursor.execute("SELECT id FROM sets WHERE game_id=%s ORDER BY id", (game_id,))
    set_ids = [int(row[0]) for row in cursor.fetchall()]
    cursor.execute("SELECT id FROM cards WHERE game_id=%s ORDER BY id", (game_id,))
    card_ids = [int(row[0]) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s ORDER BY p.id",
        (game_id,),
    )
    print_ids = [int(row[0]) for row in cursor.fetchall()]
    return {"sets": set_ids, "cards": card_ids, "prints": print_ids}


def _assert_no_durable_fk_rows(cursor, game_id: int) -> list[dict]:
    ids = _core_ids(cursor, game_id)
    cursor.execute(
        """
        SELECT DISTINCT
          tc.table_name AS dependent_table,
          kcu.column_name AS dependent_column,
          ccu.table_name AS referenced_table,
          ccu.column_name AS referenced_column,
          rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name=kcu.constraint_name
         AND tc.constraint_schema=kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name=tc.constraint_name
         AND ccu.constraint_schema=tc.constraint_schema
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_name=tc.constraint_name
         AND rc.constraint_schema=tc.constraint_schema
        WHERE tc.constraint_type='FOREIGN KEY'
          AND tc.table_schema='public'
          AND ccu.table_name IN ('sets','cards','prints')
        ORDER BY ccu.table_name, tc.table_name, kcu.column_name
        """
    )
    relationships = []
    blockers = []
    for dependent_table, dependent_column, referenced_table, referenced_column, delete_rule in cursor.fetchall():
        if dependent_table == "prints":
            classification = "core"
        elif dependent_table in ALLOWED_REBUILDABLE_FK_TABLES:
            classification = "rebuildable"
        else:
            classification = "durable_or_unknown"
        referenced_ids = ids.get(referenced_table, []) or [-1]
        table_sql = _safe_ident(dependent_table)
        column_sql = _safe_ident(dependent_column)
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_sql} WHERE {column_sql} = ANY(%s)",
            (referenced_ids,),
        )
        count = int(cursor.fetchone()[0] or 0)
        row = {
            "dependent_table": dependent_table,
            "dependent_column": dependent_column,
            "referenced_table": referenced_table,
            "delete_rule": delete_rule,
            "rows": count,
            "classification": classification,
        }
        relationships.append(row)
        if count and classification == "durable_or_unknown":
            blockers.append(row)
    if blockers:
        raise AssertionError(f"Durable/unknown YGO FK rows appeared since certification: {blockers}")
    return relationships


def _delete_rebuildable_legacy(cursor, game_id: int) -> dict[str, int]:
    delete_sql = {
        "print_releases": "DELETE FROM print_releases WHERE print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)",
        "print_attributes": "DELETE FROM print_attributes WHERE print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)",
        "card_attributes": "DELETE FROM card_attributes WHERE card_id IN (SELECT id FROM cards WHERE game_id=%s)",
        "print_identifiers": "DELETE FROM print_identifiers WHERE print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)",
        "print_images": "DELETE FROM print_images WHERE print_id IN (SELECT p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s)",
        "print_search_profiles": "DELETE FROM print_search_profiles WHERE game_id=%s",
        "card_search_profiles": "DELETE FROM card_search_profiles WHERE game_id=%s",
        "facet_definitions": "DELETE FROM facet_definitions WHERE game_id=%s",
        "search_documents": "DELETE FROM search_documents WHERE game_id=%s",
        "catalog_releases": "DELETE FROM catalog_releases WHERE game_id=%s",
    }
    deleted = {}
    for table_name, sql in delete_sql.items():
        if not _table_exists(cursor, table_name):
            deleted[table_name] = 0
            continue
        cursor.execute(sql, (game_id,))
        deleted[table_name] = int(cursor.rowcount or 0)

    # Polymorphic provenance has no FK, so remove source-recreatable legacy rows explicitly.
    if _table_exists(cursor, "field_provenance"):
        ids = _core_ids(cursor, game_id)
        total = 0
        for entity_type, entity_ids in (
            ("set", ids["sets"]),
            ("card", ids["cards"]),
            ("print", ids["prints"]),
        ):
            if not entity_ids:
                continue
            cursor.execute(
                "DELETE FROM field_provenance WHERE entity_type=%s AND entity_id = ANY(%s)",
                (entity_type, entity_ids),
            )
            total += int(cursor.rowcount or 0)
        deleted["field_provenance"] = total
    return deleted


def _load_mappings(cursor, sql: str) -> dict[str, int]:
    cursor.execute(sql)
    return {str(key): int(value) for key, value in cursor.fetchall()}


def _insert_sets(cursor, snapshot_dir: Path, game_id: int) -> int:
    return _copy(
        cursor,
        "COPY sets (game_id, code, yugioh_id, name, release_date) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                game_id,
                row["code"],
                row["yugioh_id"],
                row["name"],
                row.get("release_date"),
            )
            for row in _jsonl(snapshot_dir / "sets.jsonl")
        ),
    )


def _insert_cards(cursor, snapshot_dir: Path, game_id: int) -> int:
    return _copy(
        cursor,
        "COPY cards (game_id, name, card_key, yugoprodeck_id) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (game_id, row["name"], row["card_key"], row["yugoprodeck_id"])
            for row in _jsonl(snapshot_dir / "cards.jsonl")
        ),
    )


def _insert_prints(cursor, snapshot_dir: Path, set_ids: dict[str, int], card_ids: dict[str, int]) -> int:
    def rows():
        for row in _jsonl(snapshot_dir / "prints.jsonl"):
            yield (
                set_ids[row["set_family"]],
                card_ids[row["source_card_id"]],
                row["collector_number"],
                row["language"],
                row["rarity"],
                "true" if row["is_foil"] else "false",
                row["variant"],
                row["print_key"],
                row["yugioh_id"],
            )
    return _copy(
        cursor,
        "COPY prints (set_id, card_id, collector_number, language, rarity, is_foil, variant, print_key, yugioh_id) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        rows(),
    )


def _insert_card_attributes(cursor, snapshot_dir: Path, card_ids: dict[str, int]) -> int:
    return _copy(
        cursor,
        "COPY card_attributes (card_id, attributes_json, source, source_version) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                card_ids[row["source_card_id"]],
                _json_value(row["attributes"]),
                row["source"],
                row.get("source_version"),
            )
            for row in _jsonl(snapshot_dir / "card_attributes.jsonl")
        ),
    )


def _insert_print_attributes(cursor, snapshot_dir: Path, print_ids: dict[str, int]) -> int:
    return _copy(
        cursor,
        "COPY print_attributes (print_id, attributes_json, source, source_version) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                print_ids[row["source_print_id"]],
                _json_value(row["attributes"]),
                row["source"],
                row.get("source_version"),
            )
            for row in _jsonl(snapshot_dir / "print_attributes.jsonl")
        ),
    )


def _insert_print_images(cursor, snapshot_dir: Path, print_ids: dict[str, int]) -> int:
    return _copy(
        cursor,
        "COPY print_images (print_id, url, is_primary, source) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                print_ids[row["source_print_id"]],
                row["url"],
                "true" if row["is_primary"] else "false",
                row.get("source"),
            )
            for row in _jsonl(snapshot_dir / "representative_print_images.jsonl")
        ),
    )


def _insert_releases(cursor, snapshot_dir: Path, game_id: int) -> int:
    return _copy(
        cursor,
        "COPY catalog_releases (game_id, source, external_id, name, code, release_type, release_date, language, region, metadata_json) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                game_id,
                row["source"],
                row["external_id"],
                row["name"],
                row.get("code"),
                row.get("release_type"),
                row.get("release_date"),
                row.get("language"),
                row.get("region"),
                _json_value(row.get("metadata") or {}),
            )
            for row in _jsonl(snapshot_dir / "catalog_releases.jsonl")
        ),
    )


def _insert_print_releases(
    cursor,
    snapshot_dir: Path,
    print_ids: dict[str, int],
    release_ids: dict[str, int],
) -> int:
    return _copy(
        cursor,
        "COPY print_releases (print_id, release_id, source_print_id, appearance_type, metadata_json) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
        (
            (
                print_ids[row["source_print_id"]],
                release_ids[row["release_external_id"]],
                row.get("source_print_reference"),
                row.get("appearance_type"),
                _json_value(row.get("metadata") or {}),
            )
            for row in _jsonl(snapshot_dir / "print_releases.jsonl")
        ),
    )


def _postconditions(cursor, game_id: int) -> dict:
    counts = {
        "sets": _scalar(cursor, "SELECT COUNT(*) FROM sets WHERE game_id=%s", (game_id,)),
        "cards": _scalar(cursor, "SELECT COUNT(*) FROM cards WHERE game_id=%s", (game_id,)),
        "prints": _scalar(cursor, "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "card_attributes": _scalar(cursor, "SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=%s", (game_id,)),
        "print_attributes": _scalar(cursor, "SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "print_images": _scalar(cursor, "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "catalog_releases": _scalar(cursor, "SELECT COUNT(*) FROM catalog_releases WHERE game_id=%s", (game_id,)),
        "print_releases": _scalar(cursor, "SELECT COUNT(*) FROM print_releases pr JOIN prints p ON p.id=pr.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=%s", (game_id,)),
        "cards_without_print_evidence": _scalar(cursor, "SELECT COUNT(*) FROM cards c LEFT JOIN prints p ON p.card_id=c.id WHERE c.game_id=%s GROUP BY c.game_id HAVING COUNT(*) FILTER (WHERE p.id IS NULL) >= 0", (game_id,)) if False else 0,
    }
    # Explicit no-print query (kept separate because aggregate-by-game expression is easy to misread).
    counts["cards_without_print_evidence"] = _scalar(
        cursor,
        "SELECT COUNT(*) FROM cards c WHERE c.game_id=%s AND NOT EXISTS (SELECT 1 FROM prints p WHERE p.card_id=c.id)",
        (game_id,),
    )

    for key in (
        "sets",
        "cards",
        "prints",
        "card_attributes",
        "print_attributes",
        "print_images",
        "catalog_releases",
        "print_releases",
        "cards_without_print_evidence",
    ):
        expected = TARGET[key]
        if counts[key] != expected:
            raise AssertionError(f"YGO postcondition {key}={counts[key]} expected={expected}")

    search_counts = {}
    for table_name in ("search_documents", "card_search_profiles", "print_search_profiles", "facet_definitions"):
        if _table_exists(cursor, table_name):
            search_counts[table_name] = _scalar(
                cursor, f"SELECT COUNT(*) FROM {_safe_ident(table_name)} WHERE game_id=%s", (game_id,)
            )
            if search_counts[table_name] != 0:
                raise AssertionError(f"YGO Search V2 must remain empty until separately rebuilt: {table_name}={search_counts[table_name]}")

    fallback_rows = _scalar(
        cursor,
        """
        SELECT COUNT(*)
        FROM print_attributes pa
        JOIN prints p ON p.id=pa.print_id
        JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
          AND pa.attributes_json->>'family_resolution'='same_release_unanimous_fallback'
        """,
        (game_id,),
    )
    if fallback_rows != TARGET["no_hyphen_family_fallback_rows"]:
        raise AssertionError(f"DB1 fallback rows={fallback_rows}")

    deduped_source_rows = _scalar(
        cursor,
        """
        SELECT COALESCE(SUM(GREATEST(jsonb_array_length(pa.attributes_json->'source_rows') - 1, 0)), 0)
        FROM print_attributes pa
        JOIN prints p ON p.id=pa.print_id
        JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s
        """,
        (game_id,),
    )
    if deduped_source_rows != TARGET["deduplicated_source_print_rows"]:
        raise AssertionError(f"Deduplicated source rows={deduped_source_rows}")

    noisy_rows = _scalar(
        cursor,
        """
        SELECT COUNT(*)
        FROM print_attributes pa
        JOIN prints p ON p.id=pa.print_id
        JOIN cards c ON c.id=p.card_id
        CROSS JOIN LATERAL jsonb_array_elements(pa.attributes_json->'source_rows') sr
        WHERE c.game_id=%s
          AND sr->>'rarity_raw' = ANY(%s)
        """,
        (game_id, list(NOISY_RARITIES)),
    )
    if noisy_rows != TARGET["noisy_rarity_source_rows"]:
        raise AssertionError(f"Noisy rarity source rows={noisy_rows}")

    alias_cards = _scalar(
        cursor,
        "SELECT COUNT(*) FROM cards WHERE game_id=%s AND yugoprodeck_id='300302053'",
        (game_id,),
    )
    canonical_spell = _scalar(
        cursor,
        "SELECT COUNT(*) FROM cards WHERE game_id=%s AND yugoprodeck_id='300302018' AND name='Spell of Mask'",
        (game_id,),
    )
    alias_evidence = _scalar(
        cursor,
        """
        SELECT COUNT(*) FROM card_attributes ca
        JOIN cards c ON c.id=ca.card_id
        WHERE c.game_id=%s AND c.yugoprodeck_id='300302018'
          AND ca.attributes_json->'source_alias_ids' ? '300302053'
        """,
        (game_id,),
    )
    spell_prints = _scalar(
        cursor,
        """
        SELECT COUNT(*) FROM prints p
        JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=%s AND c.yugoprodeck_id='300302018' AND p.collector_number='SBCB-ENS08'
        """,
        (game_id,),
    )
    if alias_cards != 0 or canonical_spell != 1 or alias_evidence != 1 or spell_prints != 1:
        raise AssertionError(
            f"Spell of Mask alias gate failed alias_cards={alias_cards} canonical={canonical_spell} evidence={alias_evidence} prints={spell_prints}"
        )

    bad_assignments = [
        ("72843899", "BLCR-EN012"),
        ("46358784", "BLCR-EN013"),
        ("71620241", "BLCR-EN015"),
        ("45236142", "BLCR-EN016"),
        ("88120966", "LDS3-EN063"),
        ("94820406", "SGX3-ENA11"),
        ("24508238", "SGX3-ENE10"),
        ("78060096", "SGX3-ENI25"),
    ]
    for card_external_id, collector in bad_assignments:
        bad_count = _scalar(
            cursor,
            """
            SELECT COUNT(*) FROM prints p
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=%s AND c.yugoprodeck_id=%s AND p.collector_number=%s
            """,
            (game_id, card_external_id, collector),
        )
        if bad_count:
            raise AssertionError(f"Quarantined source conflict entered canonical DB: {card_external_id} {collector}")

    database_bytes = _scalar(cursor, "SELECT pg_database_size(current_database())")
    database_mib = round(database_bytes / 1024 / 1024, 2)
    if database_bytes >= 500 * 1024 * 1024:
        raise AssertionError(f"Pre-commit database size gate exceeded: {database_mib} MiB")

    return {
        "canonical_counts": counts,
        "search_counts": search_counts,
        "fallback_rows": fallback_rows,
        "deduplicated_source_rows": deduped_source_rows,
        "noisy_rarity_source_rows": noisy_rows,
        "spell_of_mask": {
            "alias_card_rows": alias_cards,
            "canonical_card_rows": canonical_spell,
            "alias_evidence_rows": alias_evidence,
            "physical_print_rows": spell_prints,
        },
        "precommit_database_mib": database_mib,
    }


def run(
    *,
    snapshot_dir: Path,
    report_path: Path | None = None,
    artifact_sha256: str | None = None,
) -> dict:
    manifest_path = snapshot_dir / "manifest.json"
    manifest = verify_manifest(manifest_path)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    file_validation = {}
    manifest_sizes = manifest_payload.get("bytes_by_file") or {}
    for filename, expected_rows in EXPECTED_FILES.items():
        path = snapshot_dir / filename
        if not path.exists():
            raise AssertionError(f"Snapshot file missing: {filename}")
        rows = _line_count(path)
        if rows != expected_rows:
            raise AssertionError(f"Snapshot file rows moved: {filename}={rows} expected={expected_rows}")
        expected_bytes = manifest_sizes.get(filename)
        actual_bytes = path.stat().st_size
        if expected_bytes is not None and actual_bytes != expected_bytes:
            raise AssertionError(f"Snapshot file size mismatch: {filename}={actual_bytes} manifest={expected_bytes}")
        file_validation[filename] = {
            "rows": rows,
            "bytes": actual_bytes,
            "sha256": _sha256(path),
        }

    db.init_engine()
    raw = db.engine.raw_connection()
    cursor = raw.cursor()
    started_at = datetime.now(timezone.utc)
    report: dict = {
        "started_at": started_at.isoformat(),
        "mode": "transactional_yugioh_v2_canonical_replace",
        "input_artifact_sha256": artifact_sha256,
        "snapshot_manifest": manifest,
        "snapshot_files": file_validation,
        "status": "running",
    }

    try:
        cursor.execute("SET LOCAL statement_timeout = '15min'")
        cursor.execute("SET LOCAL lock_timeout = '30s'")
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('dontripit:yugioh-v2-canonical-replace'))")

        cursor.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
        game_row = cursor.fetchone()
        if not game_row:
            raise AssertionError("Yu-Gi-Oh game row is missing")
        game_id = int(game_row[0])

        legacy_before = _legacy_counts(cursor, game_id)
        if legacy_before != LEGACY_EXPECTED:
            raise AssertionError(
                f"Legacy YGO state changed since backup/certification: {legacy_before} != {LEGACY_EXPECTED}"
            )
        fk_relationships = _assert_no_durable_fk_rows(cursor, game_id)

        deleted = _delete_rebuildable_legacy(cursor, game_id)
        cursor.execute(
            "DELETE FROM prints WHERE card_id IN (SELECT id FROM cards WHERE game_id=%s)",
            (game_id,),
        )
        deleted["prints"] = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM cards WHERE game_id=%s", (game_id,))
        deleted["cards"] = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM sets WHERE game_id=%s", (game_id,))
        deleted["sets"] = int(cursor.rowcount or 0)

        inserted = {}
        inserted["sets"] = _insert_sets(cursor, snapshot_dir, game_id)
        set_ids = _load_mappings(
            cursor,
            f"SELECT code, id FROM sets WHERE game_id={game_id}",
        )
        if len(set_ids) != TARGET["sets"]:
            raise AssertionError(f"Set mapping incomplete: {len(set_ids)}")

        inserted["cards"] = _insert_cards(cursor, snapshot_dir, game_id)
        card_ids = _load_mappings(
            cursor,
            f"SELECT yugoprodeck_id, id FROM cards WHERE game_id={game_id}",
        )
        if len(card_ids) != TARGET["cards"]:
            raise AssertionError(f"Card mapping incomplete: {len(card_ids)}")

        inserted["prints"] = _insert_prints(cursor, snapshot_dir, set_ids, card_ids)
        print_ids = _load_mappings(
            cursor,
            f"SELECT p.yugioh_id, p.id FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id={game_id}",
        )
        if len(print_ids) != TARGET["prints"]:
            raise AssertionError(f"Print mapping incomplete: {len(print_ids)}")

        inserted["card_attributes"] = _insert_card_attributes(cursor, snapshot_dir, card_ids)
        inserted["print_attributes"] = _insert_print_attributes(cursor, snapshot_dir, print_ids)
        inserted["print_images"] = _insert_print_images(cursor, snapshot_dir, print_ids)
        inserted["catalog_releases"] = _insert_releases(cursor, snapshot_dir, game_id)
        release_ids = _load_mappings(
            cursor,
            f"SELECT external_id, id FROM catalog_releases WHERE game_id={game_id} AND source='ygoprodeck'",
        )
        if len(release_ids) != TARGET["catalog_releases"]:
            raise AssertionError(f"Release mapping incomplete: {len(release_ids)}")
        inserted["print_releases"] = _insert_print_releases(
            cursor, snapshot_dir, print_ids, release_ids
        )

        for key in (
            "sets",
            "cards",
            "prints",
            "card_attributes",
            "print_attributes",
            "print_images",
            "catalog_releases",
            "print_releases",
        ):
            if inserted[key] != TARGET[key]:
                raise AssertionError(f"Inserted {key}={inserted[key]} expected={TARGET[key]}")

        post = _postconditions(cursor, game_id)

        report.update(
            {
                "game_id": game_id,
                "legacy_before": legacy_before,
                "fk_recheck": fk_relationships,
                "deleted": deleted,
                "inserted": inserted,
                "postconditions_precommit": post,
            }
        )
        raw.commit()
        report["status"] = "committed"
        report["committed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        raw.rollback()
        report["status"] = "rolled_back"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(report_path, report)
        raise
    finally:
        cursor.close()
        raw.close()

    # Commit-visibility check in a fresh connection. All correctness gates already ran
    # before commit; this confirms the committed rows are visible and records actual size.
    raw2 = db.engine.raw_connection()
    cur2 = raw2.cursor()
    try:
        cur2.execute("SELECT id FROM games WHERE slug='yugioh' LIMIT 1")
        game_id = int(cur2.fetchone()[0])
        visible = _legacy_counts(cur2, game_id)
        report["visible_after_commit"] = visible
        for key in (
            "sets",
            "cards",
            "prints",
            "card_attributes",
            "print_attributes",
            "catalog_releases",
            "print_releases",
        ):
            if visible[key] != TARGET[key]:
                raise AssertionError(f"Post-commit visibility mismatch {key}={visible[key]}")
        if visible["print_images"] != TARGET["print_images"]:
            raise AssertionError("Post-commit print image count mismatch")
        if visible["search_documents"] or visible["card_search_profiles"] or visible["print_search_profiles"]:
            raise AssertionError("YGO search data unexpectedly exists immediately after canonical replacement")
        report["database_mib_after_commit"] = round(
            _scalar(cur2, "SELECT pg_database_size(current_database())") / 1024 / 1024, 2
        )
        raw2.rollback()
    finally:
        cur2.close()
        raw2.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--artifact-sha256", default=None)
    args = parser.parse_args()
    run(
        snapshot_dir=args.snapshot_dir,
        report_path=args.report_path,
        artifact_sha256=args.artifact_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
