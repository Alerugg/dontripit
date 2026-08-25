from __future__ import annotations

import csv
import json
import os
from collections import Counter
from itertools import groupby
from pathlib import Path
from urllib.parse import urljoin

from sqlalchemy import text

from app import db

CARDMARKET_BASE = "https://www.cardmarket.com"
OUT_DIR = Path(os.environ.get("WORKLIST_OUT_DIR", "tmp/market-version-worklist-v3"))

QUERY = text(
    r"""
    WITH latest_cardmarket AS (
        SELECT game_id, MAX(last_seen_at) AS last_seen_at
        FROM external_catalog_products
        WHERE source = 'cardmarket'
          AND product_group = 'single'
        GROUP BY game_id
    ), accepted_candidates AS (
        SELECT
            l.print_id,
            l.link_status,
            e.id AS market_row_id,
            e.external_id AS market_external_id,
            e.name AS market_name,
            e.website_path,
            e.metacard_external_id,
            e.expansion_external_id,
            COUNT(*) OVER (PARTITION BY l.print_id) AS accepted_product_count
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id = l.external_product_id
        JOIN latest_cardmarket latest
          ON latest.game_id = e.game_id
         AND latest.last_seen_at = e.last_seen_at
        WHERE e.source = 'cardmarket'
          AND e.product_group = 'single'
          AND l.link_status IN ('accepted', 'mapped', 'exact')
    ), exact_link AS (
        SELECT *
        FROM accepted_candidates
        WHERE accepted_product_count = 1
    )
    SELECT
        g.slug AS tcg,
        c.id AS card_id,
        c.name AS card_name,
        p.id AS print_id,
        p.language,
        p.collector_number,
        p.rarity,
        p.is_foil,
        p.variant,
        s.id AS set_id,
        s.code AS set_code,
        s.name AS set_name,
        s.region AS set_region,
        (
            SELECT pi.url
            FROM print_images pi
            WHERE pi.print_id = p.id
              AND NULLIF(BTRIM(COALESCE(pi.url, '')), '') IS NOT NULL
            ORDER BY pi.is_primary DESC, pi.id ASC
            LIMIT 1
        ) AS primary_image_url,
        el.link_status AS exact_link_status,
        el.market_row_id,
        el.market_external_id,
        el.market_name,
        el.website_path,
        el.metacard_external_id,
        el.expansion_external_id
    FROM prints p
    JOIN cards c ON c.id = p.card_id
    JOIN games g ON g.id = c.game_id
    JOIN sets s ON s.id = p.set_id
    LEFT JOIN exact_link el ON el.print_id = p.id
    ORDER BY
        c.id,
        s.id,
        LOWER(COALESCE(p.collector_number, '')),
        LOWER(COALESCE(p.rarity, '')),
        p.is_foil,
        LOWER(COALESCE(p.variant, '')),
        p.id
    """
)

FIELDS = [
    "status",
    "cardmarket_url",
    "image_url",
    "notes",
    "priority",
    "issue_type",
    "tcg",
    "commercial_version_key",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "rarity",
    "finish",
    "variant",
    "physical_region",
    "languages",
    "language_count",
    "print_count",
    "card_id",
    "set_id",
    "print_ids",
    "print_ids_by_language",
    "current_cardmarket_product_id",
    "current_cardmarket_product_name",
    "current_cardmarket_url",
    "current_cardmarket_metacard_id",
    "current_cardmarket_expansion_id",
    "cardmarket_status",
    "cardmarket_link_statuses",
    "cardmarket_distinct_product_count",
    "representative_image_status",
    "current_image_url",
    "prints_with_image",
    "prints_without_image",
    "image_coverage_pct",
    "lookup_hint",
    "recommended_cardmarket_search",
    "resolution_path",
    "confidence",
    "import_key",
]


def clean(value) -> str:
    return str(value or "").strip()


def norm(value) -> str:
    return clean(value).lower()


def fallback_key(row: dict) -> tuple:
    return (
        int(row["card_id"]),
        int(row["set_id"]),
        norm(row.get("collector_number")),
        norm(row.get("rarity")),
        bool(row.get("is_foil")),
        norm(row.get("variant") or "default"),
    )


def group_key_text(key: tuple) -> str:
    card_id, set_id, collector, rarity, is_foil, variant = key
    return f"card:{card_id}|set:{set_id}|collector:{collector}|rarity:{rarity}|finish:{'foil' if is_foil else 'nonfoil'}|variant:{variant or 'default'}"


def cm_url(path: str | None) -> str:
    raw = clean(path)
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return urljoin(CARDMARKET_BASE, raw if raw.startswith("/") else f"/{raw}")


def summarize_group(rows: list[dict]) -> dict | None:
    first = rows[0]
    key = fallback_key(first)

    languages = sorted({norm(r.get("language")) or "unknown" for r in rows})
    print_ids = [int(r["print_id"]) for r in rows]
    ids_by_language: dict[str, list[int]] = {}
    for r in rows:
        lang = norm(r.get("language")) or "unknown"
        ids_by_language.setdefault(lang, []).append(int(r["print_id"]))

    market_rows = {
        int(r["market_row_id"]): r
        for r in rows
        if r.get("market_row_id") is not None
    }
    market_count = len(market_rows)
    image_rows = [r for r in rows if clean(r.get("primary_image_url"))]
    representative_image = clean(image_rows[0].get("primary_image_url")) if image_rows else ""

    if market_count == 0:
        cm_status = "MISSING"
    elif market_count == 1:
        cm_status = "LINKED_SIBLING"
    else:
        cm_status = "CONFLICT"

    image_status = "PRESENT" if representative_image else "MISSING"
    missing_cm = cm_status != "LINKED_SIBLING"
    missing_image = image_status == "MISSING"
    if not missing_cm and not missing_image:
        return None

    if cm_status == "CONFLICT" and missing_image:
        issue_type = "CARDMARKET_CONFLICT_AND_IMAGE"
        priority = "P0_REVIEW"
    elif cm_status == "CONFLICT":
        issue_type = "CARDMARKET_CONFLICT"
        priority = "P0_REVIEW"
    elif missing_cm and missing_image:
        issue_type = "CARDMARKET_AND_IMAGE"
        priority = "P1_BOTH"
    elif missing_cm:
        issue_type = "CARDMARKET_ONLY"
        priority = "P2_CARDMARKET"
    else:
        issue_type = "IMAGE_ONLY"
        priority = "P3_IMAGE"

    market = next(iter(market_rows.values())) if market_count == 1 else None
    market_ids = sorted({clean(r.get("market_external_id")) for r in rows if clean(r.get("market_external_id"))})
    market_names = sorted({clean(r.get("market_name")) for r in rows if clean(r.get("market_name"))})
    market_urls = sorted({cm_url(r.get("website_path")) for r in rows if clean(r.get("website_path"))})
    metacard_ids = sorted({clean(r.get("metacard_external_id")) for r in rows if clean(r.get("metacard_external_id"))})
    expansion_ids = sorted({clean(r.get("expansion_external_id")) for r in rows if clean(r.get("expansion_external_id"))})
    link_statuses = sorted({clean(r.get("exact_link_status")) for r in rows if clean(r.get("exact_link_status"))})

    card_name = clean(first.get("card_name"))
    set_name = clean(first.get("set_name"))
    set_code = clean(first.get("set_code"))
    collector = clean(first.get("collector_number"))
    rarity = clean(first.get("rarity"))
    variant = clean(first.get("variant")) or "default"
    finish = "foil" if bool(first.get("is_foil")) else "nonfoil"
    tcg = clean(first.get("tcg"))
    region = clean(first.get("set_region"))

    lookup_parts = [tcg, card_name, set_name, set_code, collector, rarity, finish, variant, region]
    lookup_hint = " | ".join(part for part in lookup_parts if part)
    recommended_search = " ".join(part for part in [card_name, set_code or set_name, collector, variant if variant != 'default' else '', 'foil' if finish == 'foil' else ''] if part)

    if cm_status == "MISSING":
        resolution_path = "USER_FILL_CARDMARKET_URL -> validate exact product -> extract product id -> propagate to sibling-language prints only after identity checks"
        confidence = "MANUAL_REQUIRED"
    elif cm_status == "CONFLICT":
        resolution_path = "REVIEW_CONFLICT -> verify whether rows are truly distinct regional/physical variants before any merge"
        confidence = "CONFLICT_REVIEW"
    else:
        resolution_path = "CARDMARKET_ALREADY_KNOWN; resolve representative image only"
        confidence = "EXACT_SIBLING_LINK"

    image_count = len(image_rows)
    print_count = len(rows)
    coverage = round((image_count / print_count) * 100, 2) if print_count else 0.0
    version_key = group_key_text(key)

    return {
        "status": "PENDING",
        "cardmarket_url": "",
        "image_url": "",
        "notes": "",
        "priority": priority,
        "issue_type": issue_type,
        "tcg": tcg,
        "commercial_version_key": version_key,
        "card_name": card_name,
        "set_name": set_name,
        "set_code": set_code,
        "collector_number": collector,
        "rarity": rarity,
        "finish": finish,
        "variant": variant,
        "physical_region": region,
        "languages": ",".join(languages),
        "language_count": len(languages),
        "print_count": print_count,
        "card_id": int(first["card_id"]),
        "set_id": int(first["set_id"]),
        "print_ids": ",".join(str(v) for v in print_ids),
        "print_ids_by_language": json.dumps(ids_by_language, separators=(",", ":"), sort_keys=True),
        "current_cardmarket_product_id": ",".join(market_ids),
        "current_cardmarket_product_name": " || ".join(market_names),
        "current_cardmarket_url": " || ".join(market_urls),
        "current_cardmarket_metacard_id": ",".join(metacard_ids),
        "current_cardmarket_expansion_id": ",".join(expansion_ids),
        "cardmarket_status": cm_status,
        "cardmarket_link_statuses": ",".join(link_statuses),
        "cardmarket_distinct_product_count": market_count,
        "representative_image_status": image_status,
        "current_image_url": representative_image,
        "prints_with_image": image_count,
        "prints_without_image": print_count - image_count,
        "image_coverage_pct": coverage,
        "lookup_hint": lookup_hint,
        "recommended_cardmarket_search": recommended_search,
        "resolution_path": resolution_path,
        "confidence": confidence,
        "import_key": version_key,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    worklist_path = OUT_DIR / "card_worklist.csv"
    summary_path = OUT_DIR / "summary.json"

    counts = Counter()
    by_game = Counter()
    by_issue = Counter()

    with db.SessionLocal() as session, worklist_path.open("w", newline="", encoding="utf-8-sig") as fh:
        # Defensive: enforce a read-only transaction in PostgreSQL before the export query.
        session.execute(text("SET TRANSACTION READ ONLY"))
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()

        stream = session.execute(QUERY.execution_options(stream_results=True)).mappings()
        for _key, iterator in groupby(stream, key=lambda row: fallback_key(dict(row))):
            group_rows = [dict(row) for row in iterator]
            counts["commercial_versions_scanned"] += 1
            counts["prints_scanned"] += len(group_rows)
            item = summarize_group(group_rows)
            if item is None:
                counts["fully_resolved_versions"] += 1
                continue
            writer.writerow(item)
            counts["worklist_rows"] += 1
            by_game[item["tcg"]] += 1
            by_issue[item["issue_type"]] += 1
            if item["cardmarket_status"] == "MISSING":
                counts["missing_cardmarket"] += 1
            if item["cardmarket_status"] == "CONFLICT":
                counts["cardmarket_conflict"] += 1
            if item["representative_image_status"] == "MISSING":
                counts["missing_representative_image"] += 1

    summary = {
        "schema_version": "market-version-manual-worklist-v3",
        "definition": "One row per commercial version: card + set/region + collector number + finish + real variant. Languages are grouped; real physical/regional variants are not merged.",
        "counts": dict(counts),
        "by_game": dict(sorted(by_game.items())),
        "by_issue": dict(sorted(by_issue.items())),
        "output": str(worklist_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
