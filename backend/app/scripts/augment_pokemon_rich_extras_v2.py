from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app import db
from app.models import Card, Game, Print, PrintIdentifier, Set
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot
from app.scripts.preflight_pokemon_bootstrap_v2 import card_key, print_key
from app.scripts.preflight_pokemon_rich_extras_v2 import _candidate_rows, run as run_preflight
from app.pokemon_source_inventory import load_inventory


SOURCE_NAME = "tcgdex"
SOURCE_VERSION_PREFIX = "tcgdex/cards-database@"
LANGUAGE = "en"
VARIANT = "default"
IS_FOIL = False


def _write_json(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    backup_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    # The exact same collision gate is rerun immediately before opening a write
    # transaction. This prevents a stale green preflight from authorizing a later
    # changed database state.
    preflight = run_preflight(snapshot_path, manifest_path)
    if preflight.get("status") != "pass" or (preflight.get("conflicts") or {}).get("count"):
        raise AssertionError("Released-English Pokémon augmentation refused: preflight is not clean")

    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_version = str(manifest.get("source_version") or "").strip()
    if not source_version:
        raise AssertionError("Rich snapshot manifest has no source version")

    inventory = load_inventory()
    rest_ids = set(inventory.physical_cards)
    candidates = _candidate_rows(snapshot, rest_ids)
    expected_candidates = int(preflight.get("released_english_repo_extras") or 0)
    if len(candidates) != expected_candidates:
        raise AssertionError(
            f"Candidate set changed between preflight and write: {len(candidates)} != {expected_candidates}"
        )
    if not candidates:
        raise AssertionError("No released-English source extras were selected for augmentation")

    db.init_engine()
    counters = {
        "cards_inserted": 0,
        "cards_updated": 0,
        "prints_inserted": 0,
        "prints_updated": 0,
        "identifiers_inserted": 0,
        "identifiers_updated": 0,
    }

    with db.SessionLocal() as session:
        with session.begin():
            game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
            if game is None:
                raise AssertionError("Pokémon game row missing")

            set_rows = list(session.execute(select(Set).where(Set.game_id == game.id)).scalars())
            sets_by_tcgdex = {str(row.tcgdex_id): row for row in set_rows if row.tcgdex_id}

            existing_candidate_cards = [dict(row) for row in session.execute(text(
                "SELECT id, name, card_key, tcgdex_id FROM cards WHERE game_id=:game AND tcgdex_id = ANY(:ids)"
            ), {"game": game.id, "ids": list(candidates)}).mappings().all()]
            existing_candidate_prints = [dict(row) for row in session.execute(text(
                "SELECT id, set_id, card_id, collector_number, language, rarity, is_foil, variant, print_key, tcgdex_id "
                "FROM prints WHERE tcgdex_id = ANY(:ids)"
            ), {"ids": list(candidates)}).mappings().all()]
            _write_json(backup_path, {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source_version": source_version,
                "candidate_count": len(candidates),
                "existing_candidate_cards": existing_candidate_cards,
                "existing_candidate_prints": existing_candidate_prints,
            })

            cards_by_tcgdex = {
                str(row.tcgdex_id): row
                for row in session.execute(select(Card).where(Card.game_id == game.id)).scalars()
                if row.tcgdex_id
            }
            card_map: dict[str, Card] = {}

            for source_id, source in sorted(candidates.items()):
                row = cards_by_tcgdex.get(source_id)
                if row is None:
                    row = Card(
                        game_id=game.id,
                        name=str(source.get("name") or source_id),
                        card_key=card_key(source_id),
                        tcgdex_id=source_id,
                    )
                    session.add(row)
                    counters["cards_inserted"] += 1
                else:
                    row.card_key = card_key(source_id)
                    # The pinned rich source is authoritative for these new
                    # identities, because REST never supplied an English row.
                    row.name = str(source.get("name") or row.name)
                    counters["cards_updated"] += 1
                card_map[source_id] = row

            session.flush()

            prints_by_tcgdex = {
                str(row.tcgdex_id): row
                for row in session.execute(
                    select(Print).join(Card, Card.id == Print.card_id).where(Card.game_id == game.id)
                ).scalars()
                if row.tcgdex_id
            }
            print_map: dict[str, Print] = {}

            for source_id, source in sorted(candidates.items()):
                set_source_id = str((source.get("set") or {}).get("id") or "")
                set_row = sets_by_tcgdex.get(set_source_id)
                if set_row is None:
                    raise AssertionError(f"Canonical set disappeared before write: {set_source_id}")
                card_row = card_map[source_id]
                attrs = source.get("attributes") or {}
                rarity = str(attrs.get("rarity") or "unknown")
                local_id = str(source.get("local_id") or "")
                if not local_id:
                    raise AssertionError(f"Candidate has no local ID: {source_id}")

                row = prints_by_tcgdex.get(source_id)
                if row is None:
                    row = Print(
                        set_id=set_row.id,
                        card_id=card_row.id,
                        collector_number=local_id,
                        language=LANGUAGE,
                        rarity=rarity,
                        is_foil=IS_FOIL,
                        variant=VARIANT,
                        print_key=print_key(source_id),
                        tcgdex_id=source_id,
                    )
                    session.add(row)
                    counters["prints_inserted"] += 1
                else:
                    row.set_id = set_row.id
                    row.card_id = card_row.id
                    row.collector_number = local_id
                    row.language = LANGUAGE
                    row.rarity = rarity
                    row.is_foil = IS_FOIL
                    row.variant = VARIANT
                    row.print_key = print_key(source_id)
                    counters["prints_updated"] += 1
                print_map[source_id] = row

            session.flush()

            identifiers = {
                (row.print_id, row.source): row
                for row in session.execute(
                    select(PrintIdentifier)
                    .join(Print, Print.id == PrintIdentifier.print_id)
                    .where(Print.tcgdex_id.in_(list(candidates)))
                ).scalars()
            }
            for source_id, print_row in print_map.items():
                identifier = identifiers.get((print_row.id, SOURCE_NAME))
                if identifier is None:
                    session.add(PrintIdentifier(print_id=print_row.id, source=SOURCE_NAME, external_id=source_id))
                    counters["identifiers_inserted"] += 1
                elif identifier.external_id != source_id:
                    identifier.external_id = source_id
                    counters["identifiers_updated"] += 1

            session.flush()

            present_cards = int(session.execute(text(
                "SELECT COUNT(*) FROM cards WHERE game_id=:game AND tcgdex_id = ANY(:ids)"
            ), {"game": game.id, "ids": list(candidates)}).scalar_one())
            present_prints = int(session.execute(text(
                "SELECT COUNT(*) FROM prints WHERE tcgdex_id = ANY(:ids)"
            ), {"ids": list(candidates)}).scalar_one())
            identity_mismatches = int(session.execute(text(
                """
                SELECT COUNT(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                WHERE p.tcgdex_id = ANY(:ids)
                  AND c.tcgdex_id IS DISTINCT FROM p.tcgdex_id
                """
            ), {"ids": list(candidates)}).scalar_one())

            if present_cards != len(candidates):
                raise AssertionError(f"Postcondition cards failed: {present_cards} != {len(candidates)}")
            if present_prints != len(candidates):
                raise AssertionError(f"Postcondition prints failed: {present_prints} != {len(candidates)}")
            if identity_mismatches:
                raise AssertionError(f"Postcondition has {identity_mismatches} Print/Card source-ID mismatches")

        final_counts = {
            "pokemon_cards_total": int(session.execute(text(
                "SELECT COUNT(*) FROM cards WHERE game_id=:game"
            ), {"game": game.id}).scalar_one()),
            "pokemon_prints_total": int(session.execute(text(
                "SELECT COUNT(*) FROM prints p JOIN cards c ON c.id=p.card_id WHERE c.game_id=:game"
            ), {"game": game.id}).scalar_one()),
            "accepted_rich_extra_cards": int(session.execute(text(
                "SELECT COUNT(*) FROM cards WHERE game_id=:game AND tcgdex_id = ANY(:ids)"
            ), {"game": game.id, "ids": list(candidates)}).scalar_one()),
            "accepted_rich_extra_prints": int(session.execute(text(
                "SELECT COUNT(*) FROM prints WHERE tcgdex_id = ANY(:ids)"
            ), {"ids": list(candidates)}).scalar_one()),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_released_english_identity_augmentation",
        "source": SOURCE_VERSION_PREFIX + source_version,
        "candidate_count": len(candidates),
        "preflight": {
            "status": preflight.get("status"),
            "conflicts": (preflight.get("conflicts") or {}).get("count"),
        },
        "mutations": counters,
        "after": final_counts,
        "status": "pass",
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    run(
        args.snapshot,
        args.manifest,
        backup_path=args.backup_path,
        report_path=args.report_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
