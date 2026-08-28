from __future__ import annotations

import pytest

from app.ingest.connectors.tcgdex_pokemon_duplicate_safe import (
    DuplicateSafeCertifiedRefreshPokemonTCGDexConnector,
)
from app.ingest.connectors.tcgdex_pokemon_identity_rehome import (
    ExactIdentityRehomeCertifiedPokemonTCGDexConnector,
)
from app.models import Card, Print


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


def test_card_identity_reuse_never_overwrites_another_exact_tcgdex_id():
    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    unclaimed = Card(game_id=1, name="Unclaimed", card_key="shared", tcgdex_id=None)
    exact = Card(game_id=1, name="Exact", card_key="shared", tcgdex_id="swsh10tg-TG15")
    claimed_other = Card(game_id=1, name="Other", card_key="shared", tcgdex_id="swsh10-069")

    assert connector._card_can_accept_tcgdex_identity(unclaimed, "swsh10tg-TG15")
    assert connector._card_can_accept_tcgdex_identity(exact, "swsh10tg-TG15")
    assert not connector._card_can_accept_tcgdex_identity(claimed_other, "swsh10tg-TG15")


def test_stale_en_card_identifier_rehome_requires_exact_target_and_distinct_legacy_owner():
    connector = DuplicateSafeCertifiedRefreshPokemonTCGDexConnector()

    legacy = Card(id=10, game_id=1, name="Legacy", card_key="shared", tcgdex_id="swsh10tg-TG05")
    target = Card(id=20, game_id=1, name="Exact", card_key="shared", tcgdex_id="ex16-9")
    unclaimed = Card(id=30, game_id=1, name="Unclaimed", card_key="shared", tcgdex_id=None)
    other_game = Card(id=40, game_id=2, name="Other game", card_key="shared", tcgdex_id="swsh10tg-TG05")

    assert connector._is_approved_legacy_en_card_identifier_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
    )
    assert not connector._is_approved_legacy_en_card_identifier_rehome(
        source="tcgdex:es",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
    )
    assert not connector._is_approved_legacy_en_card_identifier_rehome(
        source="tcgdex:en",
        external_id="different-id",
        existing_card=legacy,
        target_card=target,
    )
    assert not connector._is_approved_legacy_en_card_identifier_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=unclaimed,
        target_card=target,
    )
    assert not connector._is_approved_legacy_en_card_identifier_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=other_game,
        target_card=target,
    )


def test_exact_print_owner_rehome_accepts_same_legacy_owner_or_exact_target_only():
    connector = ExactIdentityRehomeCertifiedPokemonTCGDexConnector()

    legacy = Card(id=10, game_id=1, name="Legacy", card_key="shared", tcgdex_id="swsh10tg-TG05")
    target = Card(id=20, game_id=1, name="Exact", card_key="shared", tcgdex_id="ex16-9")
    print_on_legacy = Print(id=100, card_id=10, set_id=1, collector_number="9", language="en", tcgdex_id="ex16-9", rarity="unknown", is_foil=False, variant="default")
    print_on_target = Print(id=101, card_id=20, set_id=1, collector_number="9", language="en", tcgdex_id="ex16-9", rarity="unknown", is_foil=False, variant="default")
    print_on_third = Print(id=102, card_id=30, set_id=1, collector_number="9", language="en", tcgdex_id="ex16-9", rarity="unknown", is_foil=False, variant="default")

    assert connector._exact_print_owner_allows_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
        exact_print=print_on_legacy,
    )
    assert connector._exact_print_owner_allows_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
        exact_print=print_on_target,
    )
    assert not connector._exact_print_owner_allows_rehome(
        source="tcgdex:en",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
        exact_print=print_on_third,
    )
    assert not connector._exact_print_owner_allows_rehome(
        source="tcgdex:es",
        external_id="ex16-9",
        existing_card=legacy,
        target_card=target,
        exact_print=print_on_legacy,
    )
