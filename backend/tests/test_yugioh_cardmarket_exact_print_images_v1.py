from app.scripts.apply_yugioh_cardmarket_exact_print_images_v1 import (
    EXPECTED_ROWS,
    SOURCE,
    _load_manifest,
)


def test_manifest_is_exactly_16_unique_japanese_physical_products():
    rows = _load_manifest()
    assert len(rows) == EXPECTED_ROWS == 16
    assert len({row["print_id"] for row in rows}) == 16
    assert len({row["id_product"] for row in rows}) == 16
    assert len({row["url"] for row in rows}) == 16
    assert len({row["sha256"] for row in rows}) == 16
    assert {row["language"] for row in rows} == {"ja"}
    assert all(row["url"].startswith("https://product-images.s3.cardmarket.com/5/") for row in rows)
    assert all(row["format"] == "JPEG" for row in rows)
    assert SOURCE == "cardmarket_exact_product_image_v1"


def test_manifest_keeps_distinct_duad_rarity_versions_for_same_collector():
    mocha = [row for row in _load_manifest() if row["collector_number"] == "DUAD-JP028"]
    assert len(mocha) == 2
    assert {row["rarity"] for row in mocha} == {"prismaticsecret", "shortprint"}
    assert {row["variant"] for row in mocha} == {"rarity-prismaticsecret", "rarity-shortprint"}
    assert len({row["id_product"] for row in mocha}) == 2
    assert len({row["sha256"] for row in mocha}) == 2
