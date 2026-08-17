from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


SOURCE_SCHEMA = "mtg-canonical-v2.2"
TARGET_SCHEMA = "mtg-canonical-v2.3"
EXACT_IDENTIFIER_SOURCE = "scryfall_finish"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise AssertionError(f"{path.name}:{line_no} is not an object")
            yield row


def _verify_source(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise AssertionError("Source MTG V2.2 manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Source snapshot is not PASS")
    if manifest.get("snapshot_schema_version") != SOURCE_SCHEMA:
        raise AssertionError(
            f"Expected source schema {SOURCE_SCHEMA}, got {manifest.get('snapshot_schema_version')!r}"
        )

    for name, expected in (manifest.get("files") or {}).items():
        path = root / name
        if not path.exists():
            raise AssertionError(f"Missing source snapshot file: {name}")
        if _sha256(path) != expected.get("sha256"):
            raise AssertionError(f"Source snapshot checksum mismatch: {name}")
        with path.open("r", encoding="utf-8") as handle:
            rows = sum(1 for _ in handle)
        if rows != int(expected.get("rows") or -1):
            raise AssertionError(f"Source snapshot row mismatch: {name}")
    return manifest


def _file_metadata(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        rows = sum(1 for _ in handle)
    return {
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "rows": rows,
    }


def run(*, source_dir: Path, output_dir: Path) -> dict:
    source_manifest = _verify_source(source_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy every certified canonical file byte-for-byte except the auxiliary
    # PrintIdentifier mapping. Card/Print/Set identity and attributes therefore
    # remain exactly the already-certified V2.2 snapshot.
    for source_path in source_dir.glob("*.jsonl"):
        if source_path.name == "print_identifiers.jsonl":
            continue
        shutil.copy2(source_path, output_dir / source_path.name)

    prints_by_key: dict[str, tuple[str, str]] = {}
    duplicate_source_objects = 0
    source_object_counts: dict[str, int] = {}
    for row in _jsonl(source_dir / "prints.jsonl"):
        print_key = str(row.get("print_key") or "").strip()
        scryfall_id = str(row.get("scryfall_id") or "").strip().lower()
        finish = str(row.get("variant") or "").strip().lower()
        if not print_key or not scryfall_id or not finish:
            raise AssertionError(f"Print missing exact identity dimensions: {row!r}")
        if print_key in prints_by_key:
            raise AssertionError(f"Duplicate print_key in certified source: {print_key}")
        prints_by_key[print_key] = (scryfall_id, finish)
        source_object_counts[scryfall_id] = source_object_counts.get(scryfall_id, 0) + 1
    duplicate_source_objects = sum(1 for count in source_object_counts.values() if count > 1)

    target_path = output_dir / "print_identifiers.jsonl"
    seen_source_external: set[tuple[str, str]] = set()
    seen_print_source: set[tuple[str, str]] = set()
    source_rows = 0
    target_rows = 0

    with target_path.open("w", encoding="utf-8", newline="\n") as handle:
        for old in _jsonl(source_dir / "print_identifiers.jsonl"):
            source_rows += 1
            print_key = str(old.get("print_key") or "").strip()
            if print_key not in prints_by_key:
                raise AssertionError(f"Identifier references unknown print_key: {print_key}")
            scryfall_id, finish = prints_by_key[print_key]

            # V2.2 intentionally exposed the real source-object id here, which
            # is shared by multiple exact finishes. V2.3 keeps that real id on
            # prints.scryfall_id and gives PrintIdentifier a clearly-derived,
            # finish-aware namespace compatible with its one-owner DB contract.
            external_id = f"{scryfall_id}:{finish}"
            source_external = (EXACT_IDENTIFIER_SOURCE, external_id)
            print_source = (print_key, EXACT_IDENTIFIER_SOURCE)
            if source_external in seen_source_external:
                raise AssertionError(f"Duplicate exact identifier: {source_external}")
            if print_source in seen_print_source:
                raise AssertionError(f"Duplicate source for exact Print: {print_source}")
            seen_source_external.add(source_external)
            seen_print_source.add(print_source)

            row = {
                "print_key": print_key,
                "source": EXACT_IDENTIFIER_SOURCE,
                "external_id": external_id,
            }
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            target_rows += 1

    expected_prints = int(source_manifest["counts"]["exact_prints"])
    if len(prints_by_key) != expected_prints:
        raise AssertionError(f"Certified Print rows changed: {len(prints_by_key)} != {expected_prints}")
    if source_rows != expected_prints or target_rows != expected_prints:
        raise AssertionError(
            f"PrintIdentifier rows must remain one per exact Print: source={source_rows} target={target_rows} prints={expected_prints}"
        )

    files = {}
    for path in sorted(output_dir.glob("*.jsonl")):
        files[path.name] = _file_metadata(path)

    # Prove that every file except PrintIdentifier is byte-identical to V2.2.
    unchanged_files = []
    for name, metadata in files.items():
        if name == "print_identifiers.jsonl":
            continue
        source_meta = source_manifest["files"].get(name)
        if not source_meta or metadata["sha256"] != source_meta.get("sha256"):
            raise AssertionError(f"Canonical V2.2 file changed during identifier-only derivation: {name}")
        unchanged_files.append(name)

    manifest = dict(source_manifest)
    manifest["snapshot_schema_version"] = TARGET_SCHEMA
    manifest["identity_policy_version"] = (
        "oracle-or-rules-signature+scryfall-object-finish-v1;"
        "exact-print-identifier=scryfall_finish:<scryfall_id>:<finish>-v1"
    )
    manifest["files"] = files
    manifest["derived_from"] = {
        "snapshot_schema_version": SOURCE_SCHEMA,
        "source_bulk_id": source_manifest.get("source", {}).get("bulk_id"),
        "source_bulk_updated_at": source_manifest.get("source", {}).get("updated_at"),
        "canonical_files_byte_identical": sorted(unchanged_files),
        "only_semantic_file_changed": "print_identifiers.jsonl",
    }
    manifest["identifier_policy"] = {
        "source_object_column": "prints.scryfall_id",
        "exact_print_identifier_source": EXACT_IDENTIFIER_SOURCE,
        "exact_print_external_id_format": "<scryfall_id>:<finish>",
        "exact_identifier_rows": target_rows,
        "unique_source_external_pairs": len(seen_source_external),
        "unique_print_source_pairs": len(seen_print_source),
        "source_objects_shared_by_multiple_finishes": duplicate_source_objects,
    }
    gates = dict(manifest.get("gates") or {})
    gates.update(
        {
            "duplicate_exact_print_identifiers": 0,
            "duplicate_identifier_source_external_pairs": 0,
            "duplicate_identifier_print_source_pairs": 0,
            "canonical_v22_files_changed_other_than_identifiers": 0,
        }
    )
    manifest["gates"] = gates
    manifest["status"] = "pass"

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "status": "pass",
        "source_schema": SOURCE_SCHEMA,
        "target_schema": TARGET_SCHEMA,
        "exact_prints": expected_prints,
        "source_identifier_rows": source_rows,
        "target_identifier_rows": target_rows,
        "unique_source_external_pairs": len(seen_source_external),
        "unique_print_source_pairs": len(seen_print_source),
        "source_objects_shared_by_multiple_finishes": duplicate_source_objects,
        "canonical_files_byte_identical": sorted(unchanged_files),
        "changed_file": "print_identifiers.jsonl",
        "database_writes": 0,
    }
    (output_dir / "certification-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive MTG V2.3 exact PrintIdentifiers from frozen certified V2.2")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(source_dir=args.source_dir, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
