from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.error
import urllib.request
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
USER_AGENT = "Mozilla/5.0 (compatible; DonTripItCatalogAudit/2.0; +https://dontripit.com)"
OUTPUT = Path(os.getenv("ONEPIECE_DON_IMAGE_AUDIT_OUTPUT", "artifacts/onepiece-don-image-crosswalk-v2.json"))
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _download(url: str, *, official: bool = False) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"refusing non-https URL: {url}")
    if official and not (parsed.hostname or "").endswith("onepiece-cardgame.com"):
        raise RuntimeError(f"refusing non-official source URL: {url}")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,*/*;q=0.8" if official else "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
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
            if final.hostname != CARDMARKET_IMAGE_HOST:
                raise RuntimeError("unexpected Cardmarket image redirect")
            body = response.read(MAX_IMAGE_BYTES + 1)
            if not body or len(body) > MAX_IMAGE_BYTES:
                raise RuntimeError("invalid Cardmarket image size")
    return body


def _image_fingerprint(body: bytes) -> dict:
    try:
        with Image.open(io.BytesIO(body)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            # Hashes use different transforms so agreement is much stronger than
            # relying on a single perceptual metric.  The fitted thumbnail is
            # only a secondary diagnostic; it is never sufficient for mapping.
            fitted = ImageOps.fit(image, (96, 132), method=Image.Resampling.LANCZOS)
            gray = ImageOps.grayscale(fitted)
            pixels = list(gray.getdata())
            mean = sum(pixels) / len(pixels)
            mae_from_mean = sum(abs(p - mean) for p in pixels) / len(pixels)
            return {
                "sha256": hashlib.sha256(body).hexdigest(),
                "width": int(width),
                "height": int(height),
                "phash": str(imagehash.phash(image, hash_size=16)),
                "dhash": str(imagehash.dhash(image, hash_size=16)),
                "whash": str(imagehash.whash(image, hash_size=16)),
                "ahash": str(imagehash.average_hash(image, hash_size=16)),
                "luma_mean": round(mean, 4),
                "luma_mae": round(mae_from_mean, 4),
            }
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RuntimeError("response does not decode as an image") from exc


def _distance(a: dict, b: dict) -> dict:
    distances = {
        key: int(imagehash.hex_to_hash(a[key]) - imagehash.hex_to_hash(b[key]))
        for key in ("phash", "dhash", "whash", "ahash")
    }
    distances["sum"] = sum(distances.values())
    distances["max"] = max(distances.values())
    return distances


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

    counts: dict[str, int] = {}
    for row in raw:
        counts[row["image_sha256"]] = counts.get(row["image_sha256"], 0) + 1
    furniture = {sha for sha, count in counts.items() if count == EXPECTED_PAGES}
    if len(furniture) != 1:
        raise AssertionError({"page_furniture_hashes": sorted(furniture)})
    items = [row for row in raw if row["image_sha256"] not in furniture]
    if len(items) != EXPECTED_ITEMS or len({r["image_sha256"] for r in items}) != EXPECTED_ITEMS:
        raise AssertionError({"official_items": len(items), "unique": len({r['image_sha256'] for r in items})})

    page_slots: dict[int, int] = {}
    for sequence, row in enumerate(items, start=1):
        page_slots[row["page_number"]] = page_slots.get(row["page_number"], 0) + 1
        row["sequence_number"] = sequence
        row["slot_number"] = page_slots[row["page_number"]]
        row["fingerprint"] = _image_fingerprint(row.pop("body"))
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


def _cardmarket_image_url(category_id: str, product_id: str) -> str:
    if not str(category_id or "").isdigit() or not str(product_id or "").isdigit():
        raise ValueError("Cardmarket image path requires numeric category/product IDs")
    return f"https://{CARDMARKET_IMAGE_HOST}/{category_id}/{product_id}/{product_id}.jpg"


def main() -> int:
    db_url = (os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    official, official_meta = _official_inventory(_download(OFFICIAL_URL, official=True))
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    report: dict = {
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

        for idx, row in enumerate(market, start=1):
            product_id = str(row["representative_external_product_id"])
            category_id = str(row.get("category_id") or "")
            try:
                image_url = _cardmarket_image_url(category_id, product_id)
                body = _download(image_url)
                fp = _image_fingerprint(body)
                report["fetched_images"] += 1
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
                report["unavailable_images"].append(
                    {"metacard_external_id": row["metacard_external_id"], "product_id": product_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue

            ranked = []
            for item in official:
                dist = _distance(fp, item["fingerprint"])
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
            best = ranked[0]
            second = ranked[1]
            margin = second["distance"]["sum"] - best["distance"]["sum"]
            # Intentionally strict. A candidate must agree across four independent
            # 256-bit perceptual hashes and have a meaningful margin over the next
            # official image. This script proposes links; it never writes them.
            proposed_exact = (
                best["distance"]["sum"] <= 24
                and best["distance"]["max"] <= 8
                and margin >= 16
            )
            result = {
                "metacard_external_id": row["metacard_external_id"],
                "product_id": product_id,
                "category_id": category_id,
                "name": row["name"],
                "subject": row.get("subject"),
                "subject_normalized": row.get("subject_normalized"),
                "product_count": row["product_count"],
                "source_as_of": row["source_as_of"],
                "cardmarket_image_sha256": fp["sha256"],
                "cardmarket_image_dimensions": [fp["width"], fp["height"]],
                "best": best,
                "second_best": second,
                "distance_margin": margin,
                "proposed_exact": proposed_exact,
            }
            report["matches"].append(result)
            if proposed_exact:
                report["exact_candidates"].append(result)

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
