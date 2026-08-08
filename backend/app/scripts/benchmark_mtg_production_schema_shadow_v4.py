from __future__ import annotations

# V3's only demonstrated failure was CSV serialisation of the PostgreSQL NULL
# marker: the backslash in \N was escaped by csv.writer and arrived as literal
# text. Keep the entire proven V3 corpus/DDL benchmark unchanged and replace
# only the sentinel with a plain token that CSV does not escape or quote.
from app.scripts import benchmark_mtg_production_schema_shadow_v3 as v3


v3.NULL = "__TCG_CATALOG_NULL_8B86__"


def main() -> int:
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
