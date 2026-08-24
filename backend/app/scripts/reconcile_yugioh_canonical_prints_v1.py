from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Card, Game, Print, Set
from app.scripts.build_yugioh_v2_snapshot_canonical import run as build_snapshot
from app.scripts.reindex_search import rebuild_search_documents

ACK_ENV = "YUGIOH_CANONICAL_PRINT_RECONCILE_ACK"
ACK_VALUE = "YGO_CANONICAL_PRINTS_V1"
DEFAULT_MAX_WRITES = 500


def _load_source_prints(root: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((root / "manifest.json").read_text())
    rows = [
        json.loads(line)
        for line in (root / "prints.jsonl").read_text().splitlines()
        if line.strip()
    ]
    keys = [str(row["print_key"]) for row in rows]
    yugioh_ids = [str(row["yugioh_id"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise AssertionError("canonical source print_key uniqueness failed")
    if len(yugioh_ids) != len(set(yugioh_ids)):
        raise AssertionError("canonical source yugioh_id uniqueness failed")
    return manifest, rows


def _plan(session: Session, source_rows: list[dict], *, max_writes: int) -> dict:
    game_id = session.scalar(select(Game.id).where(Game.slug == "yugioh"))
    if game_id is None:
        raise AssertionError("Yu-Gi-Oh game row is missing")

    db_keys = {
        str(value)
        for value in session.scalars(
            select(Print.print_key)
            .join(Card, Card.id == Print.card_id)
            .where(Card.game_id == game_id, Print.print_key.is_not(None))
        )
    }
    missing = [row for row in source_rows if str(row["print_key"]) not in db_keys]
    if len(missing) > max_writes:
        raise AssertionError(
            f"current source requires {len(missing)} canonical Print writes; max_writes={max_writes}"
        )

    planned: list[dict] = []
    failures: list[dict] = []
    for row in missing:
        source_card_id = str(row["source_card_id"])
        set_family = str(row["set_family"])
        cards = session.scalars(
            select(Card).where(
                Card.game_id == game_id,
                Card.yugoprodeck_id == source_card_id,
            )
        ).all()
        sets = session.scalars(
            select(Set).where(
                Set.game_id == game_id,
                func.lower(Set.code) == set_family.lower(),
                func.lower(Set.region) == "global",
            )
        ).all()
        if len(cards) != 1:
            failures.append(
                {
                    "print_key": row["print_key"],
                    "reason": "card_resolution",
                    "source_card_id": source_card_id,
                    "matches": len(cards),
                }
            )
            continue
        if len(sets) != 1:
            failures.append(
                {
                    "print_key": row["print_key"],
                    "reason": "set_resolution",
                    "set_family": set_family,
                    "matches": len(sets),
                }
            )
            continue

        card = cards[0]
        set_row = sets[0]
        tuple_conflicts = session.scalars(
            select(Print).where(
                Print.card_id == card.id,
                Print.set_id == set_row.id,
                Print.collector_number == str(row["collector_number"]),
                Print.language == row.get("language"),
                Print.is_foil == bool(row.get("is_foil", False)),
                Print.variant == str(row["variant"]),
            )
        ).all()
        id_conflicts = session.scalars(
            select(Print).where(Print.yugioh_id == str(row["yugioh_id"]))
        ).all()
        if tuple_conflicts:
            failures.append(
                {
                    "print_key": row["print_key"],
                    "reason": "physical_tuple_conflict",
                    "existing_ids": [int(value.id) for value in tuple_conflicts],
                }
            )
            continue
        if id_conflicts:
            failures.append(
                {
                    "print_key": row["print_key"],
                    "reason": "yugioh_id_conflict",
                    "existing_ids": [int(value.id) for value in id_conflicts],
                }
            )
            continue

        planned.append(
            {
                "source": row,
                "card_id": int(card.id),
                "set_id": int(set_row.id),
            }
        )

    if failures:
        raise AssertionError(
            "canonical Print reconciliation safety gate failed: "
            + json.dumps(failures[:20], sort_keys=True)
        )

    return {
        "game_id": int(game_id),
        "db_keys_before": db_keys,
        "missing_before": len(missing),
        "planned": planned,
    }


def reconcile(
    *,
    output_dir: Path,
    report_path: Path,
    apply: bool,
    max_writes: int,
) -> dict:
    if apply and os.getenv(ACK_ENV) != ACK_VALUE:
        raise SystemExit(f"--apply requires {ACK_ENV}={ACK_VALUE}")
    if max_writes < 0:
        raise SystemExit("max_writes must be >= 0")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_snapshot(output_dir=output_dir)
    manifest_file, source_rows = _load_source_prints(output_dir)
    if manifest_file.get("counts") != manifest.get("counts"):
        raise AssertionError("snapshot manifest mismatch")

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(database_url)
    inserted_ids: list[int] = []
    touched_cards: set[int] = set()
    touched_sets: set[int] = set()
    plan_summary: dict | None = None

    with Session(engine) as session:
        try:
            plan_summary = _plan(session, source_rows, max_writes=max_writes)
            planned = plan_summary["planned"]
            if apply:
                for item in planned:
                    row = item["source"]
                    record = Print(
                        card_id=item["card_id"],
                        set_id=item["set_id"],
                        collector_number=str(row["collector_number"]),
                        language=row.get("language"),
                        rarity=row.get("rarity"),
                        is_foil=bool(row.get("is_foil", False)),
                        variant=str(row["variant"]),
                        print_key=str(row["print_key"]),
                        yugioh_id=str(row["yugioh_id"]),
                    )
                    session.add(record)
                    session.flush()
                    inserted_ids.append(int(record.id))
                    touched_cards.add(int(item["card_id"]))
                    touched_sets.add(int(item["set_id"]))

                if inserted_ids:
                    rebuild_search_documents(
                        session,
                        card_ids=touched_cards,
                        set_ids=touched_sets,
                        print_ids=set(inserted_ids),
                    )

                source_keys = {str(row["print_key"]) for row in source_rows}
                db_after = {
                    str(value)
                    for value in session.scalars(
                        select(Print.print_key)
                        .join(Card, Card.id == Print.card_id)
                        .where(
                            Card.game_id == plan_summary["game_id"],
                            Print.print_key.is_not(None),
                        )
                    )
                }
                missing_after = len(source_keys - db_after)
                if missing_after:
                    raise AssertionError(
                        f"post-write source coverage still has {missing_after} missing print_keys"
                    )
                session.commit()
            else:
                session.rollback()
                missing_after = plan_summary["missing_before"]
        except Exception:
            session.rollback()
            raise

    report = {
        "status": "pass",
        "writer": "ygo-canonical-print-reconcile-v1",
        "apply": bool(apply),
        "source_counts": manifest.get("counts", {}),
        "source_prints": len(source_rows),
        "missing_before": int(plan_summary["missing_before"] if plan_summary else 0),
        "planned_writes": len(plan_summary["planned"] if plan_summary else []),
        "canonical_print_writes": len(inserted_ids),
        "missing_after": int(missing_after),
        "max_writes": int(max_writes),
        "targeted_reindex": bool(inserted_ids),
        "touched_cards": len(touched_cards),
        "touched_sets": len(touched_sets),
        "touched_prints": len(inserted_ids),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely reconcile missing exact Yu-Gi-Oh canonical physical Prints."
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/ygo-current"))
    parser.add_argument("--report", type=Path, default=Path("/tmp/ygo-canonical-print-reconcile-v1.json"))
    parser.add_argument("--max-writes", type=int, default=DEFAULT_MAX_WRITES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    reconcile(
        output_dir=args.output_dir,
        report_path=args.report,
        apply=args.apply,
        max_writes=args.max_writes,
    )


if __name__ == "__main__":
    main()
