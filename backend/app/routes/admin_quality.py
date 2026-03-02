from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request
from sqlalchemy import and_, func, select

from app import db
from app.models import Card, Game, IngestRun, Print, PrintFieldProvenance, PrintIdentifier, PrintImage, Set

admin_quality_bp = Blueprint("admin_quality", __name__, url_prefix="/api/v1/admin/quality")


def _enabled() -> bool:
    return bool(current_app.config.get("ADMIN_ENDPOINTS_ENABLED", False))


def _guard():
    if not _enabled():
        abort(404)


@admin_quality_bp.get("/summary")
def quality_summary():
    _guard()
    with db.SessionLocal() as session:
        totals = {
            "games": session.execute(select(func.count(Game.id))).scalar_one(),
            "sets": session.execute(select(func.count(Set.id))).scalar_one(),
            "cards": session.execute(select(func.count(Card.id))).scalar_one(),
            "prints": session.execute(select(func.count(Print.id))).scalar_one(),
            "prints_without_primary_image": session.execute(
                select(func.count(Print.id)).where(~select(PrintImage.id).where(and_(PrintImage.print_id == Print.id, PrintImage.is_primary.is_(True))).exists())
            ).scalar_one(),
            "prints_without_identifiers": session.execute(
                select(func.count(Print.id)).where(~select(PrintIdentifier.id).where(PrintIdentifier.print_id == Print.id).exists())
            ).scalar_one(),
            "sets_without_release_date": session.execute(select(func.count(Set.id)).where(Set.release_date.is_(None))).scalar_one(),
        }
        coverage = session.execute(
            select(Game.slug, func.count(func.distinct(Set.id)).label("sets"), func.count(func.distinct(Print.id)).label("prints"))
            .select_from(Game)
            .join(Set, Set.game_id == Game.id, isouter=True)
            .join(Print, Print.set_id == Set.id, isouter=True)
            .group_by(Game.slug)
        ).all()
        latest_runs = session.execute(select(IngestRun).order_by(IngestRun.started_at.desc()).limit(10)).scalars().all()
    return jsonify(
        {
            **totals,
            "coverage_by_game": [dict(row._mapping) for row in coverage],
            "last_ingest_runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                    "counts": run.counts_json,
                }
                for run in latest_runs
            ],
        }
    )


@admin_quality_bp.get("/missing-primary-images")
def missing_primary_images():
    _guard()
    limit = int(request.args.get("limit", 100))
    with db.SessionLocal() as session:
        rows = session.execute(
            select(Print.id, Card.name, Set.code, Print.collector_number)
            .join(Card, Print.card_id == Card.id)
            .join(Set, Print.set_id == Set.id)
            .where(~select(PrintImage.id).where(and_(PrintImage.print_id == Print.id, PrintImage.is_primary.is_(True))).exists())
            .limit(limit)
        ).all()
    return jsonify([
        {"print_id": pid, "card_name": name, "set_code": code, "collector_number": cn}
        for pid, name, code, cn in rows
    ])


@admin_quality_bp.get("/duplicate-suspects")
def duplicate_suspects():
    _guard()
    limit = int(request.args.get("limit", 100))
    with db.SessionLocal() as session:
        same_print_key = session.execute(
            select(
                Set.game_id,
                Print.set_id,
                Print.collector_number,
                Print.language,
                Print.is_foil,
                func.count(Print.id).label("count_prints"),
            )
            .join(Set, Print.set_id == Set.id)
            .group_by(Set.game_id, Print.set_id, Print.collector_number, Print.language, Print.is_foil)
            .having(func.count(Print.id) > 1)
            .limit(limit)
        ).all()

        multi_cards = session.execute(
            select(
                Print.set_id,
                Print.collector_number,
                Print.language,
                func.count(func.distinct(Print.card_id)).label("card_count"),
            )
            .group_by(Print.set_id, Print.collector_number, Print.language)
            .having(func.count(func.distinct(Print.card_id)) > 1)
            .limit(limit)
        ).all()

    return jsonify(
        {
            "same_key_multiple_prints": [dict(row._mapping) for row in same_print_key],
            "same_set_number_language_multiple_cards": [dict(row._mapping) for row in multi_cards],
        }
    )


@admin_quality_bp.get("/conflicts")
def conflicts():
    _guard()
    limit = int(request.args.get("limit", 100))
    with db.SessionLocal() as session:
        rows = session.execute(
            select(
                PrintFieldProvenance.print_id,
                PrintFieldProvenance.field_name,
                func.count(func.distinct(PrintFieldProvenance.value_text)).label("value_count")
            )
            .group_by(PrintFieldProvenance.print_id, PrintFieldProvenance.field_name)
            .having(func.count(func.distinct(PrintFieldProvenance.value_text)) > 1)
            .limit(limit)
        ).all()
    return jsonify([dict(row._mapping) for row in rows])
