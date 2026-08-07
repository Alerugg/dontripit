from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import text

from app import db
from app.pokemon_source_inventory import load_inventory


GAME_SLUG = "pokemon"
LANGUAGE = "en"
VARIANT = "default"
IS_FOIL = False


def _card_key(source_id: str) -> str:
    return f"pokemon:tcgdex:{source_id}"


def _print_key(source_id: str) -> str:
    return f"pokemon:tcgdex:{source_id}:{LANGUAGE}:{VARIANT}"


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def run() -> dict:
    inventory = load_inventory()
    physical_sets = {row["set_id"]: row for row in inventory.physical_sets}
    physical_cards = inventory.physical_cards

    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(
            text("SELECT id, slug, name FROM games WHERE slug=:slug LIMIT 1"),
            {"slug": GAME_SLUG},
        ).mappings().first()
        if not game:
            raise AssertionError("Pokémon game row missing from Neon")
        game_id = int(game["id"])

        db_sets = [dict(row) for row in session.execute(
            text("SELECT id, code, tcgdex_id, name FROM sets WHERE game_id=:game ORDER BY id"),
            {"game": game_id},
        ).mappings().all()]
        db_cards = [dict(row) for row in session.execute(
            text("SELECT id, name, card_key, tcgdex_id FROM cards WHERE game_id=:game ORDER BY id"),
            {"game": game_id},
        ).mappings().all()]
        db_prints = [dict(row) for row in session.execute(
            text(
                """
                SELECT p.id, p.set_id, p.card_id, p.collector_number, p.language,
                       p.is_foil, p.variant, p.print_key, p.tcgdex_id,
                       s.code AS set_code, s.tcgdex_id AS set_tcgdex_id,
                       c.tcgdex_id AS card_tcgdex_id, c.card_key AS card_key
                FROM prints p
                JOIN sets s ON s.id=p.set_id
                JOIN cards c ON c.id=p.card_id
                WHERE c.game_id=:game
                ORDER BY p.id
                """
            ),
            {"game": game_id},
        ).mappings().all()]

    sets_by_tcgdex = {str(row["tcgdex_id"]): row for row in db_sets if row.get("tcgdex_id")}
    sets_by_code = {_norm(row.get("code")): row for row in db_sets if row.get("code")}
    cards_by_tcgdex = {str(row["tcgdex_id"]): row for row in db_cards if row.get("tcgdex_id")}
    cards_by_key = {str(row["card_key"]): row for row in db_cards if row.get("card_key")}
    prints_by_tcgdex = {str(row["tcgdex_id"]): row for row in db_prints if row.get("tcgdex_id")}
    prints_by_key = {str(row["print_key"]): row for row in db_prints if row.get("print_key")}

    print_tuple_map: dict[tuple, list[dict]] = {}
    for row in db_prints:
        key = (
            int(row["set_id"]),
            str(row.get("collector_number") or ""),
            _norm(row.get("language")),
            bool(row.get("is_foil")),
            str(row.get("variant") or VARIANT),
        )
        print_tuple_map.setdefault(key, []).append(row)

    conflicts: list[dict] = []
    warnings: list[dict] = []
    set_actions = Counter()
    card_actions = Counter()
    print_actions = Counter()
    source_set_to_db_id: dict[str, int | None] = {}

    # Stage 1: resolve every physical TCGdex set without writing anything.
    for source_id, source in sorted(physical_sets.items()):
        exact = sets_by_tcgdex.get(source_id)
        code_match = sets_by_code.get(_norm(source_id))
        if exact:
            source_set_to_db_id[source_id] = int(exact["id"])
            set_actions["existing_exact"] += 1
            if code_match and int(code_match["id"]) != int(exact["id"]):
                conflicts.append({
                    "type": "set_code_collision",
                    "source_set_id": source_id,
                    "exact_set_id": exact["id"],
                    "code_owner_set_id": code_match["id"],
                })
        elif code_match:
            owner_source = str(code_match.get("tcgdex_id") or "")
            if owner_source and owner_source != source_id:
                conflicts.append({
                    "type": "set_code_owned_by_other_tcgdex",
                    "source_set_id": source_id,
                    "db_set_id": code_match["id"],
                    "db_tcgdex_id": owner_source,
                })
                source_set_to_db_id[source_id] = None
            else:
                source_set_to_db_id[source_id] = int(code_match["id"])
                set_actions["safe_attach_tcgdex_id"] += 1
        else:
            source_set_to_db_id[source_id] = None
            set_actions["safe_insert"] += 1

    # For target tuples on sets that will be inserted, use a synthetic stable
    # negative id. It only exists inside this read-only simulation.
    synthetic_set_ids = {
        source_id: -(index + 1)
        for index, source_id in enumerate(sorted(physical_sets))
        if source_set_to_db_id[source_id] is None
        and not any(c.get("source_set_id") == source_id for c in conflicts)
    }

    for source_id, source in sorted(physical_cards.items()):
        set_source_id = str(source.get("set_id") or "")
        if set_source_id not in physical_sets:
            conflicts.append({"type": "card_unresolved_set", "source_id": source_id, "set_id": set_source_id})
            continue

        target_card_key = _card_key(source_id)
        card_exact = cards_by_tcgdex.get(source_id)
        key_owner = cards_by_key.get(target_card_key)
        if card_exact:
            card_actions["existing_exact"] += 1
            if key_owner and int(key_owner["id"]) != int(card_exact["id"]):
                conflicts.append({
                    "type": "card_key_collision",
                    "source_id": source_id,
                    "exact_card_id": card_exact["id"],
                    "card_key_owner_id": key_owner["id"],
                    "target_card_key": target_card_key,
                })
            card_id = int(card_exact["id"])
        elif key_owner:
            owner_source = str(key_owner.get("tcgdex_id") or "")
            if owner_source and owner_source != source_id:
                conflicts.append({
                    "type": "card_key_owned_by_other_tcgdex",
                    "source_id": source_id,
                    "db_card_id": key_owner["id"],
                    "db_tcgdex_id": owner_source,
                })
                continue
            card_actions["safe_attach_tcgdex_id"] += 1
            card_id = int(key_owner["id"])
        else:
            card_actions["safe_insert"] += 1
            card_id = None

        target_print_key = _print_key(source_id)
        print_exact = prints_by_tcgdex.get(source_id)
        print_key_owner = prints_by_key.get(target_print_key)

        resolved_set_id = source_set_to_db_id.get(set_source_id)
        if resolved_set_id is None:
            resolved_set_id = synthetic_set_ids.get(set_source_id)
        if resolved_set_id is None:
            conflicts.append({"type": "print_unresolved_set", "source_id": source_id, "set_id": set_source_id})
            continue

        target_tuple = (
            int(resolved_set_id),
            str(source.get("local_id") or ""),
            LANGUAGE,
            IS_FOIL,
            VARIANT,
        )
        tuple_owners = print_tuple_map.get(target_tuple, []) if resolved_set_id > 0 else []

        if print_exact:
            print_actions["existing_exact"] += 1
            if print_key_owner and int(print_key_owner["id"]) != int(print_exact["id"]):
                conflicts.append({
                    "type": "print_key_collision",
                    "source_id": source_id,
                    "exact_print_id": print_exact["id"],
                    "print_key_owner_id": print_key_owner["id"],
                    "target_print_key": target_print_key,
                })
            for owner in tuple_owners:
                if int(owner["id"]) != int(print_exact["id"]):
                    conflicts.append({
                        "type": "physical_print_tuple_collision",
                        "source_id": source_id,
                        "exact_print_id": print_exact["id"],
                        "tuple_owner_print_id": owner["id"],
                        "set_id": resolved_set_id,
                        "collector_number": source.get("local_id"),
                    })
        else:
            if print_key_owner:
                owner_source = str(print_key_owner.get("tcgdex_id") or "")
                if owner_source and owner_source != source_id:
                    conflicts.append({
                        "type": "print_key_owned_by_other_tcgdex",
                        "source_id": source_id,
                        "db_print_id": print_key_owner["id"],
                        "db_tcgdex_id": owner_source,
                    })
                else:
                    print_actions["safe_attach_tcgdex_id"] += 1
            elif tuple_owners:
                compatible = [
                    owner for owner in tuple_owners
                    if not owner.get("tcgdex_id") or str(owner.get("tcgdex_id")) == source_id
                ]
                incompatible = [owner for owner in tuple_owners if owner not in compatible]
                if incompatible or len(compatible) != 1:
                    conflicts.append({
                        "type": "physical_print_tuple_ambiguous",
                        "source_id": source_id,
                        "tuple_owner_ids": [owner["id"] for owner in tuple_owners],
                        "tuple_owner_tcgdex_ids": [owner.get("tcgdex_id") for owner in tuple_owners],
                        "set_id": resolved_set_id,
                        "collector_number": source.get("local_id"),
                    })
                else:
                    print_actions["safe_attach_tcgdex_id"] += 1
            else:
                print_actions["safe_insert"] += 1

        if card_exact and _norm(card_exact.get("name")) != _norm(source.get("name")):
            warnings.append({
                "type": "existing_card_name_changed",
                "source_id": source_id,
                "db_card_id": card_exact["id"],
                "db_name": card_exact.get("name"),
                "source_name": source.get("name"),
            })

    current_source_ids = set(physical_cards)
    stale_card_ids = sorted(
        str(row["tcgdex_id"])
        for row in db_cards
        if row.get("tcgdex_id") and str(row["tcgdex_id"]) not in current_source_ids
    )
    stale_print_ids = sorted(
        str(row["tcgdex_id"])
        for row in db_prints
        if row.get("tcgdex_id") and str(row["tcgdex_id"]) not in current_source_ids
    )
    unidentified_cards = [row for row in db_cards if not row.get("tcgdex_id")]
    unidentified_prints = [row for row in db_prints if not row.get("tcgdex_id")]

    # De-duplicate identical conflict records before reporting.
    serialized_conflicts = {json.dumps(row, sort_keys=True, default=str): row for row in conflicts}
    conflicts = list(serialized_conflicts.values())
    conflicts.sort(key=lambda row: (str(row.get("type")), str(row.get("source_id") or row.get("source_set_id") or "")))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_preflight",
        "source": {
            "physical_sets": len(physical_sets),
            "physical_cards": len(physical_cards),
            "pocket_sets_excluded": len(inventory.pocket_sets),
            "pocket_cards_excluded": len(inventory.pocket_cards),
        },
        "target_identity": {
            "card_key": "pokemon:tcgdex:<source_id>",
            "print_key": "pokemon:tcgdex:<source_id>:en:default",
            "language": LANGUAGE,
            "variant": VARIANT,
            "is_foil": IS_FOIL,
            "note": "This is the bootstrap identity layer. Rich physical variants are expanded only after source-level identity is safe.",
        },
        "current_neon": {
            "sets": len(db_sets),
            "cards": len(db_cards),
            "prints": len(db_prints),
            "cards_without_tcgdex_id": len(unidentified_cards),
            "prints_without_tcgdex_id": len(unidentified_prints),
            "stale_card_tcgdex_ids": stale_card_ids,
            "stale_print_tcgdex_ids": stale_print_ids,
        },
        "plan": {
            "sets": dict(set_actions),
            "cards": dict(card_actions),
            "prints": dict(print_actions),
        },
        "warnings": {
            "count": len(warnings),
            "samples": warnings[:100],
        },
        "conflicts": {
            "count": len(conflicts),
            "by_type": dict(Counter(row["type"] for row in conflicts)),
            "samples": conflicts[:200],
        },
        "status": "pass" if not conflicts else "fail",
    }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if conflicts:
        raise AssertionError(f"Pokémon bootstrap V2 blocked by {len(conflicts)} ambiguous identity collisions")
    return report


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
