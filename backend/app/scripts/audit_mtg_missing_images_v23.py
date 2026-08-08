from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2


def _url() -> str:
    value = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("No database URL configured")
    if value.startswith("postgresql+psycopg2://"):
        value = "postgresql://" + value[len("postgresql+psycopg2://"):]
    elif value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def run(output: Path) -> dict:
    conn = psycopg2.connect(_url())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                """
                WITH missing AS (
                  SELECT DISTINCT ON (p.scryfall_id)
                    p.scryfall_id,
                    c.id AS card_id,
                    c.name,
                    c.card_key,
                    s.code AS set_code,
                    s.name AS set_name,
                    ca.attributes_json AS card_attrs,
                    pa.attributes_json AS print_attrs,
                    COUNT(*) OVER (PARTITION BY p.scryfall_id) AS finish_count
                  FROM prints p
                  JOIN cards c ON c.id=p.card_id
                  JOIN games g ON g.id=c.game_id
                  JOIN sets s ON s.id=p.set_id
                  JOIN card_attributes ca ON ca.card_id=c.id
                  JOIN print_attributes pa ON pa.print_id=p.id
                  WHERE g.slug='mtg'
                    AND NOT EXISTS (
                      SELECT 1 FROM print_images i WHERE i.print_id=p.id
                    )
                  ORDER BY p.scryfall_id, p.id
                )
                SELECT scryfall_id, card_id, name, card_key, set_code, set_name,
                       card_attrs, print_attrs, finish_count
                FROM missing
                ORDER BY set_code, name, scryfall_id
                """
            )
            rows = cur.fetchall()
            conn.rollback()
    finally:
        conn.close()

    image_status = Counter()
    layouts = Counter()
    set_types = Counter()
    sets = Counter()
    promo = Counter()
    highres = Counter()
    oracle_presence = Counter()
    samples_by_status: dict[str, list[dict]] = defaultdict(list)
    samples_by_layout: dict[str, list[dict]] = defaultdict(list)

    objects = []
    for (
        scryfall_id,
        card_id,
        name,
        card_key,
        set_code,
        set_name,
        card_attrs,
        print_attrs,
        finish_count,
    ) in rows:
        card_attrs = dict(card_attrs or {})
        print_attrs = dict(print_attrs or {})
        status = str(print_attrs.get("image_status") or "<missing>")
        layout = str(card_attrs.get("layout") or "<missing>")
        set_type = str(print_attrs.get("set_type") or "<missing>")
        image_status[status] += 1
        layouts[layout] += 1
        set_types[set_type] += 1
        sets[f"{set_code} | {set_name}"] += 1
        promo[str(bool(print_attrs.get("promo"))).lower()] += 1
        highres[str(bool(print_attrs.get("highres_image"))).lower()] += 1
        has_oracle = str(card_key or "").startswith("mtg:oracle:")
        oracle_presence["oracle" if has_oracle else "fallback"] += 1

        item = {
            "scryfall_id": str(scryfall_id),
            "card_id": int(card_id),
            "name": str(name),
            "card_key": str(card_key),
            "set_code": str(set_code),
            "set_name": str(set_name),
            "layout": layout,
            "image_status": status,
            "set_type": set_type,
            "promo": bool(print_attrs.get("promo")),
            "highres_image": bool(print_attrs.get("highres_image")),
            "finish_count_missing": int(finish_count),
            "scryfall_uri": print_attrs.get("scryfall_uri"),
        }
        objects.append(item)
        if len(samples_by_status[status]) < 12:
            samples_by_status[status].append(item)
        if len(samples_by_layout[layout]) < 12:
            samples_by_layout[layout].append(item)

    # Missing-image objects are source-supported data gaps, not identity gaps.
    # We block only if their count moved unexpectedly or if any canonical row
    # lost the Scryfall source object ID.
    blockers = []
    if len(rows) != 162:
        blockers.append(f"missing_source_object_count:{len(rows)}!=162")
    if any(not item["scryfall_id"] for item in objects):
        blockers.append("blank_scryfall_id")

    report = {
        "status": "pass" if not blockers else "blocked",
        "mode": "read_only",
        "database_writes": 0,
        "missing_source_objects": len(rows),
        "missing_exact_prints": sum(int(item["finish_count_missing"]) for item in objects),
        "by_image_status": dict(sorted(image_status.items())),
        "by_layout": dict(sorted(layouts.items())),
        "by_set_type": dict(sorted(set_types.items())),
        "by_promo": dict(sorted(promo.items())),
        "by_highres_image": dict(sorted(highres.items())),
        "by_identity_type": dict(sorted(oracle_presence.items())),
        "top_sets": [
            {"set": key, "objects": value}
            for key, value in sets.most_common(30)
        ],
        "samples_by_image_status": dict(samples_by_status),
        "samples_by_layout": dict(samples_by_layout),
        "blockers": blockers,
        "policy": {
            "fabricate_images": False,
            "drop_canonical_prints_for_missing_image": False,
            "ui_fallback_required": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if blockers:
        raise SystemExit("MTG missing-image audit BLOCKED")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
