from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from app import db
from app.models import IngestRun, Print, PrintIdentifier, PrintImage, Source, SourceSyncState

admin_ingest_bp = Blueprint("admin_ingest", __name__)


@admin_ingest_bp.get("/api/v1/admin/ingest/runs")
def ingest_runs():
    source_name = request.args.get("source")
    with db.SessionLocal() as session:
        stmt = select(IngestRun, Source.name).join(Source, Source.id == IngestRun.source_id).order_by(IngestRun.started_at.desc())
        if source_name:
            stmt = stmt.where(Source.name == source_name)
        rows = session.execute(stmt.limit(100)).all()
    return jsonify(
        [
            {
                "id": run.id,
                "source": name,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "counts": run.counts_json,
                "error_summary": run.error_summary,
            }
            for run, name in rows
        ]
    )


@admin_ingest_bp.get("/api/v1/admin/ingest/state")
def ingest_state():
    source_name = request.args.get("source")
    with db.SessionLocal() as session:
        stmt = select(SourceSyncState, Source.name).join(Source, Source.id == SourceSyncState.source_id)
        if source_name:
            stmt = stmt.where(Source.name == source_name)
        rows = session.execute(stmt).all()
    return jsonify(
        [
            {
                "source": name,
                "last_run_at": state.last_run_at.isoformat() if state.last_run_at else None,
                "cursor": state.cursor_json,
            }
            for state, name in rows
        ]
    )


@admin_ingest_bp.get("/api/v1/admin/quality/summary")
def quality_summary():
    with db.SessionLocal() as session:
        prints_total = session.execute(select(func.count(Print.id))).scalar_one()
        missing_images = session.execute(
            select(func.count(Print.id)).where(~select(PrintImage.id).where(PrintImage.print_id == Print.id).exists())
        ).scalar_one()
        missing_identifiers = session.execute(
            select(func.count(Print.id)).where(~select(PrintIdentifier.id).where(PrintIdentifier.print_id == Print.id).exists())
        ).scalar_one()
    return jsonify({"prints_total": prints_total, "missing_images": missing_images, "missing_identifiers": missing_identifiers})
