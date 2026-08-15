from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg2

from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification

# This runner is intentionally separate so the certification clone can support
# both serial-id tables and canonical attribute tables keyed directly by FK.
# Production contract audit 31883824617 also proved MTG deliberately has zero
# auxiliary `print_identifiers` for Scryfall: exact identity lives on
# prints.scryfall_id + prints.variant (migration 20260808_25).
#
# The certification source is a single sealed Scryfall all_cards capture. We
# persist only the ES/JA paper objects from that capture because those are the
# only objects this gate is allowed to write. Source-wide counters are still
# measured while streaming all_cards, so filtering cannot hide source drift.

_ORIGINAL_TABLE_EXISTS = certification._table_exists
_ORIGINAL_VALIDATE_FINAL = certification.validate_final
_ORIGINAL_APPLY_PASS = certification.apply_pass
_ORIGINAL_SEED_EPHEMERAL = certification.seed_ephemeral

# 1k batches generated hundreds of indexed round-trips on pass 2. 5k keeps the
# exact same write/validation semantics while making the certification bounded.
CERTIFICATION_BATCH_SIZE = 5000


def _log(message: str) -> None:
    print(f"[mtg-multilingual-cert] {message}", flush=True)


def _table_exists(cur, table: str) -> bool:
    # Do not create legacy auxiliary Scryfall identifiers for MTG. The table is
    # shared with other TCGs and its historical (source, external_id) uniqueness
    # cannot represent one Scryfall object exposing several physical finishes.
    if table == "print_identifiers":
        return False
    return _ORIGINAL_TABLE_EXISTS(cur, table)


def _copy_filtered(src, dst, table: str, where_sql: str = "TRUE", params: tuple = ()) -> dict[str, Any]:
    """COPY one filtered table while supporting child tables keyed by FK, not id."""
    if not certification._table_exists(src, table) or not certification._table_exists(dst, table):
        return {"table": table, "rows": 0, "skipped": True}
    source_columns = certification._columns(src, table)
    target_columns = set(certification._columns(dst, table))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        raise RuntimeError(f"No common columns for {table}")
    quoted = ",".join(f'"{column}"' for column in columns)
    where = src.mogrify(where_sql, params).decode("utf-8") if params else where_sql
    src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}')
    count = int(src.fetchone()[0])
    order_column = "id" if "id" in columns else columns[0]
    fd, tmp_name = tempfile.mkstemp(prefix=f"dontripit-{table}-", suffix=".csv")
    os.close(fd)
    path = Path(tmp_name)
    started = time.monotonic()
    try:
        with path.open("w", encoding="utf-8", newline="") as out:
            src.copy_expert(
                f'COPY (SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY "{order_column}") TO STDOUT WITH (FORMAT CSV)',
                out,
            )
        digest = certification._sha256(path)
        if count:
            with path.open("r", encoding="utf-8", newline="") as inp:
                dst.copy_expert(f'COPY "{table}" ({quoted}) FROM STDIN WITH (FORMAT CSV)', inp)
        _log(f"seed table={table} rows={count} elapsed={time.monotonic() - started:.1f}s")
        return {
            "table": table,
            "rows": count,
            "sha256": digest,
            "columns": columns,
            "order_column": order_column,
        }
    finally:
        path.unlink(missing_ok=True)


def _reset_sequence(cur, table: str) -> None:
    """Reset a serial id sequence only when the cloned table actually owns id."""
    if "id" not in certification._columns(cur, table):
        return
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    row = cur.fetchone()
    sequence = row[0] if row else None
    if not sequence:
        return
    cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
    maximum = int(cur.fetchone()[0] or 0)
    if maximum:
        cur.execute("SELECT setval(%s, %s, true)", (sequence, maximum))


def _seed_ephemeral(source_url: str, target_url: str) -> dict[str, Any]:
    started = time.monotonic()
    _log("stage=seed_ephemeral start")
    result = _ORIGINAL_SEED_EPHEMERAL(source_url, target_url)
    _log(f"stage=seed_ephemeral done elapsed={time.monotonic() - started:.1f}s")
    return result


def _capture_snapshot(path: Path, meta_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    _log("stage=capture_snapshot start source=single-scryfall-all_cards scope=paper-es-ja")
    connector = certification.ScryfallMtgV2Connector()
    metadata = certification._all_cards_metadata(connector)
    url = connector._bulk_download_url(metadata)
    if not url:
        raise RuntimeError("Scryfall all_cards download URL missing")

    counts = Counter()
    stored_objects = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for card in certification._iter_all_cards(connector, url):
            counts["all_objects"] += 1
            if not certification._is_paper(card):
                continue
            counts["paper_objects"] += 1
            lang = certification.clean(card.get("lang")).lower()
            if lang not in certification.LANGUAGES:
                continue
            counts[f"paper_{lang}_objects"] += 1
            counts[f"paper_{lang}_prints"] += len(certification.finish_values(card))
            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stored_objects += 1

    report = {
        "bulk_type": certification.clean(metadata.get("type")) or "all_cards",
        "bulk_updated_at": metadata.get("updated_at"),
        "snapshot_sha256": certification._sha256(path),
        "normalized_jsonl": True,
        "sealed_source": "single-scryfall-all_cards-capture",
        "sealed_scope": "paper-es-ja",
        "stored_objects": stored_objects,
        "counts": dict(counts),
    }
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    _log(
        "stage=capture_snapshot done "
        f"stored_objects={stored_objects} es_prints={counts['paper_es_prints']} "
        f"ja_prints={counts['paper_ja_prints']} elapsed={time.monotonic() - started:.1f}s"
    )
    return report


def _apply_pass(target_url: str, snapshot: Path, source_version: str | None) -> dict[str, Any]:
    started = time.monotonic()
    _log(f"stage=apply_pass start batch_size={certification.BATCH_SIZE}")
    result = _ORIGINAL_APPLY_PASS(target_url, snapshot, source_version)
    _log(
        "stage=apply_pass done "
        f"created_es={result.get('prints_created_es', 0)} "
        f"created_ja={result.get('prints_created_ja', 0)} "
        f"elapsed={time.monotonic() - started:.1f}s"
    )
    return result


def _validate_final(target_url: str, snapshot: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    _log("stage=validate_final start")
    result = _ORIGINAL_VALIDATE_FINAL(target_url, snapshot, baseline)
    conn = psycopg2.connect(target_url, connect_timeout=30, application_name="dontripit_mtg_multilingual_identity_gate")
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            game_id, _ = certification._find_game(cur)
            cur.execute(
                """
                SELECT count(*) FROM prints p JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                  AND p.scryfall_id IS NULL
                """,
                (game_id,),
            )
            missing_scryfall_ids = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT p.scryfall_id,p.variant,count(*)
                  FROM prints p JOIN cards c ON c.id=p.card_id
                  WHERE c.game_id=%s AND lower(coalesce(p.language,'')) IN ('es','ja')
                    AND p.scryfall_id IS NOT NULL
                  GROUP BY p.scryfall_id,p.variant HAVING count(*)>1
                ) d
                """,
                (game_id,),
            )
            duplicate_scryfall_finish = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT count(*) FROM print_identifiers pi
                JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=%s AND pi.source='scryfall'
                """,
                (game_id,),
            )
            auxiliary_scryfall_identifiers = int(cur.fetchone()[0])
            if missing_scryfall_ids:
                raise AssertionError(f"MTG ES/JA Prints missing scryfall_id: {missing_scryfall_ids}")
            if duplicate_scryfall_finish:
                raise AssertionError(f"Duplicate exact Scryfall object+finish identities: {duplicate_scryfall_finish}")
            if auxiliary_scryfall_identifiers:
                raise AssertionError(
                    "MTG certification unexpectedly populated legacy print_identifiers: "
                    f"{auxiliary_scryfall_identifiers}"
                )
            result.update(
                {
                    "missing_scryfall_ids": 0,
                    "duplicate_scryfall_finish_identities": 0,
                    "auxiliary_scryfall_print_identifiers": 0,
                    "scryfall_identity_contract": "prints.scryfall_id+prints.variant",
                }
            )
            conn.rollback()
            _log(f"stage=validate_final done elapsed={time.monotonic() - started:.1f}s")
            return result
    finally:
        conn.close()


def main() -> int:
    certification.BATCH_SIZE = CERTIFICATION_BATCH_SIZE
    certification._table_exists = _table_exists
    certification._copy_filtered = _copy_filtered
    certification._reset_sequence = _reset_sequence
    certification.seed_ephemeral = _seed_ephemeral
    certification.capture_snapshot = _capture_snapshot
    certification.apply_pass = _apply_pass
    certification.validate_final = _validate_final
    _log(f"runner start certification_batch_size={CERTIFICATION_BATCH_SIZE}")
    return certification.main()


if __name__ == "__main__":
    raise SystemExit(main())
