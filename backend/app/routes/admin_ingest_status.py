from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import db
from app.scripts.ingest_status import get_ingest_status

admin_ingest_status_bp = Blueprint("admin_ingest_status", __name__)


@admin_ingest_status_bp.get("/api/v1/admin/ingest-status")
def ingest_status():
    runs_limit = request.args.get("limit", default=20, type=int)
    with db.SessionLocal() as session:
        payload = get_ingest_status(session, runs_limit=runs_limit)
    return jsonify(payload)
