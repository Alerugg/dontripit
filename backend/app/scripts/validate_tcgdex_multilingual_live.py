from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import select

from app import db
from app.ingest.base import IngestStats
from app.ingest.connectors.tcgdex_pokemon_multilingual import MultilingualTcgdexPokemonConnector
from app.models import Card, Print, PrintIdentifier
from app.multilingual_models import PrintLocalization


LANGUAGES = ("en", "es", "ja")


def _card_brief_map(payload) -> dict[str, dict]:
    if not isinstance(payload, list):
        return {}
    return {
        str(row.get("id") or "").strip(): row
        for row in payload
        if isinstance(row, dict) and row.get("id")
    }


def choose_common_cards(
    connector: MultilingualTcgdexPokemonConnector,
    limit: int,
) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """Choose card IDs exposed directly in all target language catalogs.

    TCGdex documents card IDs as language-stable. Set membership/completion can
    vary by locale, so the live gate intentionally intersects the `/cards`
    catalogs instead of inferring language availability from `/sets/<id>`.
    """
    maps: dict[str, dict[str, dict]] = {}
    for language in LANGUAGES:
        payload = connector._request_json(
            connector.base_url_template.format(lang=language) + "/cards"
        )
        maps[language] = _card_brief_map(payload)

    shared_ids = set.intersection(*(set(maps[language]) for language in LANGUAGES))
    if len(shared_ids) < limit:
        raise RuntimeError(
            "TCGdex live validation found too few card IDs shared by EN/ES/JA: "
            f"shared={len(shared_ids)} required={limit} "
            f"catalog_sizes={{lang: len(maps[lang]) for lang in LANGUAGES}}"
        )

    english_order = [card_id for card_id in maps["en"] if card_id in shared_ids]
    localized_ja = [
        card_id
        for card_id in english_order
        if maps["en"][card_id].get("name")
        and maps["ja"][card_id].get("name")
        and maps["en"][card_id].get("name") != maps["ja"][card_id].get("name")
    ]
    selected = localized_ja[:limit]
    if len(selected) < limit:
        selected.extend(card_id for card_id in english_order if card_id not in selected)
        selected = selected[:limit]
    return selected, maps


def fetch_direct_localizations(
    connector: MultilingualTcgdexPokemonConnector,
    selected_ids: list[str],
) -> dict[str, dict[str, dict]]:
    details: dict[str, dict[str, dict]] = {language: {} for language in LANGUAGES}
    for language in LANGUAGES:
        base_url = connector.base_url_template.format(lang=language)
        for external_id in selected_ids:
            payload = connector._request_json(f"{base_url}/cards/{external_id}")
            actual_id = str(payload.get("id") or "").strip()
            if actual_id != external_id:
                raise AssertionError(
                    f"TCGdex {language} direct card ID drift: requested={external_id} actual={actual_id}"
                )
            details[language][external_id] = payload
    return details


def ingest_live_sample(
    connector: MultilingualTcgdexPokemonConnector,
    selected_ids: list[str],
    details: dict[str, dict[str, dict]],
) -> dict[str, dict[str, str]]:
    source_sets: dict[str, dict[str, str]] = {language: {} for language in LANGUAGES}

    with db.SessionLocal() as session:
        for language in LANGUAGES:
            for external_id in selected_ids:
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


def certify_sample(
    card_ids: list[str],
    source_sets: dict[str, dict[str, str]],
) -> dict:
    results = []
    japanese_name_differs = False

    with db.SessionLocal() as session:
        for external_id in card_ids:
            card = session.execute(
                select(Card).where(Card.tcgdex_id == external_id)
            ).scalar_one()
            prints = session.execute(
                select(Print).where(Print.card_id == card.id)
            ).scalars().all()
            by_language = {row.language: row for row in prints}
            if set(by_language) != set(LANGUAGES):
                raise AssertionError(
                    f"Expected exactly EN/ES/JA physical prints for {external_id}; "
                    f"got={sorted(by_language)} source_sets="
                    f"{{lang: source_sets[lang][external_id] for lang in LANGUAGES}}"
                )
            if by_language["en"].tcgdex_id != external_id:
                raise AssertionError(f"English legacy tcgdex_id missing for {external_id}")
            if by_language["es"].tcgdex_id is not None or by_language["ja"].tcgdex_id is not None:
                raise AssertionError(f"Non-English physical print owns global tcgdex_id for {external_id}")

            localized_names = {}
            localized_sets = {}
            for language in LANGUAGES:
                print_row = by_language[language]
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
                        PrintLocalization.source == "tcgdex",
                    )
                ).scalar_one_or_none()
                if localization is None:
                    raise AssertionError(
                        f"Missing {language} localization for {external_id} print {print_row.id}"
                    )
                localized_names[language] = localization.card_name
                localized_sets[language] = localization.set_name

            if card.name != localized_names["en"]:
                raise AssertionError(
                    f"Canonical name drift for {external_id}: card={card.name!r} en={localized_names['en']!r}"
                )
            if localized_names["ja"] and localized_names["ja"] != localized_names["en"]:
                japanese_name_differs = True

            results.append(
                {
                    "tcgdex_id": external_id,
                    "canonical_name": card.name,
                    "source_set_ids": {
                        language: source_sets[language][external_id]
                        for language in LANGUAGES
                    },
                    "print_ids": {language: by_language[language].id for language in LANGUAGES},
                    "localized_names": localized_names,
                    "localized_sets": localized_sets,
                }
            )

    if not japanese_name_differs:
        raise AssertionError("Live sample did not demonstrate any Japanese localized card name difference")

    return {"cards": results, "certified_count": len(results)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate live TCGdex EN/ES/JA ingestion on an isolated database"
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
    connector = MultilingualTcgdexPokemonConnector()
    expected_ids, catalog_maps = choose_common_cards(connector, args.limit)
    details = fetch_direct_localizations(connector, expected_ids)
    source_sets = ingest_live_sample(connector, expected_ids, details)
    certification = certify_sample(expected_ids, source_sets)
    print(
        json.dumps(
            {
                "status": "success",
                "languages": list(LANGUAGES),
                "catalog_sizes": {
                    language: len(catalog_maps[language]) for language in LANGUAGES
                },
                "shared_card_ids": len(
                    set.intersection(
                        *(set(catalog_maps[language]) for language in LANGUAGES)
                    )
                ),
                **certification,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
