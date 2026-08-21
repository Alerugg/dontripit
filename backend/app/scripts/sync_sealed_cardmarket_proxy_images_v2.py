from __future__ import annotations

"""V2 certification policy for exact sealed Cardmarket proxy images.

Cardmarket legitimately reuses identical image bytes for some distinct source
products (for example generic case / jumbo-box artwork). Pixel-hash reuse is
therefore evidence to report, not an identity collision. Exact identity remains
bound to the source-owned ``category_id + idProduct`` URL and the reviewed 1:1
canonical mapping.

V2 is deliberately idempotent in production: variants with no image remain
candidates, and variants already materialized by this exact proxy source are
re-certified. Variants owned by any other image source remain excluded and are
never overwritten.
"""

from collections import defaultdict

from sqlalchemy import text

from app.scripts import sync_sealed_cardmarket_proxy_images_v1 as v1


IDEMPOTENT_ELIGIBLE_SQL = text(
    """
    WITH latest AS (
      SELECT game_id, MAX(last_seen_at) AS latest_seen
      FROM external_catalog_products
      WHERE source = 'cardmarket'
        AND product_group = 'non_single'
      GROUP BY game_id
    ), strict AS (
      SELECT
        l.product_variant_id,
        l.external_product_id,
        l.mapping_method,
        e.game_id,
        e.external_id,
        e.name AS external_name,
        e.category_id,
        e.category,
        e.website_path,
        COUNT(*) OVER(PARTITION BY l.external_product_id) AS variants_per_external,
        COUNT(*) OVER(PARTITION BY l.product_variant_id) AS externals_per_variant
      FROM external_catalog_product_variant_links l
      JOIN external_catalog_products e ON e.id = l.external_product_id
      JOIN latest x ON x.game_id = e.game_id AND x.latest_seen = e.last_seen_at
      WHERE e.source = 'cardmarket'
        AND e.product_group = 'non_single'
        AND l.link_status IN ('accepted', 'mapped', 'exact')
        AND l.confidence = 'exact'
        AND l.reviewed = TRUE
    )
    SELECT
      s.product_variant_id AS variant_id,
      g.slug AS game,
      p.id AS product_id,
      p.name AS product_name,
      p.product_type,
      st.code AS set_code,
      pv.language,
      pv.region,
      pv.packaging,
      s.external_id,
      s.external_name,
      s.category_id,
      s.category,
      s.website_path,
      s.mapping_method
    FROM strict s
    JOIN product_variants pv ON pv.id = s.product_variant_id
    JOIN products p ON p.id = pv.product_id
    JOIN games g ON g.id = p.game_id
    LEFT JOIN sets st ON st.id = p.set_id
    WHERE s.variants_per_external = 1
      AND s.externals_per_variant = 1
      AND (
        NOT EXISTS (
          SELECT 1
          FROM product_images pi
          WHERE pi.product_variant_id = pv.id
        )
        OR (
          EXISTS (
            SELECT 1
            FROM product_images pi
            WHERE pi.product_variant_id = pv.id
              AND pi.source = 'cardmarket_exact_proxy_v1'
          )
          AND NOT EXISTS (
            SELECT 1
            FROM product_images pi
            WHERE pi.product_variant_id = pv.id
              AND COALESCE(pi.source, '') <> 'cardmarket_exact_proxy_v1'
          )
        )
      )
    ORDER BY g.slug, p.product_type, p.name, pv.id
    """
)

# V1's selector intentionally targeted only image-less variants. Once exact
# proxies exist that makes a production rerun appear to have zero recoverable
# rows. V2 keeps the exact same identity gate while also re-certifying rows that
# are already owned exclusively by this proxy source. Other image sources are
# left untouched.
v1.ELIGIBLE_SQL = IDEMPOTENT_ELIGIBLE_SQL

_base_certify_snapshot = v1.certify_snapshot


def certify_snapshot(engine, *, max_workers: int, timeout: int, retries: int):
    report = _base_certify_snapshot(
        engine,
        max_workers=max_workers,
        timeout=timeout,
        retries=retries,
    )

    recoverable = list(report.get("recoverable") or [])
    urls: dict[str, list[dict]] = defaultdict(list)
    for row in recoverable:
        url = str(row.get("source_image_url") or "")
        if url:
            urls[url].append(row)

    # URL reuse across different Cardmarket ids would be an identity/path bug
    # and remains fail-closed. Equal SHA-256 bytes at different exact URLs are
    # allowed because they are source-owned Cardmarket media reuse.
    source_url_collision_groups = {
        url: [
            {
                "variant_id": int(item["variant_id"]),
                "external_id": str(item["external_id"]),
                "product_name": str(item["product_name"]),
                "game": str(item["game"]),
            }
            for item in items
        ]
        for url, items in urls.items()
        if len({str(item.get("external_id") or "") for item in items}) > 1
    }

    failures = [
        failure
        for failure in list(report.get("failures") or [])
        if "duplicate_hash_groups" not in failure
    ]
    if source_url_collision_groups:
        failures.append({"source_url_collision_groups": len(source_url_collision_groups)})

    report["eligibility_policy"] = (
        "image_less_or_existing_exact_proxy_only; preserve_all_other_image_sources"
    )
    report["source_url_collision_groups"] = source_url_collision_groups
    report["source_url_collision_count"] = len(source_url_collision_groups)
    report["duplicate_hash_policy"] = (
        "informational_only_when_each_exact_cardmarket_source_url_is_unique"
    )
    report["image_byte_reuse_group_count"] = len(report.get("duplicate_hash_groups") or {})
    report["failures"] = failures

    if (
        report.get("control", {}).get("ok")
        and int(report.get("hard_failure_count") or 0) == 0
        and not source_url_collision_groups
        and (int(report.get("eligible") or 0) == 0 or int(report.get("recoverable_count") or 0) > 0)
        and not failures
    ):
        report["gate"] = "PASS"
    else:
        report["gate"] = "FAIL"

    return report


# Reuse V1's apply, two-pass idempotency, verification and CLI. V2 changes
# certification eligibility/policy only; DML remains the proven V1 path.
v1.certify_snapshot = certify_snapshot


if __name__ == "__main__":
    raise SystemExit(v1.main())
