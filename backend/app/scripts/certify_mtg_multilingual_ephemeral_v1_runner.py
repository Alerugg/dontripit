from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from app.scripts import certify_mtg_multilingual_ephemeral_v1 as certification

# This runner is intentionally separate so the certification clone can support
# both serial-id tables and canonical attribute tables keyed directly by FK.


def _copy_filtered(src, dst, table: str, where_sql: str = "TRUE", params: tuple = ()) -> dict[str, Any]:
    """COPY one filtered table while supporting child tables keyed by FK, not id.

    Some canonical attribute tables intentionally use ``card_id`` or ``print_id``
    as their primary key and therefore have no ``id`` column. The certification
    clone must preserve those rows too; ordering is only for deterministic
    evidence and may use the first physical column when ``id`` is absent.
    """
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
        return {
            "table": table,
            "rows": count,
            "sha256": digest,
            "columns": columns,
            "order_column": order_column,
        }
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    certification._copy_filtered = _copy_filtered
    return certification.main()


if __name__ == "__main__":
    raise SystemExit(main())
