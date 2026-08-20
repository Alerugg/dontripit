from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from app import db
from app.search_v2.pokemon_query import normal_pokemon_search
from app.search_v2.query import normal_search
from app.search_v2.normalization import normalize_search_text

CASES = (
    ("pokemon", "Pikachu"),
    ("onepiece", "Luffy"),
)


def _canonical_counts(session, game: str, query: str) -> dict:
    q = normalize_search_text(query)
    row = session.execute(
        text(
            """
            WITH matching_cards AS (
              SELECT DISTINCT c.id
              FROM cards c
              JOIN games g ON g.id=c.game_id
              WHERE g.slug=:game
                AND lower(c.name) LIKE '%' || :q || '%'
            )
            SELECT
              (SELECT count(*) FROM matching_cards) AS logical_cards,
              (SELECT count(*) FROM prints p JOIN matching_cards mc ON mc.id=p.card_id) AS physical_prints,
              (SELECT count(*) FROM print_search_profiles psp JOIN matching_cards mc ON mc.id=psp.card_id) AS search_profiles
            """
        ),
        {"game": game, "q": q},
    ).mappings().one()
    names = session.execute(
        text(
            """
            SELECT c.id,c.name,count(p.id) AS print_count
            FROM cards c
            JOIN games g ON g.id=c.game_id
            LEFT JOIN prints p ON p.card_id=c.id
            WHERE g.slug=:game AND lower(c.name) LIKE '%' || :q || '%'
            GROUP BY c.id,c.name
            ORDER BY lower(c.name),c.id
            """
        ),
        {"game": game, "q": q},
    ).mappings().all()
    return {
        "logical_cards": int(row["logical_cards"] or 0),
        "physical_prints": int(row["physical_prints"] or 0),
        "search_profiles": int(row["search_profiles"] or 0),
        "cards": [
            {"card_id": int(r["id"]), "name": str(r["name"]), "print_count": int(r["print_count"] or 0)}
            for r in names
        ],
    }


def _normal_search_counts(session, game: str, query: str) -> dict:
    fn = normal_pokemon_search if game == "pokemon" else None
    out = {}
    for limit in (24, 50, 100):
        if fn:
            items = fn(session, query=query, limit=limit)
        else:
            items = normal_search(session, query=query, game_slug=game, limit=limit)
        out[str(limit)] = {
            "count": len(items),
            "card_ids": [int(item["card_id"]) for item in items],
            "names": [str(item["name"]) for item in items],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="READ ONLY audit exhaustive character-name search surfaces")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    report = {"status": "pass", "production_writes": 0, "cases": {}}
    with db.SessionLocal() as session:
        for game, query in CASES:
            canonical = _canonical_counts(session, game, query)
            normal = _normal_search_counts(session, game, query)
            returned_100 = set(normal["100"]["card_ids"])
            canonical_ids = {row["card_id"] for row in canonical["cards"]}
            report["cases"][f"{game}:{query.lower()}"] = {
                "game": game,
                "query": query,
                "canonical_name_substring": canonical,
                "normal_search": normal,
                "canonical_ids_missing_from_top100": sorted(canonical_ids - returned_100),
                "top100_ids_not_name_substring": sorted(returned_100 - canonical_ids),
                "default_24_truncates_name_substring_surface": canonical["logical_cards"] > normal["24"]["count"],
                "public_50_truncates_name_substring_surface": canonical["logical_cards"] > normal["50"]["count"],
                "requires_beyond_100_for_name_substring_surface": canonical["logical_cards"] > 100,
            }
        session.rollback()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
