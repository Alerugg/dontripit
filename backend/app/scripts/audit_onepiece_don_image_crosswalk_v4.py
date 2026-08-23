from __future__ import annotations

"""Read-only geometry calibration for the One Piece DON image crosswalk.

V3 fixed Cardmarket's singles S3 path and proved that 161/164 current DON
representative images are readable. V2/V3 still compared only one full-frame
rendering and also had a reporting bug where ``distance.max`` accidentally
included the already-computed ``sum`` value.

This V4 audit does NOT loosen or redefine the production exact-match gate. It
measures whether small border/crop differences between Cardmarket and Bandai's
embedded PDF renderings explain the residual perceptual distance. It produces a
ranked artifact only; production writes remain zero.
"""

import hashlib
import io
import json
import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import imagehash
import psycopg2
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg2.extras import RealDictCursor
from pypdf import PdfReader

from app.scripts import audit_onepiece_don_image_crosswalk_v2 as v2
from app.scripts import audit_onepiece_don_image_crosswalk_v3 as v3

OUTPUT = Path(
    os.getenv(
        "ONEPIECE_DON_IMAGE_CALIBRATION_OUTPUT",
        "artifacts/onepiece-don-image-crosswalk-v4.json",
    )
)
CROP_PCTS = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05)
HASH_KEYS = v2.HASH_KEYS
MAX_WORKERS = 10


def _image_from_bytes(body: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(body)) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RuntimeError("response does not decode as an image") from exc


def _crop(image: Image.Image, pct: float) -> Image.Image:
    if pct <= 0:
        return image.copy()
    width, height = image.size
    dx = max(1, int(round(width * pct)))
    dy = max(1, int(round(height * pct)))
    if width - 2 * dx < 32 or height - 2 * dy < 32:
        raise ValueError("crop leaves image too small")
    return image.crop((dx, dy, width - dx, height - dy))


def _fingerprints(body: bytes) -> dict:
    image = _image_from_bytes(body)
    width, height = image.size
    variants = {}
    for pct in CROP_PCTS:
        candidate = _crop(image, pct)
        variants[f"{pct:.2f}"] = {
            "phash": str(imagehash.phash(candidate, hash_size=16)),
            "dhash": str(imagehash.dhash(candidate, hash_size=16)),
            "whash": str(imagehash.whash(candidate, hash_size=16)),
            "ahash": str(imagehash.average_hash(candidate, hash_size=16)),
        }
    return {
        "sha256": hashlib.sha256(body).hexdigest(),
        "width": int(width),
        "height": int(height),
        "variants": variants,
    }


def _distance(left: dict, right: dict) -> dict:
    components = {
        key: int(imagehash.hex_to_hash(left[key]) - imagehash.hex_to_hash(right[key]))
        for key in HASH_KEYS
    }
    ordered = sorted(components.values())
    return {
        **components,
        "sum": int(sum(components.values())),
        "max": int(max(components.values())),
        "sum_best3": int(sum(ordered[:3])),
    }


def _best_geometry(left: dict, right: dict) -> dict:
    candidates = []
    for left_crop, left_fp in left["variants"].items():
        for right_crop, right_fp in right["variants"].items():
            distance = _distance(left_fp, right_fp)
            candidates.append(
                {
                    "market_crop": left_crop,
                    "official_crop": right_crop,
                    "distance": distance,
                }
            )
    candidates.sort(
        key=lambda row: (
            row["distance"]["sum"],
            row["distance"]["max"],
            row["distance"]["sum_best3"],
            row["market_crop"],
            row["official_crop"],
        )
    )
    return candidates[0]


def _official_inventory(pdf_bytes: bytes) -> tuple[list[dict], dict]:
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if pdf_sha != v2.EXPECTED_PDF_SHA256:
        raise AssertionError({"official_pdf_sha256": pdf_sha, "expected": v2.EXPECTED_PDF_SHA256})
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if len(reader.pages) != v2.EXPECTED_PAGES:
        raise AssertionError({"pages": len(reader.pages), "expected": v2.EXPECTED_PAGES})

    raw = []
    for page_number, page in enumerate(reader.pages, start=1):
        for image in list(page.images):
            body = image.data
            raw.append(
                {
                    "page_number": page_number,
                    "image_object": str(getattr(image, "name", "")),
                    "body": body,
                    "image_sha256": hashlib.sha256(body).hexdigest(),
                }
            )

    repeated = {}
    for row in raw:
        repeated[row["image_sha256"]] = repeated.get(row["image_sha256"], 0) + 1
    furniture = {sha for sha, count in repeated.items() if count == v2.EXPECTED_PAGES}
    if len(furniture) != 1:
        raise AssertionError({"page_furniture_hashes": sorted(furniture)})

    items = [row for row in raw if row["image_sha256"] not in furniture]
    if len(items) != v2.EXPECTED_ITEMS or len({row["image_sha256"] for row in items}) != v2.EXPECTED_ITEMS:
        raise AssertionError(
            {
                "official_items": len(items),
                "unique": len({row["image_sha256"] for row in items}),
            }
        )

    page_slots = {}
    for sequence, row in enumerate(items, start=1):
        page_slots[row["page_number"]] = page_slots.get(row["page_number"], 0) + 1
        row["sequence_number"] = sequence
        row["slot_number"] = page_slots[row["page_number"]]
        row["fingerprints"] = _fingerprints(row.pop("body"))

    return items, {
        "pdf_sha256": pdf_sha,
        "pages": len(reader.pages),
        "items": len(items),
        "crop_pcts": list(CROP_PCTS),
    }


def _market_download(row: dict) -> tuple[dict, dict]:
    product_id = str(row["representative_external_product_id"] or "")
    category_id = str(row.get("category_id") or "")
    expansion_id = str(row.get("expansion_external_id") or "")
    token = v3.CERTIFIED_EXPANSION_TOKENS.get(expansion_id)
    if not product_id.isdigit() or not category_id.isdigit() or not token:
        raise ValueError("Cardmarket image path requires certified category/product/expansion identity")
    url = (
        f"https://{v2.CARDMARKET_IMAGE_HOST}/"
        f"{category_id}/{token}/{product_id}/{product_id}.jpg"
    )
    enriched = dict(row)
    enriched["cardmarket_image_token"] = token
    enriched["cardmarket_image_url"] = url
    return enriched, _fingerprints(v2._download(url))


def _rank(row: dict, market_fp: dict, official: list[dict]) -> dict:
    ranked = []
    for item in official:
        geometry = _best_geometry(market_fp, item["fingerprints"])
        ranked.append(
            {
                "sequence_number": item["sequence_number"],
                "page_number": item["page_number"],
                "slot_number": item["slot_number"],
                "image_object": item["image_object"],
                "official_image_sha256": item["image_sha256"],
                **geometry,
            }
        )
    ranked.sort(
        key=lambda candidate: (
            candidate["distance"]["sum"],
            candidate["distance"]["max"],
            candidate["distance"]["sum_best3"],
            candidate["sequence_number"],
        )
    )
    best, second = ranked[0], ranked[1]
    return {
        "metacard_external_id": row["metacard_external_id"],
        "product_id": str(row["representative_external_product_id"]),
        "name": row["name"],
        "subject": row.get("subject"),
        "subject_normalized": row.get("subject_normalized"),
        "product_count": row["product_count"],
        "source_as_of": row["source_as_of"],
        "expansion_external_id": str(row.get("expansion_external_id") or ""),
        "cardmarket_image_token": row.get("cardmarket_image_token"),
        "cardmarket_image_url": row.get("cardmarket_image_url"),
        "cardmarket_image_sha256": market_fp["sha256"],
        "cardmarket_image_dimensions": [market_fp["width"], market_fp["height"]],
        "best": best,
        "second_best": second,
        "distance_margin": int(second["distance"]["sum"] - best["distance"]["sum"]),
    }


def _persist(report: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    best_sums = sorted(row["best"]["distance"]["sum"] for row in report["matches"])
    margins = sorted(row["distance_margin"] for row in report["matches"])
    print(
        json.dumps(
            {
                "status": report["status"],
                "production_writes": 0,
                "transaction_read_only": report["transaction_read_only"],
                "official_items": report["official"]["items"],
                "market_rows": report["market_rows"],
                "fetched_images": report["fetched_images"],
                "unavailable_images": len(report["unavailable_images"]),
                "best_sum_min": best_sums[0] if best_sums else None,
                "best_sum_median": best_sums[len(best_sums) // 2] if best_sums else None,
                "margin_median": margins[len(margins) // 2] if margins else None,
                "top20": [
                    {
                        "idProduct": row["product_id"],
                        "name": row["name"],
                        "best_sequence": row["best"]["sequence_number"],
                        "sum": row["best"]["distance"]["sum"],
                        "max": row["best"]["distance"]["max"],
                        "sum_best3": row["best"]["distance"]["sum_best3"],
                        "margin": row["distance_margin"],
                        "market_crop": row["best"]["market_crop"],
                        "official_crop": row["best"]["official_crop"],
                    }
                    for row in sorted(
                        report["matches"],
                        key=lambda r: (
                            r["best"]["distance"]["sum"],
                            -r["distance_margin"],
                            r["product_id"],
                        ),
                    )[:20]
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main() -> int:
    db_url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    official, official_meta = _official_inventory(v2._download(v2.OFFICIAL_URL, official=True))
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "production_writes": 0,
        "transaction_read_only": False,
        "official": official_meta,
        "market_rows": 0,
        "fetched_images": 0,
        "unavailable_images": [],
        "matches": [],
    }
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='12s'")
            cur.execute("SHOW transaction_read_only")
            report["transaction_read_only"] = cur.fetchone()["transaction_read_only"] == "on"
            if not report["transaction_read_only"]:
                raise AssertionError("geometry calibration is not read-only")
            market = v3._market_rows(cur)
            report["market_rows"] = len(market)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_market_download, row): row for row in market}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    source_row, market_fp = future.result()
                    report["matches"].append(_rank(source_row, market_fp, official))
                    report["fetched_images"] += 1
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
                    report["unavailable_images"].append(
                        {
                            "metacard_external_id": row["metacard_external_id"],
                            "product_id": str(row["representative_external_product_id"]),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        conn.rollback()
    finally:
        conn.close()

    report["matches"].sort(key=lambda row: str(row["metacard_external_id"]))
    report["unavailable_images"].sort(key=lambda row: str(row["metacard_external_id"]))
    report["status"] = "pass" if report["fetched_images"] == 161 and len(report["unavailable_images"]) == 3 else "blocked"
    _persist(report)

    if report["status"] != "pass":
        raise AssertionError(
            {
                "market_rows": report["market_rows"],
                "fetched_images": report["fetched_images"],
                "unavailable_images": len(report["unavailable_images"]),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
