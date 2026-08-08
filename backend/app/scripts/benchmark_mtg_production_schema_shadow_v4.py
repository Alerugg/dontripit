from __future__ import annotations

# V3's demonstrated failures were confined to COPY serialization, not MTG
# identity or candidate DDL. Keep the complete V3 corpus + schema benchmark and
# patch only the transport format here so every subsequent run remains directly
# comparable.
from pathlib import Path

from app.scripts import benchmark_mtg_production_schema_shadow_v3 as v3


v3.NULL = "__TCG_CATALOG_NULL_8B86__"


class _TextRowWriter:
    """Minimal PostgreSQL COPY TEXT row writer.

    COPY TEXT has one escaping layer instead of nesting CSV escaping around JSON
    escaping. That makes it safe for Oracle text containing quotes/backslashes.
    """

    def __init__(self, handle):
        self.handle = handle

    @staticmethod
    def _encode(value) -> str:
        if value is None or value == v3.NULL:
            return r"\N"
        text = str(value)
        return (
            text.replace("\\", "\\\\")
            .replace("\t", r"\t")
            .replace("\n", r"\n")
            .replace("\r", r"\r")
        )

    def writerow(self, row) -> None:
        self.handle.write("\t".join(self._encode(value) for value in row) + "\n")


def _writer(stack, root: Path, name: str):
    handle = stack.enter_context((root / name).open("w+", encoding="utf-8", newline=""))
    return handle, _TextRowWriter(handle)


def _copy(cur, table: str, columns: list[str], handle) -> None:
    handle.flush()
    handle.seek(0)
    columns_sql = ", ".join(f'"{col}"' for col in columns)
    cur.copy_expert(
        f'COPY public."{table}" ({columns_sql}) FROM STDIN '
        "WITH (FORMAT TEXT, DELIMITER E'\\t', NULL '\\N')",
        handle,
    )


v3._writer = _writer
v3._copy = _copy


def main() -> int:
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
