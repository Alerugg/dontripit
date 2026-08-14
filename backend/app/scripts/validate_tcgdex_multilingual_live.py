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
PREFERRED_COMMON_SETS = ("swsh1", "sv01", "base1")


def _ids(payload) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [str(row.get("id") or "").strip() for row in payload if isinstance(row, dict) and row.get("id")]


def _card_ids(set_payload: dict, limit: int) -> list[str]:
    cards = set_payload.get("cards") or []
    return [
        str(row.get("id") or "").strip()
        for row in cards[:limit]
        if isinstance(row, dict) and row.get("id")
    ]


def choose_common_set(connector: MultilingualTcgdexPokemonConnector, limit: int) -> tuple[str, list[str]]:
    sets_by_language: dict[str, set[str]] = {}
    for language in LANGUAGES:
        payload = connector._request_json(connector.base_url_template.format(lang=language) + "/sets")
        sets_by_language[language] = set(_ids(payload))

    common = set.intersection(*(sets_by_language[language] for language in LANGUAGES))
    if not common:
        raise RuntimeError("TCGdex live validation found no set IDs shared by EN/ES/JA")

    ordered = [set_id for set_id in PREFERRED_COMMON_SETS if set_id in common]
    ordered.extend(sorted(common - set(ordered), reverse=True)[:20])

    for set_id in ordered:
        details = {
            language: connector._request_json(
                connector.base_url_template.format(lang=language) + f"/sets/{set_id}"
            )
            for language in LANGUAGES
        }
        first_ids = {language: _card_ids(details[language], limit) for language in LANGUAGES}
        if len(first_ids["en"]) < limit:
            continue
        if first_ids["en"] == first_ids["es"] == first_ids["ja"]:
            return set_id, first_ids["en"]

    raise RuntimeError(
        "TCGdex live validation could not find a common EN/ES/JA set whose first "
        f"{limit} card IDs align"
    )


def ingest_live_sample(connector: MultilingualTcgdexPokemonConnector, set_id: str, limit: int) -> dict:
    source_ids_by_language: dict[str, list[str]] = {}
    with db.SessionLocal() as session:
        for language in LANGUAGES:
            rows = connector.load(None, fixture=False, limit=limit, set=set_id, lang=language)
            source_ids_by_language[language] = []
            for _path, raw_payload, _checksum in rows:
                external_id = str(raw_payload.get("id") or "").strip()
                source_ids_by_language[language].append(external_id)
                normalized = connector.normalize(raw_payload, lang=language)
                connector.upsert(
                    session,
                    normalized,
                    IngestStats(),
                    lang=language,
                    source_name="tcgdex_pokemon",
                )
            session.commit()

    if not (source_ids_by_language["en"] == source_ids_by_language["es"] == source_ids_by_language["ja"]):
        raise AssertionError(f"Live connector returned different IDs by language: {source_ids_by_language}")
    return source_ids_by_language


def certify_sample(card_ids: list[str]) -> dict:
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
                    f"Expected exactly EN/ES/JA physical prints for {external_id}; got {sorted(by_language)}"
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
                    "print_ids": {language: by_language[language].id for language in LANGUAGES},
                    "localized_names": localized_names,
                    "localized_sets": localized_sets,
                }
            )

    if not japanese_name_differs:
        raise AssertionError("Live sample did not demonstrate any Japanese localized card name difference")

    return {"cards": results, "certified_count": len(results)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate live TCGdex EN/ES/JA ingestion on an isolated database")
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 5:
        raise SystemExit("--limit must be between 1 and 5")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required; this validator must run on an explicit isolated database")
    if "localhost" not in database_url and "127.0.0.1" not in database_url:
        raise SystemExit("Refusing live multilingual validation against a non-local database")

    db.init_engine(database_url)
    connector = MultilingualTcgdexPokemonConnector()
    set_id, expected_ids = choose_common_set(connector, args.limit)
    source_ids = ingest_live_sample(connector, set_id, args.limit)
    if source_ids["en"] != expected_ids:
        raise AssertionError(
            f"Live connector sample changed after discovery: expected={expected_ids} actual={source_ids['en']}"
        )
    certification = certify_sample(expected_ids)
    print(
        json.dumps(
            {
                "status": "success",
                "set_id": set_id,
                "languages": list(LANGUAGES),
                **certification,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
