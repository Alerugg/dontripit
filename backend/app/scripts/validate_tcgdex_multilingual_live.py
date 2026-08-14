from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)
from app.models import Card, Print, PrintIdentifier
from app.multilingual_models import CardIdentifier, PrintLocalization


LANGUAGES = ("en", "es", "ja")
_SAFE_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _card_brief_map(payload) -> dict[str, dict]:
    if not isinstance(payload, list):
        return {}
    return {
        str(row.get("id") or "").strip(): row
        for row in payload
        if isinstance(row, dict) and row.get("id")
    }


def _without_set_ids(cards: dict[str, dict], set_ids: set[str]) -> dict[str, dict]:
    if not set_ids:
        return dict(cards)
    prefixes = tuple(f"{set_id}-" for set_id in set_ids)
    return {
        external_id: payload
        for external_id, payload in cards.items()
        if not external_id.startswith(prefixes)
    }


def load_physical_catalog_maps(
    connector: PhysicalMultilingualTcgdexPokemonConnector,
) -> tuple[dict[str, dict[str, dict]], dict[str, set[str]]]:
    """Load language card catalogs with TCG Pocket removed explicitly."""
    maps: dict[str, dict[str, dict]] = {}
    excluded_set_ids: dict[str, set[str]] = {}
    for language in LANGUAGES:
        payload = connector._request_json(
            connector.base_url_template.format(lang=language) + "/cards"
        )
        all_cards = _card_brief_map(payload)
        pocket_set_ids = connector._tcg_pocket_set_ids(lang=language)
        excluded_set_ids[language] = pocket_set_ids
        maps[language] = _without_set_ids(all_cards, pocket_set_ids)
    return maps, excluded_set_ids


def select_physical_samples(
    maps: dict[str, dict[str, dict]],
    limit: int,
) -> tuple[list[str], list[str], str | None]:
    """Select samples according to the certified physical identity semantics.

    EN and ES share the international physical identity space. JA is a separate
    regional physical catalog and must never be validated by requiring a shared
    raw ID with EN/ES. If an EN/JA raw-ID collision exists, one is selected
    separately so the live gate can prove that it remains two different Cards
    and Prints rather than being merged accidentally.
    """
    en_ids = set(maps["en"])
    es_ids = set(maps["es"])
    ja_ids = set(maps["ja"])

    shared_en_es = en_ids & es_ids
    international_candidates = [
        external_id
        for external_id in maps["en"]
        if external_id in shared_en_es
        and external_id not in ja_ids
        and _SAFE_EXTERNAL_ID.fullmatch(external_id)
    ]
    if len(international_candidates) < limit:
        raise RuntimeError(
            "TCGdex live validation found too few safe physical IDs shared by EN/ES: "
            f"shared={len(shared_en_es)} safe_non_ja={len(international_candidates)} "
            f"required={limit}"
        )

    japanese_candidates = [
        external_id
        for external_id in maps["ja"]
        if external_id not in en_ids
        and external_id not in es_ids
        and _SAFE_EXTERNAL_ID.fullmatch(external_id)
    ]
    if len(japanese_candidates) < limit:
        raise RuntimeError(
            "TCGdex live validation found too few independent safe JA physical IDs: "
            f"independent={len(japanese_candidates)} required={limit}"
        )

    collision_candidates = [
        external_id
        for external_id in maps["en"]
        if external_id in ja_ids
        and _SAFE_EXTERNAL_ID.fullmatch(external_id)
        and maps["en"][external_id].get("name")
        and maps["ja"][external_id].get("name")
        and maps["en"][external_id].get("name") != maps["ja"][external_id].get("name")
    ]
    if not collision_candidates:
        collision_candidates = [
            external_id
            for external_id in maps["en"]
            if external_id in ja_ids and _SAFE_EXTERNAL_ID.fullmatch(external_id)
        ]

    return (
        international_candidates[:limit],
        japanese_candidates[:limit],
        collision_candidates[0] if collision_candidates else None,
    )


def selected_ids_by_language(
    international_ids: list[str],
    japanese_ids: list[str],
    collision_id: str | None,
) -> dict[str, list[str]]:
    selected = {
        "en": list(international_ids),
        "es": list(international_ids),
        "ja": list(japanese_ids),
    }
    if collision_id:
        selected["en"].append(collision_id)
        selected["ja"].append(collision_id)
    return selected


def fetch_direct_cards(
    connector: PhysicalMultilingualTcgdexPokemonConnector,
    selected: dict[str, list[str]],
) -> dict[str, dict[str, dict]]:
    details: dict[str, dict[str, dict]] = {language: {} for language in LANGUAGES}
    for language in LANGUAGES:
        base_url = connector.base_url_template.format(lang=language)
        for external_id in selected[language]:
            payload = connector._request_json(f"{base_url}/cards/{external_id}")
            if not isinstance(payload, dict):
                raise AssertionError(
                    f"TCGdex {language} card {external_id} returned {type(payload).__name__}"
                )
            actual_id = str(payload.get("id") or "").strip()
            if actual_id != external_id:
                raise AssertionError(
                    f"TCGdex {language} direct card ID drift: requested={external_id} actual={actual_id}"
                )
            details[language][external_id] = payload
    return details


def ingest_live_sample(
    connector: PhysicalMultilingualTcgdexPokemonConnector,
    selected: dict[str, list[str]],
    details: dict[str, dict[str, dict]],
) -> dict[str, dict[str, str]]:
    source_sets: dict[str, dict[str, str]] = {language: {} for language in LANGUAGES}

    # EN must be materialized before ES because ES intentionally resolves only
    # against an already-established exact international EN identity.
    with db.SessionLocal() as session:
        for language in LANGUAGES:
            for external_id in selected[language]:
                raw_payload = details[language][external_id]
                set_id = str((raw_payload.get("set") or {}).get("id") or "").strip()
                if not set_id:
                    raise AssertionError(
                        f"TCGdex {language} direct card {external_id} has no set.id"
                    )
                source_sets[language][external_id] = set_id
                normalized = connector.normalize(raw_payload, lang=language)
                connector.upsert(
                    session,
                    normalized,
                    IngestStats(),
                    lang=language,
                    source_name="tcgdex_pokemon",
                )
            session.commit()

    return source_sets


def _certify_print_language_state(
    session,
    *,
    print_row: Print,
    language: str,
    external_id: str,
) -> PrintLocalization:
    identifier = session.execute(
        select(PrintIdentifier).where(
            PrintIdentifier.print_id == print_row.id,
            PrintIdentifier.source == f"tcgdex:{language}",
            PrintIdentifier.external_id == external_id,
        )
    ).scalar_one_or_none()
    if identifier is None:
        raise AssertionError(
            f"Missing tcgdex:{language} identifier for {external_id} print {print_row.id}"
        )

    localization = session.execute(
        select(PrintLocalization).where(
            PrintLocalization.print_id == print_row.id,
            PrintLocalization.language == language,
        )
    ).scalar_one_or_none()
    if localization is None:
        raise AssertionError(
            f"Missing {language} localization for {external_id} print {print_row.id}"
        )
    if localization.source != "tcgdex":
        raise AssertionError(
            f"Unexpected localization source for {language}/{external_id}: {localization.source}"
        )
    return localization


def certify_international_sample(
    session,
    card_ids: list[str],
    source_sets: dict[str, dict[str, str]],
) -> list[dict]:
    results = []
    for external_id in card_ids:
        card = session.execute(
            select(Card).where(Card.tcgdex_id == external_id)
        ).scalar_one()
        prints = session.execute(
            select(Print).where(Print.card_id == card.id)
        ).scalars().all()
        by_language = {row.language: row for row in prints}
        if set(by_language) != {"en", "es"}:
            raise AssertionError(
                f"Expected exactly EN/ES physical prints for international {external_id}; "
                f"got={sorted(by_language)}"
            )
        if by_language["en"].tcgdex_id != external_id:
            raise AssertionError(f"English legacy tcgdex_id missing for {external_id}")
        if by_language["es"].tcgdex_id is not None:
            raise AssertionError(f"Spanish print owns global tcgdex_id for {external_id}")

        localizations = {
            language: _certify_print_language_state(
                session,
                print_row=by_language[language],
                language=language,
                external_id=external_id,
            )
            for language in ("en", "es")
        }
        if card.name != localizations["en"].card_name:
            raise AssertionError(
                f"Canonical EN name drift for {external_id}: "
                f"card={card.name!r} en={localizations['en'].card_name!r}"
            )

        results.append(
            {
                "tcgdex_id": external_id,
                "canonical_name": card.name,
                "source_set_ids": {
                    language: source_sets[language][external_id]
                    for language in ("en", "es")
                },
                "print_ids": {
                    language: by_language[language].id for language in ("en", "es")
                },
                "localized_names": {
                    language: localizations[language].card_name
                    for language in ("en", "es")
                },
                "localized_sets": {
                    language: localizations[language].set_name
                    for language in ("en", "es")
                },
            }
        )
    return results


def certify_japanese_sample(
    session,
    card_ids: list[str],
    source_sets: dict[str, dict[str, str]],
) -> list[dict]:
    results = []
    for external_id in card_ids:
        identifier = session.execute(
            select(CardIdentifier).where(
                CardIdentifier.source == "tcgdex:ja",
                CardIdentifier.external_id == external_id,
            )
        ).scalar_one()
        card = session.get(Card, identifier.card_id)
        if card is None:
            raise AssertionError(f"Missing JA regional Card for {external_id}")
        if card.tcgdex_id is not None:
            raise AssertionError(f"JA regional Card owns global tcgdex_id for {external_id}")

        prints = session.execute(
            select(Print).where(Print.card_id == card.id)
        ).scalars().all()
        if len(prints) != 1 or prints[0].language != "ja":
            raise AssertionError(
                f"Expected exactly one JA physical print for {external_id}; "
                f"got={[row.language for row in prints]}"
            )
        print_row = prints[0]
        if print_row.tcgdex_id is not None:
            raise AssertionError(f"JA print owns global tcgdex_id for {external_id}")

        localization = _certify_print_language_state(
            session,
            print_row=print_row,
            language="ja",
            external_id=external_id,
        )
        if card.name != localization.card_name:
            raise AssertionError(
                f"JA regional name drift for {external_id}: "
                f"card={card.name!r} localization={localization.card_name!r}"
            )

        results.append(
            {
                "tcgdex_id": external_id,
                "regional_card_id": card.id,
                "regional_name": card.name,
                "source_set_id": source_sets["ja"][external_id],
                "print_id": print_row.id,
                "localized_set": localization.set_name,
            }
        )
    return results


def certify_en_ja_collision(
    session,
    external_id: str | None,
) -> dict | None:
    if not external_id:
        return None

    english_card = session.execute(
        select(Card).where(Card.tcgdex_id == external_id)
    ).scalar_one()
    ja_identifier = session.execute(
        select(CardIdentifier).where(
            CardIdentifier.source == "tcgdex:ja",
            CardIdentifier.external_id == external_id,
        )
    ).scalar_one()
    japanese_card = session.get(Card, ja_identifier.card_id)
    if japanese_card is None:
        raise AssertionError(f"Missing JA collision Card for {external_id}")
    if english_card.id == japanese_card.id:
        raise AssertionError(f"EN/JA raw-ID collision merged Cards for {external_id}")
    if japanese_card.tcgdex_id is not None:
        raise AssertionError(f"JA collision Card owns global tcgdex_id for {external_id}")

    english_prints = session.execute(
        select(Print).where(Print.card_id == english_card.id)
    ).scalars().all()
    japanese_prints = session.execute(
        select(Print).where(Print.card_id == japanese_card.id)
    ).scalars().all()
    en_print = next((row for row in english_prints if row.language == "en"), None)
    ja_print = next((row for row in japanese_prints if row.language == "ja"), None)
    if en_print is None or ja_print is None or en_print.id == ja_print.id:
        raise AssertionError(f"EN/JA raw-ID collision did not remain physically separate for {external_id}")
    if en_print.tcgdex_id != external_id or ja_print.tcgdex_id is not None:
        raise AssertionError(f"EN/JA collision global identity contract failed for {external_id}")

    en_localization = _certify_print_language_state(
        session,
        print_row=en_print,
        language="en",
        external_id=external_id,
    )
    ja_localization = _certify_print_language_state(
        session,
        print_row=ja_print,
        language="ja",
        external_id=external_id,
    )

    return {
        "tcgdex_id": external_id,
        "english_card_id": english_card.id,
        "japanese_card_id": japanese_card.id,
        "english_print_id": en_print.id,
        "japanese_print_id": ja_print.id,
        "english_name": en_localization.card_name,
        "japanese_name": ja_localization.card_name,
    }


def certify_sample(
    international_ids: list[str],
    japanese_ids: list[str],
    collision_id: str | None,
    source_sets: dict[str, dict[str, str]],
) -> dict:
    with db.SessionLocal() as session:
        international = certify_international_sample(session, international_ids, source_sets)
        japanese = certify_japanese_sample(session, japanese_ids, source_sets)
        collision = certify_en_ja_collision(session, collision_id)

    return {
        "international_en_es": international,
        "japanese_independent": japanese,
        "en_ja_collision": collision,
        "certified_counts": {
            "international_en_es": len(international),
            "japanese_independent": len(japanese),
            "collision": 1 if collision else 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate live physical TCGdex EN/ES and independent JA ingestion on an isolated database"
    )
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 5:
        raise SystemExit("--limit must be between 1 and 5")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit(
            "DATABASE_URL is required; this validator must run on an explicit isolated database"
        )
    if "localhost" not in database_url and "127.0.0.1" not in database_url:
        raise SystemExit("Refusing live multilingual validation against a non-local database")

    db.init_engine(database_url)
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    catalog_maps, pocket_set_ids = load_physical_catalog_maps(connector)
    international_ids, japanese_ids, collision_id = select_physical_samples(
        catalog_maps, args.limit
    )
    selected = selected_ids_by_language(international_ids, japanese_ids, collision_id)
    details = fetch_direct_cards(connector, selected)
    source_sets = ingest_live_sample(connector, selected, details)
    certification = certify_sample(
        international_ids,
        japanese_ids,
        collision_id,
        source_sets,
    )

    print(
        json.dumps(
            {
                "status": "success",
                "identity_contract": {
                    "en_es": "shared_international_identity_separate_physical_prints",
                    "ja": "independent_regional_identity_and_physical_prints",
                },
                "languages": list(LANGUAGES),
                "physical_catalog_sizes": {
                    language: len(catalog_maps[language]) for language in LANGUAGES
                },
                "pocket_set_ids_excluded": {
                    language: sorted(pocket_set_ids[language]) for language in LANGUAGES
                },
                "overlap_counts": {
                    "en_es": len(set(catalog_maps["en"]) & set(catalog_maps["es"])),
                    "en_ja_raw_id_collisions": len(
                        set(catalog_maps["en"]) & set(catalog_maps["ja"])
                    ),
                    "es_ja_raw_id_collisions": len(
                        set(catalog_maps["es"]) & set(catalog_maps["ja"])
                    ),
                    "en_es_ja": len(
                        set(catalog_maps["en"])
                        & set(catalog_maps["es"])
                        & set(catalog_maps["ja"])
                    ),
                },
                "selected": {
                    "international_en_es": international_ids,
                    "japanese_independent": japanese_ids,
                    "en_ja_collision": collision_id,
                },
                **certification,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
