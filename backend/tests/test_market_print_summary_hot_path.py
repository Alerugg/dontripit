from __future__ import annotations

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects import postgresql

from app.routes.market_print_summary import (
    _MARKET_PRINT_SUMMARY_SQL,
    _MAX_PRINT_IDS,
    _parse_ids,
)


def test_market_summary_hot_path_starts_from_requested_prints_and_uses_capture_seeks():
    sql = " ".join(_MARKET_PRINT_SUMMARY_SQL.lower().split())

    # The former production query globally aggregated every historical
    # Cardmarket price snapshot on every search request. Keep the hot path
    # bounded by the <=100 requested Prints and their games.
    assert "with linked as materialized" in sql
    assert "l.print_id in :print_ids" in sql
    assert "requested_games as materialized" in sql
    assert "price_games as materialized" in sql

    # Current-capture discovery must remain an index-seek shape, not regress to
    # MAX()/GROUP BY over the complete historical tables.
    assert "order by e2.last_seen_at desc limit 1" in sql
    assert "order by mp.as_of desc limit 1" in sql
    assert "max(mp.as_of)" not in sql
    assert "group by e.game_id" not in sql

    # Identity/currentness contract remains explicit.
    assert "having count(distinct l.external_product_id) = 1" in sql
    assert "coalesce(ps.raw_json ->> 'idproduct', '') = a.id_product" in sql
    assert "lgc.as_of = ps.as_of" in sql


def test_market_summary_sql_compiles_with_expanding_print_ids_for_postgres():
    statement = text(_MARKET_PRINT_SUMMARY_SQL).bindparams(bindparam("print_ids", expanding=True))
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "__[POSTCOMPILE_print_ids]" in compiled


def test_parse_ids_deduplicates_and_preserves_order():
    assert _parse_ids("571, 45462,571,675") == [571, 45462, 675]


def test_parse_ids_rejects_invalid_and_over_limit_values():
    with pytest.raises(ValueError, match="positive"):
        _parse_ids("0")
    with pytest.raises(ValueError, match="integer"):
        _parse_ids("571,nope")

    too_many = ",".join(str(value) for value in range(1, _MAX_PRINT_IDS + 2))
    with pytest.raises(ValueError, match="maximum"):
        _parse_ids(too_many)
