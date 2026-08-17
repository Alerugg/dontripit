from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

from app.jobs.regional_content import CANONICAL_SOURCE_KEYS


DUPLICATE_QUERY = text(
    """
    SELECT g.slug AS game, c.region,
           lower(regexp_replace(trim(c.title), '\\s+', ' ', 'g')) AS normalized_title,
           c.id, c.item_url, c.kind, c.release_date, c.published_date, c.last_seen_at
    FROM regional_tcg_content c
    JOIN games g ON g.id = c.game_id
    WHERE c.source_key = ANY(:keys)
      AND c.last_seen_at >= :cutoff
      AND trim(coalesce(c.title, '')) <> ''
      AND EXISTS (
        SELECT 1
        FROM regional_tcg_content other
        WHERE other.game_id = c.game_id
          AND other.region = c.region
          AND other.source_key = ANY(:keys)
          AND other.last_seen_at >= :cutoff
          AND lower(regexp_replace(trim(other.title), '\\s+', ' ', 'g')) =
              lower(regexp_replace(trim(c.title), '\\s+', ' ', 'g'))
          AND other.id <> c.id
      )
    ORDER BY game, c.region, normalized_title, c.id
    """
)


def _group(rows):
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        item = dict(row)
        key = (str(item["game"]), str(item["region"]), str(item["normalized_title"]))
        groups.setdefault(key, []).append(item)
    return {key: value for key, value in groups.items() if len(value) > 1}


def _winner(rows: list[dict]) -> dict:
    def score(row: dict):
        return (
            row["release_date"] is not None,
            row["published_date"] is not None,
            row["release_date"] or row["published_date"],
            row["kind"] == "release",
            row["kind"] == "product",
            -len(str(row["item_url"] or "")),
            str(row["item_url"] or ""),
        )

    return max(rows, key=score)


def dedupe(*, window_hours: int, apply: bool) -> dict[str, object]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    engine = create_engine(database_url)
    deleted = 0
    decisions: list[dict[str, object]] = []

    with engine.begin() as conn:
        groups = _group(
            conn.execute(
                DUPLICATE_QUERY,
                {"keys": list(CANONICAL_SOURCE_KEYS), "cutoff": cutoff},
            ).mappings().all()
        )
        for (game, region, title), rows in sorted(groups.items()):
            keep = _winner(rows)
            losers = [row for row in rows if int(row["id"]) != int(keep["id"])]
            aliases = sorted(str(row["item_url"]) for row in losers)
            decision = {
                "game": game,
                "region": region,
                "title": title,
                "keep_id": int(keep["id"]),
                "keep_url": str(keep["item_url"]),
                "remove_ids": [int(row["id"]) for row in losers],
                "remove_urls": aliases,
            }
            decisions.append(decision)
            if apply and losers:
                conn.execute(
                    text(
                        """
                        UPDATE regional_tcg_content
                        SET raw_json = coalesce(raw_json, '{}'::jsonb)
                            || jsonb_build_object('deduplicated_alias_urls', CAST(:aliases AS jsonb))
                        WHERE id = :winner_id
                        """
                    ),
                    {"winner_id": int(keep["id"]), "aliases": json.dumps(aliases)},
                )
                conn.execute(
                    text("DELETE FROM regional_tcg_content WHERE id = ANY(:ids)"),
                    {"ids": [int(row["id"]) for row in losers]},
                )
                deleted += len(losers)

        remaining = _group(
            conn.execute(
                DUPLICATE_QUERY,
                {"keys": list(CANONICAL_SOURCE_KEYS), "cutoff": cutoff},
            ).mappings().all()
        )
        if apply and remaining:
            raise RuntimeError(f"Duplicate regional titles remain after cleanup: {sorted(remaining)}")

    return {
        "gate": "PASS" if (apply and not remaining) or (not apply) else "FAIL",
        "window_hours": window_hours,
        "apply": apply,
        "duplicate_groups_before": len(decisions),
        "deleted_rows": deleted,
        "duplicate_groups_after": len(remaining),
        "decisions": decisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate current canonical regional TCG titles.")
    parser.add_argument("--window-hours", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = dedupe(window_hours=args.window_hours, apply=args.apply)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    print("REGIONAL_DEDUPE " + json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    if report["gate"] != "PASS":
        raise SystemExit(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
