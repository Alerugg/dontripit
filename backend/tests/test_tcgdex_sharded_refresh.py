from __future__ import annotations

import pytest

from app.ingest.connectors.tcgdex_pokemon_duplicate_safe import (
    DuplicateSafeCertifiedRefreshPokemonTCGDexConnector,
)


def test_shard_partition_is_disjoint_complete_and_deterministic():
    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()
    source = [
        {"id": "set-c"},
        {"id": "set-a"},
        {"id": "set-f"},
        {"id": "set-b"},
        {"id": "set-e"},
        {"id": "set-d"},
    ]

    shards = [
        connector._select_shard_sets(source, shard_index=index, shard_count=3)
        for index in range(3)
    ]
    shard_ids = [[item["id"] for item in shard] for shard in shards]

    assert shard_ids == [
        ["set-a", "set-d"],
        ["set-b", "set-e"],
        ["set-c", "set-f"],
    ]
    flattened = [item for shard in shard_ids for item in shard]
    assert sorted(flattened) == sorted(item["id"] for item in source)
    assert len(flattened) == len(set(flattened))


def test_shard_config_defaults_to_unsharded(monkeypatch):
    monkeypatch.delenv("POKEMON_SHARD_INDEX", raising=False)
    monkeypatch.delenv("POKEMON_SHARD_COUNT", raising=False)

    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    assert connector._pokemon_shard_config() == (0, 1)


def test_shard_config_rejects_invalid_index(monkeypatch):
    monkeypatch.setenv("POKEMON_SHARD_INDEX", "3")
    monkeypatch.setenv("POKEMON_SHARD_COUNT", "3")

    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    with pytest.raises(RuntimeError, match="0 <= index < count"):
        connector._pokemon_shard_config()


def test_legacy_es_trainer_gallery_rename_is_narrow_and_explicit():
    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    assert connector._is_approved_legacy_set_id_rename(
        source="tcgdex:es",
        old_external_id="swsh10.5tg",
        new_external_id="swsh10tg",
    )
    assert connector._is_approved_legacy_set_id_rename(
        source="tcgdex:es",
        old_external_id="swsh12.5tg",
        new_external_id="swsh12tg",
    )
    assert not connector._is_approved_legacy_set_id_rename(
        source="tcgdex:en",
        old_external_id="swsh10.5tg",
        new_external_id="swsh10tg",
    )
    assert not connector._is_approved_legacy_set_id_rename(
        source="tcgdex:es",
        old_external_id="swsh10.5gg",
        new_external_id="swsh10gg",
    )
    assert not connector._is_approved_legacy_set_id_rename(
        source="tcgdex:es",
        old_external_id="swsh10.5tg",
        new_external_id="different-set",
    )


def test_legacy_es_trainer_gallery_print_rename_is_narrow_and_explicit():
    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    assert connector._is_approved_legacy_print_id_rename(
        source="tcgdex:es",
        old_external_id="swsh10.5tg-TG01",
        new_external_id="swsh10tg-TG01",
    )
    assert connector._is_approved_legacy_print_id_rename(
        source="tcgdex:es",
        old_external_id="swsh12.5tg-TG30",
        new_external_id="swsh12tg-TG30",
    )
    assert not connector._is_approved_legacy_print_id_rename(
        source="tcgdex:en",
        old_external_id="swsh10.5tg-TG01",
        new_external_id="swsh10tg-TG01",
    )
    assert not connector._is_approved_legacy_print_id_rename(
        source="tcgdex:es",
        old_external_id="swsh10.5tg-TG01",
        new_external_id="swsh10tg-TG02",
    )
    assert not connector._is_approved_legacy_print_id_rename(
        source="tcgdex:es",
        old_external_id="other.5tg-TG01",
        new_external_id="othertg-TG01",
    )
