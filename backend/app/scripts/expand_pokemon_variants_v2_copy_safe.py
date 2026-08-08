from __future__ import annotations

import csv
import io

from app.scripts import expand_pokemon_variants_v2 as implementation


def _copy_safe_stage_buffer(rows: list[tuple]) -> io.StringIO:
    """Quote every CSV field so an empty string stays an empty string in PostgreSQL.

    PostgreSQL COPY CSV interprets an unquoted empty field as NULL. The physical
    variant stage intentionally uses an empty string for missing shared artwork,
    so QUOTE_ALL preserves that value without weakening the staging schema.
    """
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerows(rows)
    buffer.seek(0)
    return buffer


implementation._stage_buffer = _copy_safe_stage_buffer


if __name__ == "__main__":
    raise SystemExit(implementation.main())
