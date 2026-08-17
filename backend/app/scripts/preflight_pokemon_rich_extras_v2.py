from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import db
from app.pokemon_source_inventory import load_inventory
from app.scripts.audit_pokemon_rich_snapshot_v2 import load_snapshot
from app.scripts.preflight_pokemon_bootstrap_v2 import card_key, print_key


LANGUAGE = "en"
VARIANT = "default"
IS_FOIL = False


def _release_date(row: dict) -> date | None:
    raw = str((row.get("set") or {}).get("release_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _candidate_rows(snapshot: dict[str, dict], rest_ids: set[str]) -> dict[str, dict]:
    today = datetime.now(timezone.utc).date()
    result: dict[str, dict] = {}
    for source_id, row in snapshot.items():
        if source_id in rest_ids:
            continue
        name = str(row.get("name") or "").strip()
        released = _release_date(row)
        if not name or released is None or released > today:
            continue
        result[source_id] = row
    return result


def run(snapshot_path: Path, manifest_path: Path) -> dict:
    snapshot = load_snapshot(snapshot_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("Rich snapshot exporter did not pass")

    inventory = load_inventory()
    rest_ids = set(inventory.physical_cards)
    candidates = _candidate_rows(snapshot, rest_ids)

    db.init_engine()
    with db.SessionLocal() as session:
        game_id = int(session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one())
        sets = [dict(row) for row in session.execute(text(
            "SELECT id, code, tcgdex_id, name FROM sets WHERE game_id=:game"
        ), {"game": game_id}).mappings().all()]
        cards = [dict(row) for row in session.execute(text(
            "SELECT id, card_key, tcgdex_id, name FROM cards WHERE game_id=:game"
        ), {"game": game_id}).mappings().all()]
        prints = [dict(row) for row in session.execute(text(
            """
            SELECT p.id, p.set_id, p.card_id, p.collector_number, p.language,
                   p.is_foil, p.variant, p.print_key, p.tcgdex_id
            FROM prints p
            JOIN cards c ON c.id=p.card_id
            WHERE c.game_id=:game
            """
        ), {"game": game_id}).mappings().all()]

    sets_by_tcgdex = {str(row["tcgdex_id"]): row for row in sets if row.get("tcgdex_id")}
    cards_by_tcgdex = {str(row["tcgdex_id"]): row for row in cards if row.get("tcgdex_id")}
    cards_by_key = {str(row["card_key"]): row for row in cards if row.get("card_key")}
    prints_by_tcgdex = {str(row["tcgdex_id"]): row for row in prints if row.get("tcgdex_id")}
    prints_by_key = {str(row["print_key"]): row for row in prints if row.get("print_key")}

    tuple_owners: dict[tuple, list[dict]] = {}
    for row in prints:
        key = (
            int(row["set_id"]),
            str(row.get("collector_number") or ""),
            str(row.get("language") or "").lower(),
            bool(row.get("is_foil")),
            str(row.get("variant") or VARIANT),
        )
        tuple_owners.setdefault(key, []).append(row)

    actions = Counter()
    conflicts: list[dict] = []
    set_counts = Counter()

    for source_id, row in sorted(candidates.items()):
        set_source_id = str((row.get("set") or {}).get("id") or "")
        set_row = sets_by_tcgdex.get(set_source_id)
        if not set_row:
            conflicts.append({
                "type": "missing_canonical_set",
                "source_id": source_id,
                "set_id": set_source_id,
            })
            continue
        set_counts[set_source_id] += 1

        target_card_key = card_key(source_id)
        existing_card = cards_by_tcgdex.get(source_id)
        card_key_owner = cards_by_key.get(target_card_key)
        if existing_card:
            actions["card_existing"] += 1
            if card_key_owner and int(card_key_owner["id"]) != int(existing_card["id"]):
                conflicts.append({
                    "type": "card_key_collision",
                    "source_id": source_id,
                    "card_id": existing_card["id"],
                    "key_owner_id": card_key_owner["id"],
                })
        elif card_key_owner:
            owner_source = str(card_key_owner.get("tcgdex_id") or "")
            if owner_source and owner_source != source_id:
                conflicts.append({
                    "type": "card_key_owned_by_other_tcgdex",
                    "source_id": source_id,
                    "owner_source_id": owner_source,
                    "owner_card_id": card_key_owner["id"],
                })
            else:
                actions["card_safe_attach"] += 1
        else:
            actions["card_safe_insert"] += 1

        target_print_key = print_key(source_id)
        existing_print = prints_by_tcgdex.get(source_id)
        print_key_owner = prints_by_key.get(target_print_key)
        target_tuple = (
            int(set_row["id"]),
            str(row.get("local_id") or ""),
            LANGUAGE,
            IS_FOIL,
            VARIANT,
        )
        owners = tuple_owners.get(target_tuple, [])

        if existing_print:
            actions["print_existing"] += 1
            if print_key_owner and int(print_key_owner["id"]) != int(existing_print["id"]):
                conflicts.append({
                    "type": "print_key_collision",
                    "source_id": source_id,
                    "print_id": existing_print["id"],
                    "key_owner_id": print_key_owner["id"],
                })
            foreign_tuple_owners = [owner for owner in owners if int(owner["id"]) != int(existing_print["id"])]
            if foreign_tuple_owners:
                conflicts.append({
                    "type": "print_tuple_collision",
                    "source_id": source_id,
                    "print_id": existing_print["id"],
                    "tuple_owner_ids": [owner["id"] for owner in foreign_tuple_owners],
                })
        elif print_key_owner:
            owner_source = str(print_key_owner.get("tcgdex_id") or "")
            if owner_source and owner_source != source_id:
                conflicts.append({
                    "type": "print_key_owned_by_other_tcgdex",
                    "source_id": source_id,
                    "owner_source_id": owner_source,
                    "owner_print_id": print_key_owner["id"],
                })
            else:
                actions["print_safe_attach"] += 1
        elif owners:
            compatible = [owner for owner in owners if not owner.get("tcgdex_id") or str(owner.get("tcgdex_id")) == source_id]
            if len(owners) == 1 and len(compatible) == 1:
                actions["print_safe_attach_tuple"] += 1
            else:
                conflicts.append({
                    "type": "print_tuple_ambiguous",
                    "source_id": source_id,
                    "tuple_owner_ids": [owner["id"] for owner in owners],
                    "tuple_owner_tcgdex": [owner.get("tcgdex_id") for owner in owners],
                })
        else:
            actions["print_safe_insert"] += 1

    conflicts.sort(key=lambda row: (row["type"], row.get("source_id", "")))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_preflight",
        "source_version": manifest.get("source_version"),
        "rest_physical_baseline": len(rest_ids),
        "released_english_repo_extras": len(candidates),
        "candidate_sets": dict(set_counts),
        "plan": dict(actions),
        "conflicts": {
            "count": len(conflicts),
            "by_type": dict(Counter(row["type"] for row in conflicts)),
            "samples": conflicts[:200],
        },
        "candidate_samples": [
            {
                "source_id": source_id,
                "name": row.get("name"),
                "set_id": (row.get("set") or {}).get("id"),
                "set_name": (row.get("set") or {}).get("name"),
                "local_id": row.get("local_id"),
                "rarity": (row.get("attributes") or {}).get("rarity"),
            }
            for source_id, row in list(sorted(candidates.items()))[:200]
        ],
        "status": "pass" if not conflicts else "fail",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if conflicts:
        raise AssertionError(f"Rich-source Pokémon identity augmentation blocked by {len(conflicts)} collisions")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    run(args.snapshot, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
