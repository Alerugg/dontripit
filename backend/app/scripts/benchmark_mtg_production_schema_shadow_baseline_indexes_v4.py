from __future__ import annotations

# Compare the exact same MTG Model D corpus/DDL against the current shared
# source-specific index strategy. This isolates how much storage is actually
# saved by the cross-TCG partial-index optimization before we decide whether that
# optimization belongs in the MTG migration or in a separate later revision.
from app.scripts import benchmark_mtg_production_schema_shadow_v4 as corrected


v3 = corrected.v3
_base_candidate_ddl = v3._candidate_ddl


def _candidate_ddl_with_current_shared_indexes(cur) -> None:
    _base_candidate_ddl(cur)

    # Restore current shared uniqueness semantics from revision 25/models.
    restore_unique = [
        ("sets", "uq_sets_game_tcgdex_partial", "uq_sets_game_tcgdex", "game_id, tcgdex_id"),
        ("sets", "uq_sets_game_yugioh_partial", "uq_sets_game_yugioh", "game_id, yugioh_id"),
        ("sets", "uq_sets_game_riftbound_partial", "uq_sets_game_riftbound", "game_id, riftbound_id"),
        ("cards", "uq_cards_game_tcgdex_partial", "uq_cards_game_tcgdex", "game_id, tcgdex_id"),
        ("cards", "uq_cards_game_yugoprodeck_partial", "uq_cards_game_yugoprodeck", "game_id, yugoprodeck_id"),
        ("cards", "uq_cards_game_riftbound_partial", "uq_cards_game_riftbound", "game_id, riftbound_id"),
        ("prints", "uq_prints_tcgdex_id_partial", "uq_prints_tcgdex_id", "tcgdex_id"),
        ("prints", "uq_prints_yugioh_id_partial", "uq_prints_yugioh_id", "yugioh_id"),
        ("prints", "uq_prints_riftbound_id_partial", "uq_prints_riftbound_id", "riftbound_id"),
        ("prints", "uq_prints_print_key_partial", "uq_prints_print_key", "print_key"),
    ]
    for table, partial_index, constraint, columns in restore_unique:
        cur.execute(f"DROP INDEX IF EXISTS {partial_index}")
        cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} UNIQUE ({columns})")

    restore_lookup = [
        ("sets", "ix_sets_tcgdex_id_partial", "ix_sets_tcgdex_id", "tcgdex_id"),
        ("sets", "ix_sets_yugioh_id_partial", "ix_sets_yugioh_id", "yugioh_id"),
        ("sets", "ix_sets_riftbound_id_partial", "ix_sets_riftbound_id", "riftbound_id"),
        ("cards", "ix_cards_tcgdex_id_partial", "ix_cards_tcgdex_id", "tcgdex_id"),
        ("cards", "ix_cards_yugoprodeck_id_partial", "ix_cards_yugoprodeck_id", "yugoprodeck_id"),
        ("cards", "ix_cards_riftbound_id_partial", "ix_cards_riftbound_id", "riftbound_id"),
        ("prints", "ix_prints_tcgdex_id_partial", "ix_prints_tcgdex_id", "tcgdex_id"),
        ("prints", "ix_prints_yugioh_id_partial", "ix_prints_yugioh_id", "yugioh_id"),
        ("prints", "ix_prints_riftbound_id_partial", "ix_prints_riftbound_id", "riftbound_id"),
    ]
    for table, partial_index, broad_index, column in restore_lookup:
        cur.execute(f"DROP INDEX IF EXISTS {partial_index}")
        cur.execute(f"CREATE INDEX {broad_index} ON {table}({column})")
    cur.execute("CREATE INDEX ix_prints_print_key ON prints(print_key)")


v3._candidate_ddl = _candidate_ddl_with_current_shared_indexes


def main() -> int:
    return v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
