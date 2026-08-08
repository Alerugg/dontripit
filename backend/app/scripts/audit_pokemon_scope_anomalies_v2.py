from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(*, report_path: Path | None = None) -> dict:
    """Explain Pokémon rows that exist outside the certified V2 projection.

    This audit is intentionally read-only. It does not infer that a row should
    be deleted merely because it is absent from enrichment/Search V2.
    """

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = session.execute(text("SELECT id FROM games WHERE slug='pokemon'" )).scalar_one()

        cards = [dict(row) for row in session.execute(text(
            """
            SELECT
              c.id,
              c.name,
              c.card_key,
              c.tcgdex_id,
              c.created_at,
              (ca.card_id IS NOT NULL) AS has_card_attributes,
              (csp.card_id IS NOT NULL) AS has_search_profile,
              COUNT(p.id)::int AS print_count
            FROM cards c
            LEFT JOIN card_attributes ca ON ca.card_id=c.id
            LEFT JOIN card_search_profiles csp ON csp.card_id=c.id
            LEFT JOIN prints p ON p.card_id=c.id
            WHERE c.game_id=:game_id
              AND (ca.card_id IS NULL OR csp.card_id IS NULL)
            GROUP BY c.id, c.name, c.card_key, c.tcgdex_id, c.created_at,
                     ca.card_id, csp.card_id
            ORDER BY c.id
            """
        ), {"game_id": game_id}).mappings().all()]

        prints = [dict(row) for row in session.execute(text(
            """
            SELECT
              p.id,
              p.card_id,
              c.name AS card_name,
              c.tcgdex_id AS card_tcgdex_id,
              p.tcgdex_id AS print_tcgdex_id,
              p.print_key,
              s.code AS set_code,
              s.name AS set_name,
              p.collector_number,
              p.language,
              p.rarity,
              p.variant,
              p.is_foil,
              p.created_at,
              (pa.print_id IS NOT NULL) AS has_print_attributes,
              (psp.print_id IS NOT NULL) AS has_search_profile,
              (pi.id IS NOT NULL) AS has_image
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            JOIN sets s ON s.id=p.set_id
            LEFT JOIN print_attributes pa ON pa.print_id=p.id
            LEFT JOIN print_search_profiles psp ON psp.print_id=p.id
            LEFT JOIN LATERAL (
              SELECT id FROM print_images x WHERE x.print_id=p.id LIMIT 1
            ) pi ON TRUE
            WHERE c.game_id=:game_id
              AND (pa.print_id IS NULL OR psp.print_id IS NULL)
            ORDER BY p.id
            """
        ), {"game_id": game_id}).mappings().all()]

        totals = dict(session.execute(text(
            """
            SELECT
              (SELECT COUNT(*) FROM cards c WHERE c.game_id=:game_id) AS cards,
              (SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS prints,
              (SELECT COUNT(*) FROM card_attributes ca JOIN cards c ON c.id=ca.card_id WHERE c.game_id=:game_id) AS card_attributes,
              (SELECT COUNT(*) FROM print_attributes pa JOIN prints p ON p.id=pa.print_id JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game_id) AS print_attributes,
              (SELECT COUNT(*) FROM card_search_profiles csp WHERE csp.game_id=:game_id) AS card_search_profiles,
              (SELECT COUNT(*) FROM print_search_profiles psp WHERE psp.game_id=:game_id) AS print_search_profiles
            """
        ), {"game_id": game_id}).mappings().one())
        session.rollback()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_pokemon_scope_anomaly_audit_v2",
        "status": "pass",
        "totals": {key: int(value or 0) for key, value in totals.items()},
        "cards_outside_certified_projection": cards,
        "prints_outside_certified_projection": prints,
        "counts": {
            "cards_outside_certified_projection": len(cards),
            "prints_outside_certified_projection": len(prints),
        },
        "database_writes": 0,
        "interpretation": "Rows are reported for evidence only; absence from enrichment/Search V2 is not itself deletion authority.",
    }
    _write(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args()
    run(report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
