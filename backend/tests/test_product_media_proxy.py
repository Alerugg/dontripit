from __future__ import annotations

import io
import urllib.error

from flask import Flask
from PIL import Image
from sqlalchemy import create_engine, text

from app.routes import product_media
from app.scripts import sync_sealed_cardmarket_proxy_images_v1 as sync


def _jpeg(width: int = 320, height: int = 240) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_cardmarket_url_requires_exact_numeric_source_ids():
    assert (
        product_media._cardmarket_url("19", "855004")
        == "https://product-images.s3.cardmarket.com/19/855004/855004.jpg"
    )

    for category_id, product_id in [("", "855004"), ("19", "abc"), ("x", "1")]:
        try:
            product_media._cardmarket_url(category_id, product_id)
        except ValueError:
            pass
        else:
            raise AssertionError("non-numeric Cardmarket image identity must fail closed")


def test_image_decoder_rejects_non_images_and_tiny_assets():
    try:
        product_media._validated_dimensions(b"not-an-image")
    except ValueError:
        pass
    else:
        raise AssertionError("non-image bytes must fail")

    try:
        product_media._validated_dimensions(_jpeg(20, 20))
    except ValueError:
        pass
    else:
        raise AssertionError("tiny assets must fail")

    assert product_media._validated_dimensions(_jpeg(320, 240)) == (320, 240)


def test_proxy_serves_only_validated_exact_bytes(monkeypatch):
    body = _jpeg()
    monkeypatch.setattr(
        product_media,
        "_exact_cardmarket_source",
        lambda variant_id: {
            "product_variant_id": variant_id,
            "product_id": "855004",
            "category_id": "19",
        },
    )
    monkeypatch.setattr(
        product_media,
        "_fetch_exact_image",
        lambda url: (body, "image/jpeg", 320, 240, "a" * 64),
    )

    app = Flask(__name__)
    app.register_blueprint(product_media.product_media_bp)
    response = app.test_client().get("/media/product-variants/36155/cardmarket-image")

    assert response.status_code == 200
    assert response.data == body
    assert response.headers["X-Image-Source"] == "cardmarket-exact-proxy-v1"
    assert response.headers["X-Image-Width"] == "320"
    assert response.headers["X-Image-Height"] == "240"
    assert response.headers["ETag"] == '"sha256-' + ("a" * 64) + '"'
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_proxy_maps_upstream_403_to_cacheable_not_available(monkeypatch):
    monkeypatch.setattr(
        product_media,
        "_exact_cardmarket_source",
        lambda variant_id: {
            "product_variant_id": variant_id,
            "product_id": "710617",
            "category_id": "7",
        },
    )

    def blocked(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(product_media, "_fetch_exact_image", blocked)

    app = Flask(__name__)
    app.register_blueprint(product_media.product_media_bp)
    response = app.test_client().get("/media/product-variants/35143/cardmarket-image")

    assert response.status_code == 404
    assert response.get_json() == {"error": "image_not_available"}
    assert "s-maxage=86400" in response.headers["CDN-Cache-Control"]


def test_probe_classifies_recoverable_and_blocked(monkeypatch):
    row = {"variant_id": 1, "external_id": "855004", "category_id": "19"}
    monkeypatch.setattr(
        sync,
        "_fetch_exact_image",
        lambda url, timeout=12: (_jpeg(), "image/jpeg", 320, 240, "b" * 64),
    )
    recovered = sync._probe_row(row, timeout=12, retries=0)
    assert recovered.status == "recoverable_exact"
    assert recovered.sha256 == "b" * 64

    def forbidden(url, timeout=12):
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(sync, "_fetch_exact_image", forbidden)
    blocked = sync._probe_row(row, timeout=12, retries=0)
    assert blocked.status == "blocked"


def test_manifest_apply_is_idempotent(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE product_images (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  product_variant_id INTEGER NOT NULL,
                  url TEXT NOT NULL,
                  is_primary BOOLEAN NOT NULL,
                  source TEXT
                )
                """
            )
        )

    monkeypatch.setattr(sync, "_current_exact_identity", lambda engine: {7: ("855004", "19")})
    manifest = [
        {
            "variant_id": 7,
            "external_id": "855004",
            "category_id": "19",
        }
    ]

    first = sync.apply_manifest(engine, manifest, public_base_url="https://api.dontripit.com")
    second = sync.apply_manifest(engine, manifest, public_base_url="https://api.dontripit.com")

    assert first["inserted"] == 1
    assert first["material_writes"] == 1
    assert second["inserted"] == 0
    assert second["material_writes"] == 0

    with engine.connect() as conn:
        row = conn.execute(text("SELECT url, source, is_primary FROM product_images")).mappings().one()
    assert row["url"] == "https://api.dontripit.com/media/product-variants/7/cardmarket-image"
    assert row["source"] == sync.SOURCE
    assert bool(row["is_primary"]) is True


# Operational marker: fresh sealed read-only audit trigger, 2026-08-30.
