from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import imagehash
import psycopg2
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg2.extras import RealDictCursor
from pypdf import PdfReader

OFFICIAL_URL = os.getenv(
    "ONEPIECE_DON_OFFICIAL_URL",
    "https://onepiece-cardgame.com/pdf/don-cardlist.pdf?v=260227",
)
EXPECTED_PDF_SHA256 = "cd518a04ea3ff1acdc1f3bc824ad53d0ca17d8ee2fd0a6427717e0bdaacbdfe0"
EXPECTED_PAGES = 30
EXPECTED_ITEMS = 262
CARDMARKET_IMAGE_HOST = "product-images.s3.cardmarket.com"
CARDMARKET_REFERER = "https://www.cardmarket.com/"
# Keep these identical to app.routes.product_media. A custom crawler UA is
# rejected by Cardmarket's image CDN even though the same public image is
# available to the production browser-compatible proxy.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
OUTPUT = Path(os.getenv("ONEPIECE_DON_IMAGE_AUDIT_OUTPUT", "artifacts/onepiece-don-image-crosswalk-v2.json"))
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_WORKERS = 12
HASH_KEYS = ("phash", "dhash", "whash", "ahash")


def _download(url: str, *, official: bool = False) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"refusing non-https URL: {url}")
    if official and not (parsed.hostname or "").endswith("onepiece-cardgame.com"):
        raise RuntimeError(f"refusing non-official source URL: {url}")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,*/*;q=0.8" if official else "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if not official:
        headers["Referer"] = CARDMARKET_REFERER
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20 if not official else 90) as response:
        final = urlparse(response.geturl())
        if official:
            if not (final.hostname or "").endswith("onepiece-cardgame.com"):
                raise RuntimeError("unexpected official-source redirect")
            body = response.read(80_000_001)
            if len(body) > 80_000_000 or not body.startswith(b"%PDF-"):
                raise RuntimeError("invalid official PDF response")
        else:
            if final.scheme != "https" or final.hostname != CARDMARKET_IMAGE_HOST:
                raise RuntimeError("unexpected Cardmarket image redirect")
            body = response.read(MAX_IMAGE_BYTES + 1)
            if not body or len(body) > MAX_IMAGE_BYTES:
                raise RuntimeError("invalid Cardmarket image size")
    return body


def _fingerprint(body: bytes) -> dict:
    try:
        with Image.open(io.BytesIO(body)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            return {
                "sha256": hashlib.sha256(body).hexdigest(),
                "width": int(width),
                "height": int(height),
                "phash": str(imagehash.phash(image, hash_size=16)),
                "dhash": str(imagehash.dhash(image, hash_size=16)),
                "whash": str(imagehash.whash(image, hash_size=16)),
                "ahash": str(imagehash.average_hash(image, hash_size=16)),
            }
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RuntimeError("response does not decode as an image") from exc


def _distance(left: dict, right: dict) -> dict:
    values = {
        key: int(imagehash.hex_to_hash(left[key]) - imagehash.hex_to_hash(right[key]))
        for key in HASH_KEYS
    }
    values["sum"] = sum(values.values())
    values["max"] = max(values.values())
    return values


def _official_inventory(pdf_bytes: bytes) -> tuple[list[dict], dict]:
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if pdf_sha != EXPECTED_PDF_SHA256:
        raise AssertionError({"official_pdf_sha256": pdf_sha, "expected": EXPECTED_PDF_SHA256})
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    if len(reader.pages) != EXPECTED_PAGES:
        raise AssertionError({"pages": len(reader.pages), "expected": EXPECTED_PAGES})

    raw: list[dict] = []
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
    repeated: dict[str, int] = {}
    for row in raw:
        repeated[row["image_sha256"]] = repeated.get(row["image_sha256"], 0) + 1
    furniture = {sha for sha, count in repeated.items() if count == EXPECTED_PAGES}
    if len(furniture) != 1:
        raise AssertionError({"page_furniture_hashes": sorted(furniture)})
    items = [row for row in raw if row["image_sha256"] not in furniture]
    if len(items) != EXPECTED_ITEMS or len({row["image_sha256"] for row in items}) != EXPECTED_ITEMS:
        raise AssertionError({"official_items": len(items), "unique": len({row['image_sha256'] for row in items})})

    page_slots: dict[int, int] = {}
    for sequence, row in enumerate(items, start=1):
        page_slots[row["page_number"]] = page_slots.get(row["page_number"], 0) + 1
        row["sequence_number"] = sequence
        row["slot_number"] = page_slots[row["page_number"]]
        row["fingerprint"] = _fingerprint(row.pop("body"))
    return items, {"pdf_sha256": pdf_sha, "pages": len(reader.pages), "items": len(items)}


def _market_rows(cur) -> list[dict]:
    cur.execute(
        """
        SELECT
          m.metacard_external_id,
          m.representative_external_product_id,
          m.name,
          m.subject,
          m.subject_normalized,
          m.product_count,
          m.source_as_of,
          e.raw_json->>'category_id' AS category_id
        FROM onepiece_don_market_items m
        JOIN games g ON g.slug='onepiece'
        JOIN external_catalog_products e
          ON e.source='cardmarket'
         AND e.game_id=g.id
         AND e.external_id=m.representative_external_product_id
        WHERE m.source='cardmarket'
          AND m.source_as_of=(SELECT max(source_as_of) FROM onepiece_don_market_items WHERE source='cardmarket')
        ORDER BY m.metacard_external_id
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    if len(rows) < 150:
        raise AssertionError({"market_rows": len(rows), "minimum": 150})
    return rows


def _market_image(row: dict) -> tuple[dict, dict]:
    product_id = str(row["representative_external_product_id"] or "")
    category_id = str(row.get("category_id") or "")
    if not product_id.isdigit() or not category_id.isdigit():
        raise ValueError("Cardmarket image path requires numeric category/product IDs")
    url = f"https://{CARDMARKET_IMAGE_HOST}/{category_id}/{product_id}/{product_id}.jpg"
    return row, _fingerprint(_download(url))


def _rank(row: dict, market_fp: dict, official: list[dict]) -> dict:
    ranked = []
    for item in official:
        dist = _distance(market_fp, item["fingerprint"])
        ranked.append(
            {
                "sequence_number": item["sequence_number"],
                "page_number": item["page_number"],
                "slot_number": item["slot_number"],
                "image_object": item["image_object"],
                "official_image_sha256": item["image_sha256"],
                "distance": dist,
            }
        )
    ranked.sort(key=lambda candidate: (candidate["distance"]["sum"], candidate["distance"]["max"], candidate["sequence_number"]))
    best, second = ranked[0], ranked[1]
    margin = second["distance"]["sum"] - best["distance"]["sum"]
    proposed_exact = (
        best["distance"]["sum"] <= 24
        and best["distance"]["max"] <= 8
        and margin >= 16
    )
    return {
        "metacard_external_id": row["metacard_external_id"],
        "product_id": str(row["representative_external_product_id"]),
        "category_id": str(row.get("category_id") or ""),
        "name": row["name"],
        "subject": row.get("subject"),
        "subject_normalized": row.get("subject_normalized"),
        "product_count": row["product_count"],
        "source_as_of": row["source_as_of"],
        "cardmarket_image_sha256": market_fp["sha256"],
        "cardmarket_image_dimensions": [market_fp["width"], market_fp["height"]],
        "best": best,
        "second_best": second,
        "distance_margin": margin,
        "proposed_exact": proposed_exact,
    }


def main() -> int:
    db_url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    official, official_meta = _official_inventory(_download(OFFICIAL_URL, official=True))
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
        "exact_candidates": [],
    }
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='12s'")
            cur.execute("SHOW transaction_read_only")
            report["transaction_read_only"] = cur.fetchone()["transaction_read_only"] == "on"
            if not report["transaction_read_only"]:
                raise AssertionError("crosswalk auditor is not read-only")
            market = _market_rows(cur)
            report["market_rows"] = len(market)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_market_image, row): row for row in market}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    source_row, fp = future.result()
                    result = _rank(source_row, fp, official)
                    report["fetched_images"] += 1
                    report["matches"].append(result)
                    if result["proposed_exact"]:
                        report["exact_candidates"].append(result)
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
                    report["unavailable_images"].append(
                        {
                            "metacard_external_id": row["metacard_external_id"],
                            "product_id": str(row["representative_external_product_id"]),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        report["matches"].sort(key=lambda row: str(row["metacard_external_id"]))
        report["exact_candidates"].sort(key=lambda row: str(row["metacard_external_id"]))
        report["unavailable_images"].sort(key=lambda row: str(row["metacard_external_id"]))
        report["exact_candidate_count"] = len(report["exact_candidates"])
        report["status"] = "pass"
        conn.rollback()
    finally:
        conn.close()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "transaction_read_only": report["transaction_read_only"],
        "official_items": report["official"]["items"],
        "market_rows": report["market_rows"],
        "fetched_images": report["fetched_images"],
        "unavailable_images": len(report["unavailable_images"]),
        "exact_candidate_count": report["exact_candidate_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
