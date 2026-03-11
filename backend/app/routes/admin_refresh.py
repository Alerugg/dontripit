from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from flask import Blueprint, jsonify, request

from app import db
from app.scripts.daily_refresh import build_refresh_args, run_daily_refresh
from app.scripts.ingest_status import get_ingest_status

admin_refresh_bp = Blueprint("admin_refresh", __name__)
_REFRESH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="admin-refresh")


def _as_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_limit(payload: dict, field: str, default: int | None = None) -> int | None:
    if field not in payload or payload.get(field) is None:
        return default

    parsed = _as_int(payload.get(field), default or 0)
    if parsed <= 0:
        return 0
    return parsed


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
        pokemon_limit=_parse_limit(payload, "pokemon_limit"),
        mtg_limit=_parse_limit(payload, "mtg_limit"),
        yugioh_limit=_parse_limit(payload, "yugioh_limit"),
        riftbound_limit=_parse_limit(payload, "riftbound_limit"),
        incremental=_as_bool(payload.get("incremental"), True),
        sleep_seconds=0,
    )
    job_id = str(uuid4())

    def _run_refresh_job() -> None:
        print(
            "[admin_refresh] refresh_args="
            + json.dumps(
                {
                    "job_id": job_id,
                    "pokemon_limit": args.pokemon_limit,
                    "mtg_limit": args.mtg_limit,
                    "yugioh_limit": args.yugioh_limit,
                    "riftbound_limit": args.riftbound_limit,
                    "incremental": args.incremental,
                    "fixture": args.fixture,
                    "path": args.path,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        try:
            summary = run_daily_refresh(args)
            print("[admin_refresh] summary=" + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[admin_refresh] failed job_id={job_id} error={exc}", flush=True)
            print(traceback.format_exc(), flush=True)

    _REFRESH_EXECUTOR.submit(_run_refresh_job)
    return jsonify({"queued": True, "job_id": job_id, "status_url": "/api/v1/admin/ingest-status"}), 202


@admin_refresh_bp.get("/api/admin/ingest-status")
def admin_ingest_status():
    runs_limit = request.args.get("limit", default=20, type=int)
    with db.SessionLocal() as session:
        payload = get_ingest_status(session, runs_limit=runs_limit)
    return jsonify(payload)
