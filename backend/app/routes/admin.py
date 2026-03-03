from __future__ import annotations

from flask import Blueprint, jsonify

from app import db
from app.scripts.ingest_status import get_ingest_status

admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/api/v1/admin/ingest-status")
def ingest_status():
    with db.SessionLocal() as session:
        payload = get_ingest_status(session)
    return jsonify(payload)
