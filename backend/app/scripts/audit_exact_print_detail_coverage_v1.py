from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app import db


def _details(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def run_audit() -> dict:
    db.init_engine()
    with db.SessionLocal() as session:
        physical_rows = session.execute(
            text(
                """
                SELECT g.slug AS game,
                       lower(COALESCE(p.language, '')) AS language,
                       COUNT(*) AS total_prints,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1
                           FROM print_images pi
                           WHERE pi.print_id = p.id
                             AND trim(COALESCE(pi.url, '')) <> ''
                       ) THEN 1 ELSE 0 END) AS exact_image_prints,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1
                           FROM print_localizations pl
                           WHERE pl.print_id = p.id
                             AND lower(pl.language) = lower(p.language)
                       ) THEN 1 ELSE 0 END) AS exact_localization_prints
                FROM prints p
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                GROUP BY g.slug, lower(COALESCE(p.language, ''))
                ORDER BY g.slug, lower(COALESCE(p.language, ''))
                """
            )
        ).mappings().all()

        localization_rows = session.execute(
            text(
                """
                SELECT g.slug AS game,
                       p.id AS print_id,
                       lower(COALESCE(p.language, '')) AS physical_language,
                       lower(COALESCE(pl.language, '')) AS localization_language,
                       pl.source,
                       pl.details_json
                FROM print_localizations pl
                JOIN prints p ON p.id = pl.print_id
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                ORDER BY g.slug, p.id, pl.language
                """
            )
        ).mappings().all()

        image_source_rows = session.execute(
            text(
                """
                SELECT g.slug AS game,
                       lower(COALESCE(p.language, '')) AS language,
                       COALESCE(pi.source, 'unknown') AS source,
                       COUNT(DISTINCT p.id) AS print_count
                FROM print_images pi
                JOIN prints p ON p.id = pi.print_id
                JOIN cards c ON c.id = p.card_id
                JOIN games g ON g.id = c.game_id
                WHERE trim(COALESCE(pi.url, '')) <> ''
                GROUP BY g.slug, lower(COALESCE(p.language, '')), COALESCE(pi.source, 'unknown')
                ORDER BY g.slug, lower(COALESCE(p.language, '')), COALESCE(pi.source, 'unknown')
                """
            )
        ).mappings().all()

    by_game_language = []
    total_prints = 0
    total_exact_images = 0
    total_exact_localizations = 0
    for row in physical_rows:
        total = int(row["total_prints"] or 0)
        images = int(row["exact_image_prints"] or 0)
        localizations = int(row["exact_localization_prints"] or 0)
        total_prints += total
        total_exact_images += images
        total_exact_localizations += localizations
        by_game_language.append(
            {
                "game": row["game"],
                "language": row["language"] or None,
                "total_prints": total,
                "exact_image_prints": images,
                "missing_exact_image_prints": total - images,
                "exact_image_coverage_percent": round(images / total * 100, 3) if total else 0.0,
                "exact_localization_prints": localizations,
                "missing_exact_localization_prints": total - localizations,
                "exact_localization_coverage_percent": round(localizations / total * 100, 3) if total else 0.0,
            }
        )

    localization_stats: defaultdict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for row in localization_rows:
        key = (
            str(row["game"]),
            str(row["localization_language"] or ""),
            str(row["source"] or "unknown"),
        )
        details = _details(row.get("details_json"))
        effect = str(details.get("effect") or "").strip()
        pendulum_effect = str(details.get("pendulum_effect") or "").strip()
        stats = localization_stats[key]
        stats["rows"] += 1
        if effect:
            stats["effect_rows"] += 1
        if pendulum_effect:
            stats["pendulum_effect_rows"] += 1
        if details.get("official") is True:
            stats["official_rows"] += 1
        if str(row["physical_language"] or "") == str(row["localization_language"] or ""):
            stats["exact_physical_language_rows"] += 1

    localization_coverage = [
        {
            "game": game,
            "language": language or None,
            "source": source,
            **dict(sorted(stats.items())),
        }
        for (game, language, source), stats in sorted(localization_stats.items())
    ]

    image_sources = [
        {
            "game": row["game"],
            "language": row["language"] or None,
            "source": row["source"],
            "print_count": int(row["print_count"] or 0),
        }
        for row in image_source_rows
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "totals": {
            "prints": total_prints,
            "exact_image_prints": total_exact_images,
            "missing_exact_image_prints": total_prints - total_exact_images,
            "exact_image_coverage_percent": round(total_exact_images / total_prints * 100, 3) if total_prints else 0.0,
            "exact_localization_prints": total_exact_localizations,
            "missing_exact_localization_prints": total_prints - total_exact_localizations,
            "exact_localization_coverage_percent": round(total_exact_localizations / total_prints * 100, 3) if total_prints else 0.0,
        },
        "by_game_language": by_game_language,
        "localization_coverage": localization_coverage,
        "image_sources": image_sources,
        "contract": {
            "exact_image": "Only print_images rows owned by the exact print count as image coverage.",
            "exact_localization": "Only a localization row whose language equals the physical print language counts as printed-text coverage.",
            "display_localization": "Browser-language display text may come from another physical print of the same canonical card and must retain provenance.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Exact Print Detail V2 image/text coverage without writes")
    parser.parse_args()
    payload = run_audit()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())