from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text

from app.ingest.connectors.tcgdex_pokemon_multilingual_physical import (
    PhysicalMultilingualTcgdexPokemonConnector,
)
from app.scripts.prove_pokemon_tcgdex_parity import (
    KNOWN_PROMO_ANCHORS,
    LANGUAGES,
    _source_manifest,
)


def _database_manifest(conn, *, game_id: int, language: str) -> dict[str, set[str]]:
    if language == "en":
        sets = {
            str(value)
            for value in conn.execute(
                text(
                    """
                    SELECT tcgdex_id
                    FROM sets
                    WHERE game_id=:game_id AND tcgdex_id IS NOT NULL
                    """
                ),
                {"game_id": game_id},
            ).scalars()
        }
        cards = {
            str(value)
            for value in conn.execute(
                text(
                    """
                    SELECT tcgdex_id
                    FROM cards
                    WHERE game_id=:game_id AND tcgdex_id IS NOT NULL
                    """
                ),
                {"game_id": game_id},
            ).scalars()
        }
        prints = {
            str(value)
            for value in conn.execute(
                text(
                    """
                    SELECT p.tcgdex_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=:game_id
                      AND p.language='en'
                      AND p.tcgdex_id IS NOT NULL
                    """
                ),
                {"game_id": game_id},
            ).scalars()
        }
        wrong_language_prints = {
            str(value)
            for value in conn.execute(
                text(
                    """
                    SELECT p.tcgdex_id
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    WHERE c.game_id=:game_id
                      AND coalesce(p.language,'') <> 'en'
                      AND p.tcgdex_id IS NOT NULL
                    """
                ),
                {"game_id": game_id},
            ).scalars()
        }
        # EN is the canonical international catalog. Card/Set names and details
        # live on canonical rows, so a duplicate PrintLocalization is optional.
        localizations = set(prints)
        return {
            "sets": sets,
            "cards": cards,
            "prints": prints,
            "localizations": localizations,
            "wrong_language_prints": wrong_language_prints,
        }

    source = f"tcgdex:{language}"
    sets = {
        str(value)
        for value in conn.execute(
            text(
                """
                SELECT si.external_id
                FROM set_identifiers si
                JOIN sets s ON s.id=si.set_id
                WHERE s.game_id=:game_id AND si.source=:source
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
                JOIN cards c ON c.id=ci.card_id
                WHERE c.game_id=:game_id AND ci.source=:source
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
                JOIN prints p ON p.id=pi.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game_id
                  AND pi.source=:source
                  AND p.language=:language
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
                JOIN prints p ON p.id=pl.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game_id
                  AND pl.source='tcgdex'
                  AND pl.language=:language
                  AND p.language=:language
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
                JOIN prints p ON p.id=pi.print_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game_id
                  AND pi.source=:source
                  AND coalesce(p.language,'') <> :language
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
    proof: dict[str, object] = {
        "status": "pass",
        "production_writes": 0,
        "en_identity_contract": "canonical_legacy_tcgdex_id",
        "regional_identity_contract": "language_qualified_identifiers_and_localizations",
        "languages": {},
    }
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
                "identity_contract": (
                    "canonical_legacy_tcgdex_id"
                    if language == "en"
                    else "language_qualified_overlay"
                ),
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
