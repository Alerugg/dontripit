from __future__ import annotations

import json

from sqlalchemy import text

from app import db


def main() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
        report = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game) AS card_attributes,
              (SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS print_attributes,
              (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game AND lower(COALESCE(p.rarity,''))='unknown') AS unknown_rarity,
              (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game) AS total_prints
            """
        ), {"game": game_id}).mappings().one())
    report = {key: int(value) for key, value in report.items()}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
