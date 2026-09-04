from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select, text

from app import db
from app.models import Card, Game, Price, PriceSnapshot, Print, PrintIdentifier, Product, Set
from app.scripts.reindex_search import rebuild_search_documents


CONFIRM_TOKEN = "APPLY_MTG_CURRENT_SOURCE_V1"
MAX_NEW_SETS = 10
MAX_NEW_CARDS = 50
MAX_NEW_PRINTS = 500
MAX_COLLECTOR_CORRECTIONS = 100


@dataclass(frozen=True)
class Plan:
    new_sets: tuple[str, ...]
    new_cards: tuple[str, ...]
    new_prints: tuple[str, ...]
    collector_corrections: tuple[str, ...]
    forbidden_mismatches: tuple[str, ...]

    @property
    def write_count(self) -> int:
        return len(self.new_sets) + len(self.new_cards) + len(self.new_prints) + len(self.collector_corrections)


def _jsonl(path: Path, *, key_field: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise AssertionError(f"{path.name}:{line_number} is not an object")
            key = row.get(key_field)
            if not key:
                raise AssertionError(f"{path.name}:{line_number} has no {key_field}")
            key = str(key)
            if key in rows:
                raise AssertionError(f"duplicate snapshot identity {key!r} in {path.name}")
            rows[key] = row
    return rows


def load_snapshot(snapshot_dir: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict]:
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise AssertionError("MTG snapshot source gates are not green")
    if manifest.get("snapshot_schema_version") != "mtg-canonical-v2.2":
        raise AssertionError(f"unexpected MTG snapshot schema {manifest.get('snapshot_schema_version')!r}")
    gates = manifest.get("gates") or {}
    for key in (
        "duplicate_paper_scryfall_ids",
        "exact_print_key_collisions",
        "natural_exact_print_collisions",
        "unknown_finishes",
        "missing_scryfall_ids",
    ):
        if int(gates.get(key, -1)) != 0:
            raise AssertionError(f"snapshot gate {key} is not zero")

    sets = _jsonl(snapshot_dir / "sets.jsonl", key_field="code")
    cards = _jsonl(snapshot_dir / "cards.jsonl", key_field="card_key")
    prints = _jsonl(snapshot_dir / "prints.jsonl", key_field="print_key")
    counts = manifest.get("counts") or {}
    if len(sets) != int(counts.get("sets") or -1):
        raise AssertionError("snapshot set count mismatch")
    if len(cards) != int(counts.get("logical_cards") or -1):
        raise AssertionError("snapshot card count mismatch")
    if len(prints) != int(counts.get("exact_prints") or -1):
        raise AssertionError("snapshot print count mismatch")
    return sets, cards, prints, manifest


def build_plan(*, source_sets: dict[str, dict], source_cards: dict[str, dict], source_prints: dict[str, dict], prod_sets: dict[str, dict], prod_cards: dict[str, dict], prod_prints: dict[str, dict]) -> Plan:
    new_sets = sorted(set(source_sets) - set(prod_sets))
    new_cards = sorted(set(source_cards) - set(prod_cards))
    new_prints = sorted(set(source_prints) - set(prod_prints))

    collector_corrections: list[str] = []
    forbidden: list[str] = []
    for print_key in sorted(set(source_prints) & set(prod_prints)):
        src = source_prints[print_key]
        prod = prod_prints[print_key]
        fields = {
            "card_key": (src.get("card_key"), prod.get("card_key")),
            "set_code": (src.get("set_code"), prod.get("set_code")),
            "language": (src.get("language"), prod.get("language")),
            "rarity": (src.get("rarity"), prod.get("rarity")),
            "is_foil": (bool(src.get("is_foil")), bool(prod.get("is_foil"))),
            "variant": (src.get("variant"), prod.get("variant")),
            "scryfall_id": (src.get("scryfall_id"), prod.get("scryfall_id")),
        }
        differing = [name for name, (expected, actual) in fields.items() if expected != actual]
        collector_diff = str(src.get("collector_number") or "") != str(prod.get("collector_number") or "")
        if differing:
            forbidden.append(f"{print_key}:" + ",".join(differing))
        elif collector_diff:
            collector_corrections.append(print_key)

    plan = Plan(
        new_sets=tuple(new_sets),
        new_cards=tuple(new_cards),
        new_prints=tuple(new_prints),
        collector_corrections=tuple(collector_corrections),
        forbidden_mismatches=tuple(forbidden),
    )
    assert_plan_bounds(plan)
    return plan


def assert_plan_bounds(plan: Plan) -> None:
    if plan.forbidden_mismatches:
        raise AssertionError(f"current source has forbidden identity/field drift: {plan.forbidden_mismatches[:10]!r}")
    checks = (
        ("new_sets", len(plan.new_sets), MAX_NEW_SETS),
        ("new_cards", len(plan.new_cards), MAX_NEW_CARDS),
        ("new_prints", len(plan.new_prints), MAX_NEW_PRINTS),
        ("collector_corrections", len(plan.collector_corrections), MAX_COLLECTOR_CORRECTIONS),
    )
    for label, actual, ceiling in checks:
        if actual > ceiling:
            raise AssertionError(f"MTG reconciler ceiling exceeded for {label}: {actual}>{ceiling}")


def _economics(session, game_id: int) -> dict[str, int]:
    values = {
        "prices": int(session.execute(select(func.count(Price.id)).where(Price.game_id == game_id)).scalar_one()),
        "products": int(session.execute(select(func.count(Product.id)).where(Product.game_id == game_id)).scalar_one()),
    }
    card_ids = select(Card.id).where(Card.game_id == game_id)
    print_ids = select(Print.id).join(Card, Card.id == Print.card_id).where(Card.game_id == game_id)
    values["price_snapshots"] = int(
        session.execute(
            select(func.count(PriceSnapshot.id)).where(
                ((PriceSnapshot.entity_type == "card") & PriceSnapshot.entity_id.in_(card_ids))
                | ((PriceSnapshot.entity_type == "print") & PriceSnapshot.entity_id.in_(print_ids))
            )
        ).scalar_one()
    )
    return values


def _production_state(session, game_id: int) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    prod_sets = {
        str(row.code): {"id": row.id, "name": row.name, "release_date": row.release_date.isoformat() if row.release_date else None}
        for row in session.execute(select(Set).where(Set.game_id == game_id)).scalars().all()
    }
    prod_cards = {
        str(row.card_key): {"id": row.id, "name": row.name, "oracle_id": row.oracle_id}
        for row in session.execute(select(Card).where(Card.game_id == game_id, Card.card_key.is_not(None))).scalars().all()
    }
    rows = session.execute(
        select(Print, Set.code, Card.card_key)
        .join(Set, Set.id == Print.set_id)
        .join(Card, Card.id == Print.card_id)
        .where(Card.game_id == game_id, Print.print_key.is_not(None))
    ).all()
    prod_prints = {}
    for row, set_code, card_key in rows:
        key = str(row.print_key)
        if not key.startswith("mtg:scryfall:"):
            continue
        if key in prod_prints:
            raise AssertionError(f"duplicate production print_key {key}")
        prod_prints[key] = {
            "id": row.id,
            "card_id": row.card_id,
            "set_id": row.set_id,
            "card_key": str(card_key) if card_key else None,
            "set_code": str(set_code),
            "collector_number": str(row.collector_number),
            "language": row.language,
            "rarity": row.rarity,
            "is_foil": bool(row.is_foil),
            "variant": row.variant,
            "scryfall_id": row.scryfall_id,
        }
    return prod_sets, prod_cards, prod_prints


def _assert_card_identity_free(session, *, game_id: int, card_key: str, oracle_id: str | None) -> None:
    if oracle_id:
        conflict = session.execute(
            select(Card).where(Card.game_id == game_id, Card.oracle_id == oracle_id, Card.card_key != card_key)
        ).scalar_one_or_none()
        if conflict is not None:
            raise AssertionError(f"oracle_id {oracle_id} already belongs to card_id={conflict.id} card_key={conflict.card_key}")


def _assert_print_identity_free(session, *, print_key: str, card_id: int, set_id: int, source: dict, exclude_id: int | None = None) -> None:
    scryfall_query = select(Print).where(
        Print.scryfall_id == source.get("scryfall_id"),
        Print.variant == source.get("variant"),
    )
    natural_query = select(Print).where(
        Print.card_id == card_id,
        Print.set_id == set_id,
        Print.collector_number == str(source.get("collector_number") or ""),
        Print.language == source.get("language"),
        Print.is_foil.is_(bool(source.get("is_foil"))),
        Print.variant == source.get("variant"),
    )
    if exclude_id is not None:
        scryfall_query = scryfall_query.where(Print.id != exclude_id)
        natural_query = natural_query.where(Print.id != exclude_id)
    scryfall_conflict = session.execute(scryfall_query).scalars().first()
    if scryfall_conflict is not None:
        raise AssertionError(f"Scryfall+finish identity for {print_key} already belongs to print_id={scryfall_conflict.id}")
    natural_conflict = session.execute(natural_query).scalars().first()
    if natural_conflict is not None:
        raise AssertionError(f"natural identity for {print_key} already belongs to print_id={natural_conflict.id}")


def apply_plan(session, *, game_id: int, plan: Plan, source_sets: dict[str, dict], source_cards: dict[str, dict], source_prints: dict[str, dict], prod_sets: dict[str, dict], prod_cards: dict[str, dict], prod_prints: dict[str, dict]) -> dict:
    touched = {"card_ids": set(), "set_ids": set(), "print_ids": set()}

    for code in plan.new_sets:
        src = source_sets[code]
        raw_date = src.get("release_date")
        release_date = date.fromisoformat(raw_date) if raw_date else None
        row = Set(game_id=game_id, code=code, region="global", name=str(src.get("name") or code.upper()), release_date=release_date)
        session.add(row)
        session.flush()
        prod_sets[code] = {"id": row.id, "name": row.name, "release_date": raw_date}
        touched["set_ids"].add(row.id)

    for card_key in plan.new_cards:
        src = source_cards[card_key]
        oracle_id = str(src.get("oracle_id")) if src.get("oracle_id") else None
        _assert_card_identity_free(session, game_id=game_id, card_key=card_key, oracle_id=oracle_id)
        row = Card(game_id=game_id, name=str(src.get("name") or "").strip(), card_key=card_key, oracle_id=oracle_id)
        if not row.name:
            raise AssertionError(f"new Card {card_key} has empty name")
        session.add(row)
        session.flush()
        prod_cards[card_key] = {"id": row.id, "name": row.name, "oracle_id": row.oracle_id}
        touched["card_ids"].add(row.id)

    for print_key in plan.new_prints:
        src = source_prints[print_key]
        card_key = str(src.get("card_key") or "")
        set_code = str(src.get("set_code") or "")
        if card_key not in prod_cards or set_code not in prod_sets:
            raise AssertionError(f"missing parent identity for new Print {print_key}")
        card_id = int(prod_cards[card_key]["id"])
        set_id = int(prod_sets[set_code]["id"])
        _assert_print_identity_free(session, print_key=print_key, card_id=card_id, set_id=set_id, source=src)
        row = Print(
            card_id=card_id,
            set_id=set_id,
            collector_number=str(src.get("collector_number") or ""),
            language=src.get("language"),
            rarity=src.get("rarity"),
            is_foil=bool(src.get("is_foil")),
            variant=str(src.get("variant") or ""),
            print_key=print_key,
            scryfall_id=str(src.get("scryfall_id") or "") or None,
        )
        session.add(row)
        session.flush()
        session.add(PrintIdentifier(print_id=row.id, source="scryfall", external_id=str(src.get("scryfall_id") or "")))
        touched["print_ids"].add(row.id)
        touched["card_ids"].add(card_id)
        touched["set_ids"].add(set_id)

    for print_key in plan.collector_corrections:
        src = source_prints[print_key]
        prod = prod_prints[print_key]
        row = session.get(Print, int(prod["id"]))
        if row is None:
            raise AssertionError(f"production Print disappeared during reconciliation: {print_key}")
        _assert_print_identity_free(
            session,
            print_key=print_key,
            card_id=row.card_id,
            set_id=row.set_id,
            source=src,
            exclude_id=row.id,
        )
        row.collector_number = str(src.get("collector_number") or "")
        touched["print_ids"].add(row.id)
        touched["card_ids"].add(row.card_id)
        touched["set_ids"].add(row.set_id)

    if any(touched.values()):
        rebuild_search_documents(
            session,
            card_ids=touched["card_ids"],
            set_ids=touched["set_ids"],
            print_ids=touched["print_ids"],
        )
    return {key: sorted(value) for key, value in touched.items()}


def run(*, snapshot_dir: Path, output: Path, apply: bool, confirm: str | None) -> dict:
    source_sets, source_cards, source_prints, manifest = load_snapshot(snapshot_dir)
    db.init_engine()
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "mtg")).scalar_one()
        prod_sets, prod_cards, prod_prints = _production_state(session, game.id)
        economics_before = _economics(session, game.id)
        plan = build_plan(
            source_sets=source_sets,
            source_cards=source_cards,
            source_prints=source_prints,
            prod_sets=prod_sets,
            prod_cards=prod_cards,
            prod_prints=prod_prints,
        )

        touched = {"card_ids": [], "set_ids": [], "print_ids": []}
        if apply:
            if confirm != CONFIRM_TOKEN:
                raise AssertionError("production apply requires exact confirmation token")
            read_only = str(session.execute(text("SHOW transaction_read_only")).scalar_one()).lower()
            if read_only == "on":
                raise AssertionError("cannot apply MTG reconciliation in a read-only transaction")
            touched = apply_plan(
                session,
                game_id=game.id,
                plan=plan,
                source_sets=source_sets,
                source_cards=source_cards,
                source_prints=source_prints,
                prod_sets=prod_sets,
                prod_cards=prod_cards,
                prod_prints=prod_prints,
            )
            economics_after = _economics(session, game.id)
            if economics_after != economics_before:
                raise AssertionError(f"MTG economics changed unexpectedly: before={economics_before} after={economics_after}")
            session.commit()
        else:
            economics_after = economics_before
            session.rollback()

    report = {
        "status": "pass",
        "mode": "apply" if apply else "dry-run",
        "source": manifest.get("source"),
        "snapshot_schema_version": manifest.get("snapshot_schema_version"),
        "production_writes": plan.write_count if apply else 0,
        "planned_writes": plan.write_count,
        "plan": {
            "new_sets": len(plan.new_sets),
            "new_cards": len(plan.new_cards),
            "new_prints": len(plan.new_prints),
            "collector_corrections": len(plan.collector_corrections),
            "forbidden_mismatches": len(plan.forbidden_mismatches),
        },
        "samples": {
            "new_sets": list(plan.new_sets[:25]),
            "new_cards": list(plan.new_cards[:25]),
            "new_prints": list(plan.new_prints[:25]),
            "collector_corrections": list(plan.collector_corrections[:25]),
        },
        "safety": {
            "deletes": 0,
            "image_writes": 0,
            "cardmarket_writes": 0,
            "price_writes": 0,
            "historical_or_localized_extra_prints_preserved": True,
            "generic_scryfall_writer_quarantine_relaxed": False,
            "ceilings": {
                "new_sets": MAX_NEW_SETS,
                "new_cards": MAX_NEW_CARDS,
                "new_prints": MAX_NEW_PRINTS,
                "collector_corrections": MAX_COLLECTOR_CORRECTIONS,
            },
        },
        "economics_before": economics_before,
        "economics_after": economics_after,
        "touched": touched,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded additive/corrective MTG current-Scryfall reconciler")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default=os.getenv("MTG_RECONCILE_CONFIRM"))
    args = parser.parse_args()
    run(snapshot_dir=args.snapshot_dir, output=args.output, apply=args.apply, confirm=args.confirm)


if __name__ == "__main__":
    main()
