from __future__ import annotations

# V3's demonstrated failures were confined to COPY/CSV serialization, not MTG
# identity or candidate DDL. Keep the complete V3 corpus + schema benchmark and
# patch only those serialization mechanics here so every subsequent run remains
# directly comparable.
from app.scripts import benchmark_mtg_production_schema_shadow_v3 as v3


v3.NULL = "__TCG_CATALOG_NULL_8B86__"


def _copy(cur, table: str, columns: list[str], handle) -> None:
    handle.flush()
    handle.seek(0)
    columns_sql = ", ".join(f'"{col}"' for col in columns)
    # Python's csv.writer uses standard CSV double-quote escaping. PostgreSQL
    # must use the same quote character as ESCAPE; using backslash here strips
    # JSON object quotes and turns valid JSON into invalid {key:value} text.
    cur.copy_expert(
        f'COPY public."{table}" ({columns_sql}) FROM STDIN '
        f"WITH (FORMAT CSV, DELIMITER E'\\t', QUOTE '\"', ESCAPE '\"', NULL '{v3.NULL}')",
        handle,
    )


v3._copy = _copy


def main() -> int:
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
