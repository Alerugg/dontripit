from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app import db
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set
from app.pokemon_source_inventory import load_inventory
from app.scripts.preflight_pokemon_bootstrap_v2 import (
    IS_FOIL,
    LANGUAGE,
    VARIANT,
    card_key,
    choose_new_set_code,
    print_key,
    run as run_preflight,
)


GAME_SLUG = "pokemon"
SOURCE_NAME = "tcgdex"
EXPECTED_PHYSICAL_SETS = 203
EXPECTED_PHYSICAL_CARDS = 20964
BASELINE_RARITY = "unknown"


def _release_date(value: object) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return date.fromisoformat(clean[:10])
    except ValueError:
        return None


def _primary_image_url(image_base: object) -> str | None:
    clean = str(image_base or "").strip().rstrip("/")
    if not clean:
        return None
    if clean.lower().endswith((".webp", ".png", ".jpg", ".jpeg")):
        return clean
    return f"{clean}/high.webp"


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _before_state(session, game_id: int) -> dict:
    sets = [dict(row) for row in session.execute(text(
        "SELECT id, code, tcgdex_id, name, release_date FROM sets WHERE game_id=:game ORDER BY id"
    ), {"game": game_id}).mappings().all()]
    cards = [dict(row) for row in session.execute(text(
        "SELECT id, name, card_key, tcgdex_id FROM cards WHERE game_id=:game ORDER BY id"
    ), {"game": game_id}).mappings().all()]
    prints = [dict(row) for row in session.execute(text(
        """
        SELECT p.id, p.set_id, p.card_id, p.collector_number, p.language,
               p.is_foil, p.variant, p.print_key, p.tcgdex_id, p.rarity
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        WHERE c.game_id=:game
        ORDER BY p.id
        """
    ), {"game": game_id}).mappings().all()]
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "game_id": game_id,
        "sets": sets,
        "cards": cards,
        "prints": prints,
    }


def _physical_source_counts(session, game_id: int, set_ids: list[str], card_ids: list[str]) -> dict:
    return {
        "sets": int(session.execute(text(
            "SELECT COUNT(*) FROM sets WHERE game_id=:game AND tcgdex_id = ANY(:ids)"
        ), {"game": game_id, "ids": set_ids}).scalar_one()),
        "cards": int(session.execute(text(
            "SELECT COUNT(*) FROM cards WHERE game_id=:game AND tcgdex_id = ANY(:ids)"
        ), {"game": game_id, "ids": card_ids}).scalar_one()),
        "prints": int(session.execute(text(
            "SELECT COUNT(*) FROM prints WHERE tcgdex_id = ANY(:ids)"
        ), {"ids": card_ids}).scalar_one()),
        "print_card_identity_mismatches": int(session.execute(text(
            """
            SELECT COUNT(*)
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            WHERE p.tcgdex_id = ANY(:ids)
              AND c.tcgdex_id IS DISTINCT FROM p.tcgdex_id
            """
        ), {"ids": card_ids}).scalar_one()),
    }


def run(*, backup_path: Path | None = None, report_path: Path | None = None) -> dict:
    # Mandatory read-only gate. Any ambiguous identity collision aborts before a
    # write transaction is opened.
    preflight = run_preflight()
    if preflight.get("status") != "pass" or (preflight.get("conflicts") or {}).get("count"):
        raise AssertionError("Pokémon V2 bootstrap refused: preflight is not clean")

    inventory = load_inventory()
    physical_sets = {row["set_id"]: row for row in inventory.physical_sets}
    physical_cards = inventory.physical_cards
    if len(physical_sets) != EXPECTED_PHYSICAL_SETS or len(physical_cards) != EXPECTED_PHYSICAL_CARDS:
        raise AssertionError(
            f"Physical source moved unexpectedly: sets={len(physical_sets)} cards={len(physical_cards)}; "
            "re-audit before bootstrap"
        )

    source_set_ids = list(physical_sets)
    source_card_ids = list(physical_cards)
    db.init_engine()
    counters = {
        "sets_inserted": 0,
        "sets_updated": 0,
        "cards_inserted": 0,
        "cards_updated": 0,
        "prints_inserted": 0,
        "prints_updated": 0,
        "prints_relinked": 0,
        "identifiers_inserted": 0,
        "identifiers_updated": 0,
        "images_inserted": 0,
        "images_updated": 0,
    }

    game_id: int | None = None

    with db.SessionLocal() as session:
        # Start the transaction before the first SELECT. SQLAlchemy 2.x
        # autobegins on SELECT, so doing this explicitly keeps the whole mutation
        # under one clear transaction boundary.
        with session.begin():
            game = session.execute(select(Game).where(Game.slug == GAME_SLUG)).scalar_one_or_none()
            if game is None:
                raise AssertionError("Pokémon game row missing from Neon")
            game_id = int(game.id)

            before = _before_state(session, game_id)
            _write_json(backup_path, before)

            existing_sets = list(session.execute(select(Set).where(Set.game_id == game_id)).scalars())
            sets_by_tcgdex = {str(row.tcgdex_id): row for row in existing_sets if row.tcgdex_id}
            occupied_codes = {str(row.code).strip().lower() for row in existing_sets}
            set_map: dict[str, Set] = {}

            for source_id, source in sorted(physical_sets.items()):
                row = sets_by_tcgdex.get(source_id)
                if row is None:
                    code, _strategy = choose_new_set_code(source, occupied_codes)
                    occupied_codes.add(code.strip().lower())
                    row = Set(
                        game_id=game_id,
                        code=code,
                        tcgdex_id=source_id,
                        name=source.get("set_name") or source_id,
                        release_date=_release_date(source.get("release_date")),
                    )
                    session.add(row)
                    counters["sets_inserted"] += 1
                else:
                    row.name = source.get("set_name") or row.name
                    parsed_release = _release_date(source.get("release_date"))
                    if parsed_release is not None:
                        row.release_date = parsed_release
                    counters["sets_updated"] += 1
                set_map[source_id] = row

            session.flush()

            existing_cards = list(session.execute(select(Card).where(Card.game_id == game_id)).scalars())
            cards_by_tcgdex = {str(row.tcgdex_id): row for row in existing_cards if row.tcgdex_id}
            card_map: dict[str, Card] = {}

            for source_id, source in sorted(physical_cards.items()):
                row = cards_by_tcgdex.get(source_id)
                if row is None:
                    row = Card(
                        game_id=game_id,
                        name=source.get("name") or source_id,
                        card_key=card_key(source_id),
                        tcgdex_id=source_id,
                    )
                    session.add(row)
                    counters["cards_inserted"] += 1
                else:
                    row.name = source.get("name") or row.name
                    row.card_key = card_key(source_id)
                    counters["cards_updated"] += 1
                card_map[source_id] = row

            session.flush()

            existing_prints = list(session.execute(
                select(Print).join(Card, Card.id == Print.card_id).where(Card.game_id == game_id)
            ).scalars())
            prints_by_tcgdex = {str(row.tcgdex_id): row for row in existing_prints if row.tcgdex_id}
            print_map: dict[str, Print] = {}

            for source_id, source in sorted(physical_cards.items()):
                set_row = set_map[str(source["set_id"])]
                card_row = card_map[source_id]
                row = prints_by_tcgdex.get(source_id)
                if row is None:
                    row = Print(
                        set_id=set_row.id,
                        card_id=card_row.id,
                        collector_number=str(source["local_id"]),
                        language=LANGUAGE,
                        rarity=BASELINE_RARITY,
                        is_foil=IS_FOIL,
                        variant=VARIANT,
                        print_key=print_key(source_id),
                        tcgdex_id=source_id,
                    )
                    session.add(row)
                    counters["prints_inserted"] += 1
                else:
                    if row.card_id != card_row.id:
                        counters["prints_relinked"] += 1
                    row.set_id = set_row.id
                    row.card_id = card_row.id
                    row.collector_number = str(source["local_id"])
                    row.language = LANGUAGE
                    row.is_foil = IS_FOIL
                    row.variant = VARIANT
                    row.print_key = print_key(source_id)
                    # Preserve any existing non-null rarity until detailed V2
                    # enrichment supplies authoritative values.
                    counters["prints_updated"] += 1
                print_map[source_id] = row

            session.flush()

            tcgdex_identifiers = {
                row.print_id: row
                for row in session.execute(
                    select(PrintIdentifier)
                    .join(Print, Print.id == PrintIdentifier.print_id)
                    .join(Card, Card.id == Print.card_id)
                    .where(Card.game_id == game_id, PrintIdentifier.source == SOURCE_NAME)
                ).scalars()
            }
            existing_images = list(session.execute(
                select(PrintImage)
                .join(Print, Print.id == PrintImage.print_id)
                .join(Card, Card.id == Print.card_id)
                .where(Card.game_id == game_id)
            ).scalars())
            tcgdex_images = {
                row.print_id: row
                for row in existing_images
                if str(row.source or "").lower() == SOURCE_NAME
            }
            image_any_print_ids = {row.print_id for row in existing_images}

            for source_id, source in sorted(physical_cards.items()):
                print_row = print_map[source_id]
                identifier = tcgdex_identifiers.get(print_row.id)
                if identifier is None:
                    session.add(PrintIdentifier(print_id=print_row.id, source=SOURCE_NAME, external_id=source_id))
                    counters["identifiers_inserted"] += 1
                elif identifier.external_id != source_id:
                    identifier.external_id = source_id
                    counters["identifiers_updated"] += 1

                image_url = _primary_image_url(source.get("image"))
                if not image_url:
                    continue
                image = tcgdex_images.get(print_row.id)
                if image is not None:
                    if image.url != image_url:
                        image.url = image_url
                        counters["images_updated"] += 1
                elif print_row.id not in image_any_print_ids:
                    session.add(
                        PrintImage(
                            print_id=print_row.id,
                            url=image_url,
                            is_primary=True,
                            source=SOURCE_NAME,
                        )
                    )
                    counters["images_inserted"] += 1

            session.flush()

            actual = _physical_source_counts(session, game_id, source_set_ids, source_card_ids)
            if actual["sets"] != EXPECTED_PHYSICAL_SETS:
                raise AssertionError(f"Postcondition failed: physical sets {actual['sets']} != {EXPECTED_PHYSICAL_SETS}")
            if actual["cards"] != EXPECTED_PHYSICAL_CARDS:
                raise AssertionError(f"Postcondition failed: physical cards {actual['cards']} != {EXPECTED_PHYSICAL_CARDS}")
            if actual["prints"] != EXPECTED_PHYSICAL_CARDS:
                raise AssertionError(f"Postcondition failed: physical prints {actual['prints']} != {EXPECTED_PHYSICAL_CARDS}")
            if actual["print_card_identity_mismatches"]:
                raise AssertionError(
                    f"Postcondition failed: {actual['print_card_identity_mismatches']} print/card TCGdex identity mismatches"
                )

        # The transaction has committed. Use a fresh implicit read transaction
        # only for final evidence.
        assert game_id is not None
        after = {
            "sets_total": int(session.execute(text("SELECT COUNT(*) FROM sets WHERE game_id=:game"), {"game": game_id}).scalar_one()),
            "cards_total": int(session.execute(text("SELECT COUNT(*) FROM cards WHERE game_id=:game"), {"game": game_id}).scalar_one()),
            "prints_total": int(session.execute(text(
                "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game"
            ), {"game": game_id}).scalar_one()),
            **{
                f"physical_source_{key}": value
                for key, value in _physical_source_counts(session, game_id, source_set_ids, source_card_ids).items()
            },
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_idempotent_bootstrap",
        "source": {
            "physical_sets": len(physical_sets),
            "physical_cards": len(physical_cards),
            "pocket_sets_excluded": len(inventory.pocket_sets),
            "pocket_cards_excluded": len(inventory.pocket_cards),
        },
        "preflight": {
            "status": preflight.get("status"),
            "conflicts": (preflight.get("conflicts") or {}).get("count"),
        },
        "mutations": counters,
        "after": after,
        "stale_legacy_policy": "preserved; no destructive deletes in bootstrap",
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(backup_path=args.backup_path, report_path=args.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
