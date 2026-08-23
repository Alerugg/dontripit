from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

from flask import Flask
from PIL import Image

from app.onepiece_don_media import (
    cardmarket_don_source_url,
    onepiece_don_proxy_path,
    onepiece_don_proxy_url,
)
from app.routes import product_media
from app.search_v2.onepiece_don_query import onepiece_don_market_page


def _jpeg(width: int = 320, height: int = 448) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_certified_don_source_url_requires_known_expansion_token():
    assert cardmarket_don_source_url(
        category_id="1621",
        expansion_external_id="6492",
        product_id="904161",
    ) == "https://product-images.s3.cardmarket.com/1621/OP17/904161/904161.jpg"

    assert cardmarket_don_source_url(
        category_id="1621",
        expansion_external_id="5244",
        product_id="732272",
    ) == "https://product-images.s3.cardmarket.com/1621/OPPR/732272/732272.jpg"

    assert cardmarket_don_source_url(
        category_id="1621",
        expansion_external_id="999999",
        product_id="904161",
    ) is None
    assert cardmarket_don_source_url(
        category_id="not-numeric",
        expansion_external_id="6492",
        product_id="904161",
    ) is None


def test_source_owned_don_proxy_path_is_metacard_scoped_and_public():
    assert (
        onepiece_don_proxy_path("467133")
        == "/media/onepiece/don/467133/cardmarket-image"
    )
    assert (
        onepiece_don_proxy_url(
            "467133",
            expansion_external_id="6492",
            public_base_url="https://api.dontripit.com/",
        )
        == "https://api.dontripit.com/media/onepiece/don/467133/cardmarket-image"
    )
    assert onepiece_don_proxy_path("not-a-metacard") is None
    assert onepiece_don_proxy_url(
        "467133",
        expansion_external_id="999999",
        public_base_url="https://api.dontripit.com",
    ) is None
    assert onepiece_don_proxy_url(
        "467133",
        expansion_external_id="6492",
        public_base_url="http://unsafe.invalid",
    ) is None


def test_don_proxy_serves_validated_current_source_image(monkeypatch):
    body = _jpeg()
    source_url = "https://product-images.s3.cardmarket.com/1621/OP17/904161/904161.jpg"
    monkeypatch.setattr(
        product_media,
        "_exact_onepiece_don_source",
        lambda metacard_id: {
            "metacard_external_id": str(metacard_id),
            "product_id": "904161",
            "category_id": "1621",
            "expansion_external_id": "6492",
            "source_url": source_url,
        },
    )
    seen = []

    def fetch(url):
        seen.append(url)
        return body, "image/jpeg", 320, 448, "c" * 64

    monkeypatch.setattr(product_media, "_fetch_exact_image", fetch)

    app = Flask(__name__)
    app.register_blueprint(product_media.product_media_bp)
    response = app.test_client().get("/media/onepiece/don/467133/cardmarket-image")

    assert response.status_code == 200
    assert response.data == body
    assert seen == [source_url]
    assert response.headers["X-Image-Source"] == "cardmarket-don-source-proxy-v1"
    assert response.headers["X-Image-Width"] == "320"
    assert response.headers["X-Image-Height"] == "448"
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_don_proxy_fails_closed_for_unavailable_upstream(monkeypatch):
    source_url = "https://product-images.s3.cardmarket.com/1621/OP17/904163/904163.jpg"
    monkeypatch.setattr(
        product_media,
        "_exact_onepiece_don_source",
        lambda metacard_id: {
            "metacard_external_id": str(metacard_id),
            "product_id": "904163",
            "category_id": "1621",
            "expansion_external_id": "6492",
            "source_url": source_url,
        },
    )

    def blocked(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(product_media, "_fetch_exact_image", blocked)

    app = Flask(__name__)
    app.register_blueprint(product_media.product_media_bp)
    response = app.test_client().get("/media/onepiece/don/467134/cardmarket-image")

    assert response.status_code == 404
    assert response.get_json() == {"error": "image_not_available"}
    assert "s-maxage=86400" in response.headers["CDN-Cache-Control"]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _PostgresSession:
    def __init__(self, rows):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.rows = rows

    def execute(self, statement, params):
        return _Rows(self.rows)


def _search_row(expansion_external_id="6492"):
    return {
        "id": 1,
        "metacard_external_id": "467133",
        "representative_external_product_id": "904161",
        "representative_expansion_external_id": expansion_external_id,
        "name": "Don!! (OP17 Luffy & Loki)",
        "subject": "Luffy & Loki",
        "subject_normalized": "luffy & loki",
        "product_ids_json": ["904161"],
        "product_count": 1,
        "source_as_of": "2026-08-23T09:30:54Z",
        "official_item_id": None,
        "mapping_source": None,
        "mapping_confidence": None,
        "total": 1,
        "cardmarket_id_product": "904161",
        "cardmarket_product_name": "Don!! (OP17 Luffy & Loki)",
        "cardmarket_website_path": "/OnePiece/Products?idProduct=904161",
        "cardmarket_category_id": 1621,
        "cardmarket_price": 5.0,
        "cardmarket_price_variant": "avg",
        "cardmarket_currency": "EUR",
        "cardmarket_as_of": "2026-08-23T00:44:42Z",
    }


def test_don_search_uses_owned_proxy_not_direct_cardmarket_hotlink(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.dontripit.com")
    page = onepiece_don_market_page(
        _PostgresSession([_search_row()]),
        query="Luffy",
        limit=24,
        offset=0,
    )

    item = page["items"][0]
    assert item["primary_image_url"] == "https://api.dontripit.com/media/onepiece/don/467133/cardmarket-image"
    assert "product-images.s3.cardmarket.com" not in item["primary_image_url"]
    assert item["card_id"] is None
    assert item["print_id"] is None
    assert item["identity_scope"] == "source_owned"


def test_don_search_hides_media_for_future_uncertified_expansion(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.dontripit.com")
    page = onepiece_don_market_page(
        _PostgresSession([_search_row("999999")]),
        query="Luffy",
        limit=24,
        offset=0,
    )
    assert page["items"][0]["primary_image_url"] is None
