from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

from app import db


ACCEPTED = ("accepted", "mapped", "exact")
IMAGE_KEYWORDS = ("image", "photo", "picture", "thumbnail", "thumb", "media")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def _image_like_fields(value, path: str = "") -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            child = f"{path}.{key}" if path else str(key)
            lower_key = str(key).lower()
            if isinstance(nested, str):
                lower_value = nested.lower().split("?", 1)[0]
                if any(token in lower_key for token in IMAGE_KEYWORDS) or (
                    "url" in lower_key and lower_value.endswith(IMAGE_SUFFIXES)
                ):
                    found.append({"path": child, "value": nested[:500]})
            found.extend(_image_like_fields(nested, child))
    elif isinstance(value, list):
        for index, nested in enumerate(value[:20]):
            found.extend(_image_like_fields(nested, f"{path}[{index}]"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--sample-limit", type=int, default=8)
    args = parser.parse_args()

    missing_sql = text(
        """
        SELECT pv.id AS variant_id,
               g.slug AS game,
               p.name AS product_name,
               p.product_type,
               s.code AS set_code,
               pv.language,
               pv.region,
               pv.packaging,
               pv.sku
        FROM product_variants pv
        JOIN products p ON p.id = pv.product_id
        JOIN games g ON g.id = p.game_id
        LEFT JOIN sets s ON s.id = p.set_id
        WHERE NOT EXISTS (
          SELECT 1 FROM product_images pi WHERE pi.product_variant_id = pv.id
        )
        ORDER BY g.slug, p.name, pv.id
        """
    )
    identifier_sql = text(
        """
        SELECT g.slug AS game,
               pid.source,
               COUNT(DISTINCT pv.id)::bigint AS variants
        FROM product_variants pv
        JOIN products p ON p.id = pv.product_id
        JOIN games g ON g.id = p.game_id
        JOIN product_identifiers pid ON pid.product_variant_id = pv.id
        WHERE NOT EXISTS (
          SELECT 1 FROM product_images pi WHERE pi.product_variant_id = pv.id
        )
        GROUP BY g.slug, pid.source
        ORDER BY g.slug, variants DESC, pid.source
        """
    )
    existing_image_sources_sql = text(
        """
        SELECT g.slug AS game,
               COALESCE(pi.source, '<null>') AS source,
               COUNT(*)::bigint AS images,
               COUNT(DISTINCT pi.product_variant_id)::bigint AS variants
        FROM product_images pi
        JOIN product_variants pv ON pv.id = pi.product_variant_id
        JOIN products p ON p.id = pv.product_id
        JOIN games g ON g.id = p.game_id
        GROUP BY g.slug, COALESCE(pi.source, '<null>')
        ORDER BY g.slug, variants DESC, source
        """
    )
    strict_cardmarket_sql = text(
        """
        WITH latest AS (
          SELECT game_id, MAX(last_seen_at) AS latest_seen
          FROM external_catalog_products
          WHERE source = 'cardmarket' AND product_group = 'non_single'
          GROUP BY game_id
        ), strict AS (
          SELECT l.external_product_id,
                 l.product_variant_id,
                 l.mapping_method,
                 ecp.external_id,
                 ecp.game_id,
                 ecp.name AS external_name,
                 ecp.category_id,
                 ecp.category,
                 ecp.website_path,
                 ecp.raw_json,
                 COUNT(*) OVER (PARTITION BY l.external_product_id) AS variants_per_external,
                 COUNT(*) OVER (PARTITION BY l.product_variant_id) AS externals_per_variant
          FROM external_catalog_product_variant_links l
          JOIN external_catalog_products ecp ON ecp.id = l.external_product_id
          JOIN latest x ON x.game_id = ecp.game_id AND x.latest_seen = ecp.last_seen_at
          WHERE ecp.source = 'cardmarket'
            AND ecp.product_group = 'non_single'
            AND l.link_status IN ('accepted', 'mapped', 'exact')
            AND l.confidence = 'exact'
            AND l.reviewed = TRUE
        )
        SELECT s.product_variant_id AS variant_id,
               g.slug AS game,
               p.name AS product_name,
               p.product_type,
               pv.language,
               pv.region,
               pv.packaging,
               s.external_product_id,
               s.external_id,
               s.external_name,
               s.category_id,
               s.category,
               s.website_path,
               s.raw_json,
               s.mapping_method
        FROM strict s
        JOIN product_variants pv ON pv.id = s.product_variant_id
        JOIN products p ON p.id = pv.product_id
        JOIN games g ON g.id = p.game_id
        WHERE s.variants_per_external = 1
          AND s.externals_per_variant = 1
          AND NOT EXISTS (
            SELECT 1 FROM product_images pi WHERE pi.product_variant_id = s.product_variant_id
          )
        ORDER BY g.slug, p.name, s.product_variant_id
        """
    )
    accepted_cardmarket_counts_sql = text(
        """
        WITH latest AS (
          SELECT game_id, MAX(last_seen_at) AS latest_seen
          FROM external_catalog_products
          WHERE source = 'cardmarket' AND product_group = 'non_single'
          GROUP BY game_id
        )
        SELECT g.slug AS game,
               COUNT(DISTINCT l.product_variant_id)::bigint AS variants
        FROM external_catalog_product_variant_links l
        JOIN external_catalog_products ecp ON ecp.id = l.external_product_id
        JOIN latest x ON x.game_id = ecp.game_id AND x.latest_seen = ecp.last_seen_at
        JOIN product_variants pv ON pv.id = l.product_variant_id
        JOIN products p ON p.id = pv.product_id
        JOIN games g ON g.id = p.game_id
        WHERE ecp.source = 'cardmarket'
          AND ecp.product_group = 'non_single'
          AND l.link_status IN ('accepted', 'mapped', 'exact')
          AND NOT EXISTS (
            SELECT 1 FROM product_images pi WHERE pi.product_variant_id = l.product_variant_id
          )
        GROUP BY g.slug
        ORDER BY g.slug
        """
    )

    with db.SessionLocal() as session:
        missing = [dict(row) for row in session.execute(missing_sql).mappings().all()]
        identifier_rows = [dict(row) for row in session.execute(identifier_sql).mappings().all()]
        existing_sources = [dict(row) for row in session.execute(existing_image_sources_sql).mappings().all()]
        strict_rows = [dict(row) for row in session.execute(strict_cardmarket_sql).mappings().all()]
        accepted_rows = [dict(row) for row in session.execute(accepted_cardmarket_counts_sql).mappings().all()]

    missing_by_game = Counter(row["game"] for row in missing)
    missing_by_product_type: dict[str, Counter] = defaultdict(Counter)
    missing_by_language_region: dict[str, Counter] = defaultdict(Counter)
    for row in missing:
        missing_by_product_type[row["game"]][str(row.get("product_type") or "<null>")] += 1
        key = f"{row.get('language') or '<null>'}|{row.get('region') or '<null>'}"
        missing_by_language_region[row["game"]][key] += 1

    identifiers_by_game: dict[str, dict[str, int]] = defaultdict(dict)
    for row in identifier_rows:
        identifiers_by_game[row["game"]][row["source"]] = int(row["variants"])

    existing_sources_by_game: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for row in existing_sources:
        existing_sources_by_game[row["game"]][row["source"]] = {
            "images": int(row["images"]),
            "variants": int(row["variants"]),
        }

    accepted_by_game = {row["game"]: int(row["variants"]) for row in accepted_rows}
    strict_by_game = Counter(row["game"] for row in strict_rows)
    strict_with_path_by_game = Counter(
        row["game"] for row in strict_rows if str(row.get("website_path") or "").strip()
    )
    strict_with_raw_image_field_by_game = Counter()
    image_field_paths_by_game: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list[dict]] = defaultdict(list)

    for row in strict_rows:
        image_fields = _image_like_fields(row.get("raw_json"))
        if image_fields:
            strict_with_raw_image_field_by_game[row["game"]] += 1
            for field in image_fields:
                image_field_paths_by_game[row["game"]][field["path"]] += 1
        if len(samples[row["game"]]) < args.sample_limit:
            samples[row["game"]].append({
                "variant_id": int(row["variant_id"]),
                "product_name": row["product_name"],
                "product_type": row["product_type"],
                "language": row["language"],
                "region": row["region"],
                "packaging": row["packaging"],
                "cardmarket_id_product": row["external_id"],
                "cardmarket_name": row["external_name"],
                "category_id": row["category_id"],
                "category": row["category"],
                "website_path": row["website_path"],
                "mapping_method": row["mapping_method"],
                "image_like_fields": image_fields[:10],
            })

    report = {
        "status": "pass",
        "production_writes": 0,
        "missing_product_variants": len(missing),
        "missing_by_game": dict(sorted(missing_by_game.items())),
        "missing_by_product_type": {
            game: dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))
            for game, counter in sorted(missing_by_product_type.items())
        },
        "missing_by_language_region": {
            game: dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))
            for game, counter in sorted(missing_by_language_region.items())
        },
        "identifier_coverage_by_game": dict(sorted(identifiers_by_game.items())),
        "existing_product_image_sources_by_game": dict(sorted(existing_sources_by_game.items())),
        "current_cardmarket_accepted_missing_variants_by_game": dict(sorted(accepted_by_game.items())),
        "current_cardmarket_exact_reviewed_one_to_one_missing_by_game": dict(sorted(strict_by_game.items())),
        "strict_with_website_path_by_game": dict(sorted(strict_with_path_by_game.items())),
        "strict_with_image_like_raw_field_by_game": dict(sorted(strict_with_raw_image_field_by_game.items())),
        "raw_image_field_paths_by_game": {
            game: dict(counter.most_common(20))
            for game, counter in sorted(image_field_paths_by_game.items())
        },
        "samples": dict(sorted(samples.items())),
    }

    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
