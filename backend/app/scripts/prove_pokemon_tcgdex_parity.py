from __future__ import annotations

import json
import os
from dataclasses import dataclass

from sqlalchemy import create_engine, text

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)


LANGUAGES = ("en", "es", "ja")
KNOWN_PROMO_ANCHORS = {"en": {"svp-202"}}


@dataclass(frozen=True)
class SourceManifest:
    language: str
    sets: frozenset[str]
    cards: frozenset[str]
    prints: frozenset[str]
    pocket_cards: frozenset[str]


def _match_set_id(card_id: str, set_ids: tuple[str, ...]) -> str | None:
    """Resolve a TCGdex card id to the longest published set-id prefix."""

    for set_id in set_ids:
        if card_id.startswith(f"{set_id}-"):
            return set_id
    return None


def _source_manifest(
    connector: PhysicalMultilingualTcgdexPokemonConnector,
    *,
    language: str,
) -> SourceManifest:
    language = connector._assert_certified_language(language)
    base_url = connector.base_url_template.format(lang=language)
    pocket_set_ids = connector._tcg_pocket_set_ids(lang=language)

    raw_sets = connector._request_json(f"{base_url}/sets")
    raw_cards = connector._request_json(f"{base_url}/cards")
    if not isinstance(raw_sets, list) or not isinstance(raw_cards, list):
        raise RuntimeError(
            f"Unexpected TCGdex catalog payload language={language} "
            f"sets={type(raw_sets).__name__} cards={type(raw_cards).__name__}"
        )

    published_set_ids = {
        str(row.get("id") or "").strip()
        for row in raw_sets
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    if not published_set_ids:
        raise RuntimeError(f"TCGdex published zero sets for language={language}")

    ordered_set_ids = tuple(sorted(published_set_ids, key=lambda value: (-len(value), value)))
    physical_cards: set[str] = set()
    physical_sets: set[str] = set()
    pocket_cards: set[str] = set()
    unmatched_cards: list[str] = []

    for row in raw_cards:
        if not isinstance(row, dict):
            continue
        card_id = str(row.get("id") or "").strip()
        if not card_id:
            continue
        set_id = _match_set_id(card_id, ordered_set_ids)
        if set_id is None:
            unmatched_cards.append(card_id)
            continue
        if set_id in pocket_set_ids:
            pocket_cards.add(card_id)
            continue
        physical_cards.add(card_id)
        physical_sets.add(set_id)

    if unmatched_cards:
        raise RuntimeError(
            f"TCGdex cards could not be assigned to a published set language={language} "
            f"count={len(unmatched_cards)} examples={sorted(unmatched_cards)[:20]}"
        )
    if not physical_cards or not physical_sets:
        raise RuntimeError(
            f"TCGdex physical catalog unexpectedly empty language={language} "
            f"cards={len(physical_cards)} sets={len(physical_sets)}"
        )

    missing_anchors = sorted(KNOWN_PROMO_ANCHORS.get(language, set()) - physical_cards)
    if missing_anchors:
        raise RuntimeError(
            f"TCGdex known promo anchors disappeared language={language} missing={missing_anchors}"
        )

    return SourceManifest(
        language=language,
        sets=frozenset(physical_sets),
        cards=frozenset(physical_cards),
        prints=frozenset(physical_cards),
        pocket_cards=frozenset(pocket_cards),
    )


def _database_manifest(conn, *, game_id: int, language: str) -> dict[str, set[str]]:
    source = f"tcgdex:{language}"
    sets = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT si.external_id
                FROM set_identifiers si
                JOIN sets s ON s.id = si.set_id
                WHERE s.game_id = :game_id AND si.source = :source
                """
            ),
            {"game_id": game_id, "source": source},
        ).scalars()
    }
    cards = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT ci.external_id
                FROM card_identifiers ci
                JOIN cards c ON c.id = ci.card_id
                WHERE c.game_id = :game_id AND ci.source = :source
                """
            ),
            {"game_id": game_id, "source": source},
        ).scalars()
    }
    prints = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT pi.external_id
                FROM print_identifiers pi
                JOIN prints p ON p.id = pi.print_id
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = :game_id
                  AND pi.source = :source
                  AND p.language = :language
                """
            ),
            {"game_id": game_id, "source": source, "language": language},
        ).scalars()
    }
    localizations = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT pl.external_id
                FROM print_localizations pl
                JOIN prints p ON p.id = pl.print_id
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = :game_id
                  AND pl.source = 'tcgdex'
                  AND pl.language = :language
                  AND p.language = :language
                  AND pl.external_id IS NOT NULL
                """
            ),
            {"game_id": game_id, "language": language},
        ).scalars()
    }
    wrong_language_prints = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT pi.external_id
                FROM print_identifiers pi
                JOIN prints p ON p.id = pi.print_id
                JOIN cards c ON c.id = p.card_id
                WHERE c.game_id = :game_id
                  AND pi.source = :source
                  AND coalesce(p.language, '') <> :language
                """
            ),
            {"game_id": game_id, "source": source, "language": language},
        ).scalars()
    }
    return {
        "sets": sets,
        "cards": cards,
        "prints": prints,
        "localizations": localizations,
        "wrong_language_prints": wrong_language_prints,
    }


def main() -> None:
    connector = PhysicalMultilingualTcgdexPokemonConnector()
    source_manifests = {
        language: _source_manifest(connector, language=language)
        for language in LANGUAGES
    }

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(database_url)
    proof: dict[str, object] = {"status": "pass", "languages": {}}
    failures: list[dict[str, object]] = []

    with engine.connect() as conn:
        game_id = conn.execute(
            text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")
        ).scalar_one()

        for language, source in source_manifests.items():
            db = _database_manifest(conn, game_id=game_id, language=language)
            missing_sets = sorted(source.sets - db["sets"])
            missing_cards = sorted(source.cards - db["cards"])
            missing_prints = sorted(source.prints - db["prints"])
            missing_localizations = sorted(source.prints - db["localizations"])
            pocket_prints = sorted(source.pocket_cards & db["prints"])
            wrong_language = sorted(db["wrong_language_prints"])

            row = {
                "source_sets": len(source.sets),
                "source_cards": len(source.cards),
                "source_prints": len(source.prints),
                "source_pocket_cards_excluded": len(source.pocket_cards),
                "missing_sets": len(missing_sets),
                "missing_cards": len(missing_cards),
                "missing_prints": len(missing_prints),
                "missing_localizations": len(missing_localizations),
                "pocket_prints_materialized": len(pocket_prints),
                "wrong_language_print_identifiers": len(wrong_language),
                "missing_set_examples": missing_sets[:20],
                "missing_card_examples": missing_cards[:20],
                "missing_print_examples": missing_prints[:20],
                "missing_localization_examples": missing_localizations[:20],
                "pocket_print_examples": pocket_prints[:20],
                "wrong_language_examples": wrong_language[:20],
                "known_promo_anchors": sorted(KNOWN_PROMO_ANCHORS.get(language, set())),
            }
            proof["languages"][language] = row

            if (
                missing_sets
                or missing_cards
                or missing_prints
                or missing_localizations
                or pocket_prints
                or wrong_language
            ):
                failures.append({"language": language, **row})

    if failures:
        proof["status"] = "fail"
        proof["failures"] = failures
        raise AssertionError(json.dumps(proof, indent=2, sort_keys=True))

    print(json.dumps(proof, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
