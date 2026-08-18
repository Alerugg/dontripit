from __future__ import annotations

import json

import pytest

from app.jobs.cardmarket_master_inventory import CatalogFeed, load_catalog_feed_bytes
from app.jobs.cardmarket_public_catalog_sync_v1 import (
    APPLY_CONFIRMATION,
    CARDMARKET_GAME_IDS,
    FULL_SURFACE_KEYS,
    catalog_url,
    resolve_game_slugs,
    validate_no_capture_regression,
    validate_source_feeds,
)


def _feed(game: str, group: str, product_id: int) -> CatalogFeed:
    payload = {
        "products": [
            {
                "idProduct": product_id,
                "name": f"{game}-{group}-{product_id}",
                "idExpansion": product_id + 1000,
            }
        ]
    }
    return load_catalog_feed_bytes(
        json.dumps(payload).encode(),
        game_slug=game,
        product_group=group,
    )


def test_cardmarket_public_catalog_urls_are_game_scoped_and_group_exact():
    assert catalog_url("mtg", "single").endswith("/products_singles_1.json")
    assert catalog_url("yugioh", "single").endswith("/products_singles_3.json")
    assert catalog_url("pokemon", "single").endswith("/products_singles_6.json")
    assert catalog_url("onepiece", "single").endswith("/products_singles_18.json")
    assert catalog_url("riftbound", "single").endswith("/products_singles_22.json")
    assert catalog_url("onepiece", "non_single").endswith("/products_nonsingles_18.json")


def test_all_resolves_to_every_supported_game_once():
    assert resolve_game_slugs("all") == tuple(CARDMARKET_GAME_IDS)
    assert set(resolve_game_slugs("all")) == {game for game, _ in FULL_SURFACE_KEYS}


def test_full_surface_gate_requires_every_game_and_both_product_groups():
    feeds = []
    next_id = 1
    for game, group in FULL_SURFACE_KEYS:
        feeds.append(_feed(game, group, next_id))
        next_id += 1
    summary = validate_source_feeds(feeds, require_full_surface=True)
    assert summary["full_surface"] is True
    assert summary["feed_count"] == 10
    assert summary["product_count"] == 10

    with pytest.raises(RuntimeError, match="Incomplete Cardmarket full surface"):
        validate_source_feeds(feeds[:-1], require_full_surface=True)


def test_source_gate_rejects_duplicate_product_ids_across_feeds():
    feeds = [
        _feed("onepiece", "single", 100),
        _feed("yugioh", "single", 100),
    ]
    with pytest.raises(RuntimeError, match="duplicate idProduct"):
        validate_source_feeds(feeds, require_full_surface=False)


def test_capture_regression_gate_fails_closed_on_truncated_source():
    feeds = [_feed("onepiece", "single", 100)]
    with pytest.raises(RuntimeError, match="count regression"):
        validate_no_capture_regression(feeds, {("onepiece", "single"): 2})


def test_capture_regression_gate_allows_growth():
    feeds = [
        CatalogFeed(
            game_slug="onepiece",
            product_group="single",
            rows=tuple(
                load_catalog_feed_bytes(
                    json.dumps({"products": [{"idProduct": i, "name": f"Card {i}"} for i in range(1, 4)]}).encode(),
                    game_slug="onepiece",
                    product_group="single",
                ).rows
            ),
            created_at=None,
            raw_records=3,
            rejected_records=0,
        )
    ]
    proof = validate_no_capture_regression(feeds, {("onepiece", "single"): 2})
    assert proof["incoming_counts"]["onepiece:single"] == 3
    assert proof["regressions"] == []


def test_apply_confirmation_token_is_deliberately_specific():
    assert APPLY_CONFIRMATION == "APPLY_CARDMARKET_PUBLIC_CATALOG_V1"
