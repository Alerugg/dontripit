from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import db
from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.models import Card, Game, Print, PrintIdentifier, Set
from app.multilingual_models import PrintLocalization

SOURCE = "onepiece_official"
LANGUAGE = "en"


@dataclass(frozen=True)
class TargetRow:
    print_id: int
    external_id: str
    language: str
    collector_number: str
    variant: str
    card_name: str
    set_name: str


def _normalize_external_id(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_text(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def _canonical_details(source_print: dict) -> dict:
    raw = dict(source_print.get("details") or {})
    details = {
        "effect": _normalize_text(raw.get("effect")),
        "trigger": _normalize_text(raw.get("trigger")),
        "cost": _normalize_text(raw.get("cost")),
        "attribute": _normalize_text(raw.get("attribute")),
        "power": _normalize_text(raw.get("power")),
        "counter": _normalize_text(raw.get("counter")),
        "color": _normalize_text(raw.get("color")),
        "block": _normalize_text(raw.get("block")),
        "card_type": _normalize_text(raw.get("card_type")),
        "official": raw.get("official") is True,
        "source": SOURCE,
        "source_print_id": _normalize_external_id(source_print.get("id")),
        "collector_number": _normalize_text(source_print.get("collector_number")),
        "variant": _normalize_text(source_print.get("variant")) or "default",
    }
    return details


def _remote_source_rows(payload: dict) -> tuple[dict[str, dict], list[dict]]:
    rows: dict[str, dict] = {}
    duplicates: list[dict] = []
    for card in payload.get("cards") or []:
        card_name = _normalize_text(card.get("name"))
        for source_print in card.get("prints") or []:
            external_id = _normalize_external_id(source_print.get("id"))
            if not external_id:
                continue
            row = {
                "external_id": external_id,
                "card_name": card_name,
                "source_print": source_print,
                "details": _canonical_details(source_print),
            }
            previous = rows.get(external_id)
            if previous is not None and previous != row:
                duplicates.append(
                    {
                        "external_id": external_id,
                        "first": previous,
                        "second": row,
                    }
                )
                continue
            rows[external_id] = row
    return rows, duplicates


def _database_targets(session) -> tuple[dict[str, list[TargetRow]], dict[int, PrintLocalization]]:
    game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
    if game is None:
        raise RuntimeError("One Piece game is missing from the canonical database")

    rows = session.execute(
        select(
            Print.id,
            PrintIdentifier.external_id,
            Print.language,
            Print.collector_number,
            Print.variant,
            Card.name,
            Set.name,
        )
        .join(PrintIdentifier, PrintIdentifier.print_id == Print.id)
        .join(Card, Card.id == Print.card_id)
        .join(Set, Set.id == Print.set_id)
        .where(
            Card.game_id == game.id,
            PrintIdentifier.source == SOURCE,
        )
        .order_by(Print.id.asc())
    ).all()

    by_external: dict[str, list[TargetRow]] = {}
    print_ids: list[int] = []
    for row in rows:
        target = TargetRow(
            print_id=int(row[0]),
            external_id=_normalize_external_id(row[1]),
            language=str(row[2] or "").strip().lower(),
            collector_number=str(row[3] or "").strip(),
            variant=str(row[4] or "default").strip().lower() or "default",
            card_name=str(row[5] or "").strip(),
            set_name=str(row[6] or "").strip(),
        )
        by_external.setdefault(target.external_id, []).append(target)
        print_ids.append(target.print_id)

    existing: dict[int, PrintLocalization] = {}
    if print_ids:
        localizations = session.execute(
            select(PrintLocalization).where(
                PrintLocalization.print_id.in_(print_ids),
                PrintLocalization.language == LANGUAGE,
            )
        ).scalars().all()
        existing = {int(row.print_id): row for row in localizations}
    return by_external, existing


def _same_payload(existing: PrintLocalization, proposal: dict) -> bool:
    return (
        str(existing.source or "") == proposal["source"]
        and _normalize_external_id(existing.external_id) == proposal["external_id"]
        and _normalize_text(existing.card_name) == proposal["card_name"]
        and _normalize_text(existing.set_name) == proposal["set_name"]
        and dict(existing.details_json or {}) == proposal["details_json"]
    )


def build_plan(payload: dict, session) -> dict:
    source_rows, source_duplicates = _remote_source_rows(payload)
    targets_by_external, existing = _database_targets(session)

    diagnostics = payload.get("diagnostics") or {}
    source_text_conflicts = diagnostics.get("source_text_conflicts") or []

    proposals: list[dict] = []
    already_current: list[int] = []
    unresolved_source: list[str] = []
    ambiguous_targets: list[dict] = []
    language_mismatches: list[dict] = []
    conflicting_localizations: list[dict] = []

    for external_id, source_row in sorted(source_rows.items()):
        candidates = targets_by_external.get(external_id) or []
        if not candidates:
            unresolved_source.append(external_id)
            continue
        if len(candidates) != 1:
            ambiguous_targets.append(
                {
                    "external_id": external_id,
                    "print_ids": [row.print_id for row in candidates],
                }
            )
            continue

        target = candidates[0]
        if target.language != LANGUAGE:
            language_mismatches.append(
                {
                    "external_id": external_id,
                    "print_id": target.print_id,
                    "database_language": target.language,
                    "source_language": LANGUAGE,
                }
            )
            continue

        proposal = {
            "print_id": target.print_id,
            "language": LANGUAGE,
            "source": SOURCE,
            "external_id": external_id,
            "card_name": source_row["card_name"] or target.card_name,
            "set_name": target.set_name,
            "details_json": source_row["details"],
        }
        prior = existing.get(target.print_id)
        if prior is None:
            proposal["action"] = "insert"
            proposals.append(proposal)
            continue
        if str(prior.source or "") != SOURCE:
            conflicting_localizations.append(
                {
                    "print_id": target.print_id,
                    "external_id": external_id,
                    "existing_source": prior.source,
                }
            )
            continue
        if _same_payload(prior, proposal):
            already_current.append(target.print_id)
            continue
        proposal["action"] = "update"
        proposals.append(proposal)

    source_external_ids = set(source_rows)
    database_external_ids = set(targets_by_external)
    database_without_source = sorted(database_external_ids - source_external_ids)

    effect_rows = sum(
        1 for row in source_rows.values() if _normalize_text(row["details"].get("effect"))
    )
    trigger_rows = sum(
        1 for row in source_rows.values() if _normalize_text(row["details"].get("trigger"))
    )
    insert_count = sum(1 for row in proposals if row["action"] == "insert")
    update_count = sum(1 for row in proposals if row["action"] == "update")

    blockers = {
        "source_duplicate_external_ids": len(source_duplicates),
        "source_text_conflicts": len(source_text_conflicts),
        "ambiguous_database_targets": len(ambiguous_targets),
        "language_mismatches": len(language_mismatches),
        "conflicting_existing_localizations": len(conflicting_localizations),
    }

    return {
        "schema_version": 1,
        "strategy": "onepiece_official_exact_identifier_text_backfill",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "source": {
            "language": LANGUAGE,
            "print_rows": len(source_rows),
            "effect_rows": effect_rows,
            "trigger_rows": trigger_rows,
            "source_text_conflicts": len(source_text_conflicts),
        },
        "database": {
            "identifier_external_ids": len(database_external_ids),
            "existing_en_localizations": len(existing),
        },
        "matching": {
            "unresolved_source_count": len(unresolved_source),
            "database_without_source_count": len(database_without_source),
            "ambiguous_target_count": len(ambiguous_targets),
            "language_mismatch_count": len(language_mismatches),
        },
        "proposals": {
            "count": len(proposals),
            "insert_count": insert_count,
            "update_count": update_count,
            "already_current_count": len(already_current),
        },
        "blockers": blockers,
        "safe_to_apply": all(value == 0 for value in blockers.values()),
        "samples": {
            "unresolved_source": unresolved_source[:25],
            "database_without_source": database_without_source[:25],
            "source_duplicates": source_duplicates[:10],
            "source_text_conflicts": source_text_conflicts[:10],
            "ambiguous_targets": ambiguous_targets[:10],
            "language_mismatches": language_mismatches[:10],
            "conflicting_localizations": conflicting_localizations[:10],
            "proposals": proposals[:10],
        },
        "_proposals": proposals,
    }


def _apply_plan(session, plan: dict) -> int:
    writes = 0
    for proposal in plan["_proposals"]:
        action = proposal["action"]
        values = {
            "print_id": proposal["print_id"],
            "language": proposal["language"],
            "source": proposal["source"],
            "external_id": proposal["external_id"],
            "card_name": proposal["card_name"],
            "set_name": proposal["set_name"],
            "details_json": proposal["details_json"],
        }
        if action == "insert":
            session.add(PrintLocalization(**values))
            writes += 1
            continue
        if action == "update":
            row = session.execute(
                select(PrintLocalization).where(
                    PrintLocalization.print_id == proposal["print_id"],
                    PrintLocalization.language == LANGUAGE,
                )
            ).scalar_one()
            row.source = values["source"]
            row.external_id = values["external_id"]
            row.card_name = values["card_name"]
            row.set_name = values["set_name"]
            row.details_json = values["details_json"]
            writes += 1
            continue
        raise RuntimeError(f"Unsupported proposal action: {action}")
    session.flush()
    return writes


def _public_plan(plan: dict) -> dict:
    return {key: value for key, value in plan.items() if key != "_proposals"}


def run(*, apply: bool, expected_proposals: int | None) -> dict:
    # Fetch and parse the entire official source before opening any potential
    # write transaction. Network failures can never leave a partial backfill.
    connector = OnePieceV2Connector()
    source_payload = connector._load_official_cardlist_remote(limit=None)

    db.init_engine()
    with db.SessionLocal() as session:
        plan = build_plan(source_payload, session)
        proposal_count = int(plan["proposals"]["count"])

        if expected_proposals is not None and proposal_count != expected_proposals:
            raise RuntimeError(
                f"proposal count changed: expected={expected_proposals} actual={proposal_count}"
            )

        writes = 0
        if apply:
            if expected_proposals is None:
                raise RuntimeError("--apply requires --expected-proposals")
            if not plan["safe_to_apply"]:
                raise RuntimeError(f"One Piece official text backfill blocked: {plan['blockers']}")
            writes = _apply_plan(session, plan)
            if writes != expected_proposals:
                raise RuntimeError(
                    f"write count changed inside transaction: expected={expected_proposals} actual={writes}"
                )
            session.commit()
        else:
            session.rollback()

    result = _public_plan(plan)
    result["read_only"] = not apply
    result["production_writes"] = writes
    result["applied"] = apply
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill One Piece official EN text onto existing exact prints. Read-only by default."
    )
    parser.add_argument("--apply", action="store_true", help="Apply the certified proposal set")
    parser.add_argument("--expected-proposals", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = run(apply=args.apply, expected_proposals=args.expected_proposals)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())