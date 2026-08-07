from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app import db


def _safe_count(session, table: str, where: str = "1=1", params: dict | None = None) -> int | None:
    try:
        return int(session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params or {}).scalar_one())
    except Exception:
        return None


def run() -> dict:
    db.init_engine()
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    relevant_tables = [name for name in sorted(tables) if any(token in name.lower() for token in ("game", "set", "card", "print", "identifier", "source"))]

    schema = {}
    for table in relevant_tables:
        columns = inspector.get_columns(table)
        schema[table] = [
            {
                "name": column.get("name"),
                "type": str(column.get("type")),
                "nullable": bool(column.get("nullable")),
            }
            for column in columns
            if any(
                token in str(column.get("name") or "").lower()
                for token in ("id", "key", "slug", "code", "collector", "language", "source", "tcgdex", "external", "image")
            )
        ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": "neon",
        "mode": "read_only",
        "relevant_schema": schema,
        "pokemon": {},
    }

    with db.SessionLocal() as session:
        game_row = None
        if "games" in tables:
            game_row = session.execute(
                text("SELECT id, slug, name FROM games WHERE lower(slug) IN ('pokemon','pokémon') ORDER BY id LIMIT 1")
            ).mappings().first()

        if not game_row:
            report["pokemon"] = {"game_found": False}
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return report

        game_id = int(game_row["id"])
        pokemon = {
            "game_found": True,
            "game": dict(game_row),
            "sets": None,
            "cards": None,
            "prints": None,
            "print_images": None,
            "identifier_rows": None,
            "tcgdex_columns": {},
            "tcgdex_non_null": {},
        }

        if "sets" in tables:
            set_cols = {column["name"] for column in inspector.get_columns("sets")}
            if "game_id" in set_cols:
                pokemon["sets"] = _safe_count(session, "sets", "game_id = :game", {"game": game_id})

        if "cards" in tables:
            card_cols = {column["name"] for column in inspector.get_columns("cards")}
            if "game_id" in card_cols:
                pokemon["cards"] = _safe_count(session, "cards", "game_id = :game", {"game": game_id})

        if "prints" in tables:
            print_cols = {column["name"] for column in inspector.get_columns("prints")}
            if "game_id" in print_cols:
                pokemon["prints"] = _safe_count(session, "prints", "game_id = :game", {"game": game_id})
            elif "card_id" in print_cols and "cards" in tables:
                pokemon["prints"] = int(session.execute(text(
                    "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id = p.card_id WHERE c.game_id = :game"
                ), {"game": game_id}).scalar_one())

            tcgdex_cols = sorted([name for name in print_cols if "tcgdex" in name.lower()])
            pokemon["tcgdex_columns"]["prints"] = tcgdex_cols
            for column in tcgdex_cols:
                try:
                    pokemon["tcgdex_non_null"][f"prints.{column}"] = int(session.execute(text(
                        f"SELECT COUNT(*) FROM prints p JOIN cards c ON c.id = p.card_id WHERE c.game_id = :game AND p.{column} IS NOT NULL AND CAST(p.{column} AS text) <> ''"
                    ), {"game": game_id}).scalar_one())
                except Exception:
                    pass

        for table in sorted(tables):
            columns = {column["name"] for column in inspector.get_columns(table)}
            tcgdex_cols = sorted([name for name in columns if "tcgdex" in name.lower()])
            if tcgdex_cols:
                pokemon["tcgdex_columns"][table] = tcgdex_cols

        identifier_table = next((name for name in ("print_identifiers", "print_identifier", "identifiers") if name in tables), None)
        if identifier_table:
            identifier_cols = {column["name"] for column in inspector.get_columns(identifier_table)}
            pokemon["identifier_table"] = identifier_table
            pokemon["identifier_columns"] = sorted(identifier_cols)
            if "print_id" in identifier_cols and "prints" in tables and "cards" in tables:
                pokemon["identifier_rows"] = int(session.execute(text(
                    f"SELECT COUNT(*) FROM {identifier_table} i JOIN prints p ON p.id = i.print_id JOIN cards c ON c.id = p.card_id WHERE c.game_id = :game"
                ), {"game": game_id}).scalar_one())
                source_like = [name for name in identifier_cols if any(token in name.lower() for token in ("source", "provider", "namespace", "type"))]
                value_like = [name for name in identifier_cols if any(token in name.lower() for token in ("value", "external", "identifier", "id")) and name not in {"id", "print_id"}]
                pokemon["identifier_source_columns"] = source_like
                pokemon["identifier_value_columns"] = value_like

                # Safe aggregate sampling of likely TCGdex namespaces only; never emit secrets.
                for source_col in source_like[:2]:
                    try:
                        rows = session.execute(text(
                            f"SELECT CAST(i.{source_col} AS text) AS source, COUNT(*) AS count "
                            f"FROM {identifier_table} i JOIN prints p ON p.id=i.print_id JOIN cards c ON c.id=p.card_id "
                            f"WHERE c.game_id=:game GROUP BY CAST(i.{source_col} AS text) ORDER BY count DESC LIMIT 20"
                        ), {"game": game_id}).mappings().all()
                        pokemon[f"identifier_{source_col}_distribution"] = [dict(row) for row in rows]
                    except Exception:
                        pass

        if "print_images" in tables and "prints" in tables and "cards" in tables:
            pokemon["print_images"] = int(session.execute(text(
                "SELECT COUNT(*) FROM print_images pi JOIN prints p ON p.id=pi.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game"
            ), {"game": game_id}).scalar_one())

        report["pokemon"] = pokemon

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
