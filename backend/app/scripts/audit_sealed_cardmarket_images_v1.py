from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image
from sqlalchemy import text

from app import db

HOST = "product-images.s3.cardmarket.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CONTROL = "https://product-images.s3.cardmarket.com/5/DUAD-JP/823714/823714.jpg"

PROFILES = {
    "cardmarket_referer": {
        "Referer": "https://www.cardmarket.com/",
    },
    "direct_browser": {},
    "dontripit_img": {
        "Referer": "https://dontripit.com/",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    },
}


def get(url: str, profile: str = "cardmarket_referer", timeout: int = 30):
    if profile not in PROFILES:
        raise ValueError(f"unknown request profile: {profile}")
    headers = {
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        **PROFILES[profile],
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return body, {
                "profile": profile,
                "status": int(getattr(response, "status", 200) or 200),
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(body),
                "final_url": response.geturl(),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
    except urllib.error.HTTPError as exc:
        return None, {
            "profile": profile,
            "status": int(exc.code),
            "error": f"HTTPError: {exc.code}",
            "final_url": exc.geturl(),
        }
    except Exception as exc:
        return None, {
            "profile": profile,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def image_meta(body, meta):
    out = dict(meta)
    if not body:
        return False, out
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            image_format = image.format
        out.update(width=width, height=height, format=image_format)
        # Cardmarket currently serves a non-standard Content-Type header
        # (`multerS3.AUTO_CONTENT_TYPE`) for otherwise valid image bytes.
        # Decode + sane dimensions are the authoritative media checks.
        return bool(width >= 80 and height >= 80), out
    except Exception as exc:
        out["decode_error"] = f"{type(exc).__name__}: {exc}"
        return False, out


def verify_profile(url: str, profile: str) -> tuple[bool, dict]:
    body, meta = get(url, profile=profile)
    return image_meta(body, meta)


def sealed_s3_url(category_id: str, product_id: str) -> str:
    """Cardmarket non-single public asset form: /category/idProduct/idProduct.jpg.

    Unlike singles, this path does not derive an expansion/set token. Both path
    inputs are immutable source-owned fields from the current Cardmarket feed.
    """
    return f"https://{HOST}/{category_id}/{product_id}/{product_id}.jpg"


def classify_failure(meta: dict) -> str:
    status = meta.get("status")
    if status == 404:
        return "s3_not_found"
    if status in {401, 403, 429}:
        return "s3_blocked"
    if status == 200:
        return "s3_invalid_media"
    return "s3_error"


def probe(row: dict) -> dict:
    product_id = str(row.get("external_id") or "").strip()
    category_id = str(row.get("category_id") or "").strip()
    keep = (
        "variant_id",
        "game",
        "product_name",
        "product_type",
        "set_code",
        "language",
        "region",
        "packaging",
        "external_id",
        "external_name",
        "category_id",
        "category",
        "expansion_external_id",
        "website_path",
        "mapping_method",
    )
    out = {key: row.get(key) for key in keep}
    out["id_product"] = product_id

    if not product_id or not category_id:
        out.update(probe="missing_source_path_fields", valid_image=False, browser_deliverable=False)
        return out

    url = sealed_s3_url(category_id, product_id)
    valid, meta = verify_profile(url, "cardmarket_referer")
    out["candidate_url"] = url
    out["candidate_verification"] = meta

    if not valid:
        out.update(probe=classify_failure(meta), valid_image=False, browser_deliverable=False)
        return out

    baseline_hash = str(meta.get("sha256") or "")
    delivery_profiles = {"cardmarket_referer": meta}
    browser_safe = True
    for profile in ("direct_browser", "dontripit_img"):
        profile_valid, profile_meta = verify_profile(url, profile)
        profile_meta["valid_image"] = profile_valid
        profile_meta["same_sha256_as_source_probe"] = bool(
            profile_valid and baseline_hash and str(profile_meta.get("sha256") or "") == baseline_hash
        )
        delivery_profiles[profile] = profile_meta
        if not profile_valid or not profile_meta["same_sha256_as_source_probe"]:
            browser_safe = False

    out.update(
        probe="resolved" if browser_safe else "resolved_not_browser_deliverable",
        valid_image=True,
        browser_deliverable=browser_safe,
        delivery_profiles=delivery_profiles,
        image_url=url,
        image_sha256=meta.get("sha256"),
        image_width=meta.get("width"),
        image_height=meta.get("height"),
        image_format=meta.get("format"),
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sample-per-type", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.sample_per_type <= 5:
        raise SystemExit("--sample-per-type must be 1..5")

    control_profiles = {}
    for profile in PROFILES:
        valid, meta = verify_profile(CONTROL, profile)
        control_profiles[profile] = {"url": CONTROL, "valid": valid, **meta}
    control = control_profiles["cardmarket_referer"]

    query = text(
        """
        WITH latest AS (
          SELECT game_id, MAX(last_seen_at) latest_seen
          FROM external_catalog_products
          WHERE source='cardmarket' AND product_group='non_single'
          GROUP BY game_id
        ), strict AS (
          SELECT
            l.product_variant_id,
            l.external_product_id,
            l.mapping_method,
            e.external_id,
            e.name external_name,
            e.category_id,
            e.category,
            e.expansion_external_id,
            e.website_path,
            COUNT(*) OVER(PARTITION BY l.external_product_id) variants_per_external,
            COUNT(*) OVER(PARTITION BY l.product_variant_id) externals_per_variant
          FROM external_catalog_product_variant_links l
          JOIN external_catalog_products e ON e.id=l.external_product_id
          JOIN latest x ON x.game_id=e.game_id AND x.latest_seen=e.last_seen_at
          WHERE e.source='cardmarket'
            AND e.product_group='non_single'
            AND l.link_status IN ('accepted','mapped','exact')
            AND l.confidence='exact'
            AND l.reviewed=TRUE
        ), eligible AS (
          SELECT
            s.product_variant_id variant_id,
            g.slug game,
            p.name product_name,
            p.product_type,
            st.code set_code,
            pv.language,
            pv.region,
            pv.packaging,
            s.external_id,
            s.external_name,
            s.category_id,
            s.category,
            s.expansion_external_id,
            s.website_path,
            s.mapping_method,
            ROW_NUMBER() OVER(
              PARTITION BY g.slug,p.product_type
              ORDER BY CASE WHEN COALESCE(s.category_id,'')='' THEN 1 ELSE 0 END,p.name,pv.id
            ) rn
          FROM strict s
          JOIN product_variants pv ON pv.id=s.product_variant_id
          JOIN products p ON p.id=pv.product_id
          JOIN games g ON g.id=p.game_id
          LEFT JOIN sets st ON st.id=p.set_id
          WHERE s.variants_per_external=1
            AND s.externals_per_variant=1
            AND NOT EXISTS(
              SELECT 1 FROM product_images pi WHERE pi.product_variant_id=pv.id
            )
        )
        SELECT * FROM eligible
        WHERE rn<=:n
        ORDER BY game,product_type,rn,variant_id
        """
    )
    with db.SessionLocal() as session:
        rows = [dict(row) for row in session.execute(query, {"n": args.sample_per_type}).mappings().all()]

    results = [probe(row) for row in rows]
    counts = Counter(str(item["probe"]) for item in results)
    by_game = defaultdict(Counter)
    by_type = defaultdict(Counter)
    for item in results:
        by_game[str(item["game"])][str(item["probe"])] += 1
        by_type[f"{item['game']}|{item['product_type']}"][str(item["probe"])] += 1

    resolved = [item for item in results if item.get("valid_image")]
    browser_deliverable = [item for item in resolved if item.get("browser_deliverable")]
    browser_unsafe = [item for item in resolved if not item.get("browser_deliverable")]

    hashes = defaultdict(list)
    for item in resolved:
        hashes[str(item.get("image_sha256") or "")].append(
            {key: item.get(key) for key in ("game", "product_type", "variant_id", "id_product", "product_name", "image_url")}
        )
    duplicates = {
        digest: items
        for digest, items in hashes.items()
        if digest and len({str(item["id_product"]) for item in items}) > 1
    }

    control_safe = all(item.get("valid") for item in control_profiles.values())
    all_resolved_browser_safe = len(browser_unsafe) == 0
    status = "pass" if control_safe and resolved and all_resolved_browser_safe and not duplicates else "fail"

    report = {
        "status": status,
        "production_writes": 0,
        "path_contract": "https://product-images.s3.cardmarket.com/{category_id}/{idProduct}/{idProduct}.jpg",
        "path_inputs": "current exact source-owned Cardmarket category_id + idProduct only",
        "browser_delivery_contract": "direct browser + dontripit.com image referer must decode and retain identical SHA-256",
        "control_cardmarket_s3": control,
        "control_delivery_profiles": control_profiles,
        "sample_per_game_product_type": args.sample_per_type,
        "sample_rows": len(rows),
        "probe_counts": dict(sorted(counts.items())),
        "resolved_exact_product_images": len(resolved),
        "resolved_browser_deliverable_images": len(browser_deliverable),
        "resolved_not_browser_deliverable_images": len(browser_unsafe),
        "resolved_unique_urls": len({str(item.get("image_url")) for item in resolved}),
        "resolved_unique_hashes": len({str(item.get("image_sha256")) for item in resolved}),
        "by_game": {game: dict(sorted(counter.items())) for game, counter in sorted(by_game.items())},
        "by_game_product_type": {key: dict(sorted(counter.items())) for key, counter in sorted(by_type.items())},
        "duplicate_hash_groups_across_distinct_products": duplicates,
        "browser_unsafe_candidates": [
            {key: item.get(key) for key in ("game", "product_type", "variant_id", "id_product", "product_name", "image_url", "delivery_profiles")}
            for item in browser_unsafe
        ],
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
