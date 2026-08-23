from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text

from app import db
from app.onepiece_don_media import cardmarket_don_source_url


product_media_bp = Blueprint("product_media", __name__)

_CARDMARKET_IMAGE_HOST = "product-images.s3.cardmarket.com"
_CARDMARKET_REFERER = "https://www.cardmarket.com/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MIN_IMAGE_DIMENSION = 80
_MAX_IMAGE_DIMENSION = 12_000


_EXACT_CARDMARKET_VARIANT_SQL = text(
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
        e.external_id,
        e.category_id,
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
    SELECT product_variant_id, external_id, category_id
    FROM strict
    WHERE product_variant_id = :variant_id
      AND variants_per_external = 1
      AND externals_per_variant = 1
    LIMIT 2
    """
)


_CURRENT_ONEPIECE_DON_SOURCE_SQL = text(
    """
    WITH latest_source AS (
      SELECT MAX(source_as_of) AS source_as_of
      FROM onepiece_don_market_items
      WHERE source = 'cardmarket'
    )
    SELECT
      m.metacard_external_id,
      m.representative_external_product_id,
      e.category_id,
      e.expansion_external_id
    FROM onepiece_don_market_items m
    JOIN latest_source latest ON latest.source_as_of = m.source_as_of
    JOIN games g ON g.slug = 'onepiece'
    JOIN external_catalog_products e
      ON e.source = 'cardmarket'
     AND e.game_id = g.id
     AND e.product_group = 'single'
     AND e.external_id = m.representative_external_product_id
    WHERE m.source = 'cardmarket'
      AND m.metacard_external_id = :metacard_id
    LIMIT 2
    """
)


def _exact_cardmarket_source(variant_id: int) -> dict | None:
    with db.SessionLocal() as session:
        rows = session.execute(
            _EXACT_CARDMARKET_VARIANT_SQL,
            {"variant_id": variant_id},
        ).mappings().all()
    if len(rows) != 1:
        return None
    row = dict(rows[0])
    product_id = str(row.get("external_id") or "").strip()
    category_id = str(row.get("category_id") or "").strip()
    if not product_id.isdigit() or not category_id.isdigit():
        return None
    return {
        "product_variant_id": int(row["product_variant_id"]),
        "product_id": product_id,
        "category_id": category_id,
    }


def _exact_onepiece_don_source(metacard_id: str) -> dict | None:
    source_id = str(metacard_id or "").strip()
    if not source_id.isdigit():
        return None

    with db.SessionLocal() as session:
        rows = session.execute(
            _CURRENT_ONEPIECE_DON_SOURCE_SQL,
            {"metacard_id": source_id},
        ).mappings().all()
    if len(rows) != 1:
        return None

    row = dict(rows[0])
    product_id = str(row.get("representative_external_product_id") or "").strip()
    category_id = str(row.get("category_id") or "").strip()
    expansion_id = str(row.get("expansion_external_id") or "").strip()
    source_url = cardmarket_don_source_url(
        category_id=category_id,
        expansion_external_id=expansion_id,
        product_id=product_id,
    )
    if source_url is None:
        return None

    return {
        "metacard_external_id": source_id,
        "product_id": product_id,
        "category_id": category_id,
        "expansion_external_id": expansion_id,
        "source_url": source_url,
    }


def _cardmarket_url(category_id: str, product_id: str) -> str:
    if not str(category_id).isdigit() or not str(product_id).isdigit():
        raise ValueError("Cardmarket sealed image path requires numeric source IDs")
    return f"https://{_CARDMARKET_IMAGE_HOST}/{category_id}/{product_id}/{product_id}.jpg"


def _detect_image_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validated_dimensions(body: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("upstream response does not decode as a valid image") from exc

    if not (
        _MIN_IMAGE_DIMENSION <= int(width) <= _MAX_IMAGE_DIMENSION
        and _MIN_IMAGE_DIMENSION <= int(height) <= _MAX_IMAGE_DIMENSION
    ):
        raise ValueError("upstream image dimensions are outside the allowed range")
    return int(width), int(height)


def _fetch_exact_image(
    url: str,
    timeout: int = 12,
) -> tuple[bytes, str, int, int, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": _CARDMARKET_REFERER,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as upstream:
        if int(getattr(upstream, "status", 200) or 200) != 200:
            raise urllib.error.HTTPError(
                url,
                int(upstream.status),
                "unexpected status",
                upstream.headers,
                None,
            )
        final = urlparse(upstream.geturl())
        if final.scheme != "https" or final.hostname != _CARDMARKET_IMAGE_HOST:
            raise ValueError("unexpected upstream redirect")
        body = upstream.read(_MAX_IMAGE_BYTES + 1)

    if not body or len(body) > _MAX_IMAGE_BYTES:
        raise ValueError("invalid upstream image size")
    content_type = _detect_image_type(body)
    if content_type is None:
        raise ValueError("upstream response is not a supported image")
    width, height = _validated_dimensions(body)
    digest = hashlib.sha256(body).hexdigest()
    return body, content_type, width, height, digest


def _unavailable(status: int = 404):
    response = jsonify({"error": "image_not_available"})
    response.status_code = status
    if status == 404:
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["CDN-Cache-Control"] = "public, s-maxage=86400"
        response.headers["Vercel-CDN-Cache-Control"] = "public, s-maxage=86400"
    else:
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _image_response(
    body: bytes,
    content_type: str,
    width: int,
    height: int,
    digest: str,
    *,
    source: str,
) -> Response:
    response = Response(body, status=200, content_type=content_type)
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["CDN-Cache-Control"] = "public, s-maxage=604800, stale-while-revalidate=2592000"
    response.headers["Vercel-CDN-Cache-Control"] = "public, s-maxage=604800, stale-while-revalidate=2592000"
    response.headers["ETag"] = f'"sha256-{digest}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["X-Image-Source"] = source
    response.headers["X-Image-Width"] = str(width)
    response.headers["X-Image-Height"] = str(height)
    return response


@product_media_bp.get("/media/product-variants/<int:variant_id>/cardmarket-image")
def exact_cardmarket_product_image(variant_id: int):
    source = _exact_cardmarket_source(variant_id)
    if source is None:
        return _unavailable(404)

    url = _cardmarket_url(source["category_id"], source["product_id"])
    try:
        body, content_type, width, height, digest = _fetch_exact_image(url)
    except urllib.error.HTTPError as error:
        if error.code in {401, 403, 404}:
            return _unavailable(404)
        return _unavailable(503)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return _unavailable(503)

    return _image_response(
        body,
        content_type,
        width,
        height,
        digest,
        source="cardmarket-exact-proxy-v1",
    )


@product_media_bp.get("/media/onepiece/don/<metacard_id>/cardmarket-image")
def exact_cardmarket_onepiece_don_image(metacard_id: str):
    """Proxy the exact current Cardmarket image for a source-owned DON metacard.

    The route intentionally resolves only the current representative source
    product. It does not imply or create canonical Card/Print identity.
    """
    source = _exact_onepiece_don_source(metacard_id)
    if source is None:
        return _unavailable(404)

    try:
        body, content_type, width, height, digest = _fetch_exact_image(source["source_url"])
    except urllib.error.HTTPError as error:
        if error.code in {401, 403, 404}:
            return _unavailable(404)
        return _unavailable(503)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return _unavailable(503)

    return _image_response(
        body,
        content_type,
        width,
        height,
        digest,
        source="cardmarket-don-source-proxy-v1",
    )
