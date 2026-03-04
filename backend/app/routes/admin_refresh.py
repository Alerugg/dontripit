from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import db
from app.scripts.daily_refresh import build_refresh_args, run_daily_refresh
from app.scripts.ingest_status import get_ingest_status

admin_refresh_bp = Blueprint("admin_refresh", __name__)


def _as_int(value, default: int = 200) -> int:
    try:
        parsed = int(value)
        if parsed <= 0:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@admin_refresh_bp.post("/api/admin/refresh")
def admin_refresh():
    payload = request.get_json(silent=True) or {}
    args = build_refresh_args(
        pokemon_set=payload.get("pokemon_set"),
        pokemon_limit=_as_int(payload.get("pokemon_limit"), 200),
        mtg_limit=_as_int(payload.get("mtg_limit"), 200),
        yugioh_limit=_as_int(payload.get("yugioh_limit"), 200),
        riftbound_limit=_as_int(payload.get("riftbound_limit"), 200),
        incremental=_as_bool(payload.get("incremental"), True),
        sleep_seconds=0,
    )
    summary = run_daily_refresh(args)
    status_code = 200 if summary.get("exit_code", 1) == 0 else 500
    return jsonify(summary), status_code


@admin_refresh_bp.get("/api/admin/ingest-status")
def admin_ingest_status():
    runs_limit = request.args.get("limit", default=20, type=int)
    with db.SessionLocal() as session:
        payload = get_ingest_status(session, runs_limit=runs_limit)
    return jsonify(payload)
