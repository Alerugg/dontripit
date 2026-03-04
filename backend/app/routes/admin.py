from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app import db
from app.auth.service import generate_api_key
from app.models import ApiKey, ApiPlan

admin_bp = Blueprint("admin", __name__)


def _is_local_request() -> bool:
    host = (request.host or "").split(":")[0]
    return host in {"127.0.0.1", "localhost"}


@admin_bp.post("/api/admin/api-keys")
def create_api_key():
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if admin_token:
        provided_token = request.headers.get("X-Admin-Token", "").strip()
        if provided_token != admin_token:
            return jsonify({"error": "forbidden"}), 403
    elif not _is_local_request():
        return jsonify({"error": "forbidden"}), 403

    with db.SessionLocal() as session:
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == "free")).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()

        generated = generate_api_key()
        api_key = ApiKey(
            key_hash=generated.key_hash,
            prefix=generated.prefix,
            plan_id=plan.id,
            is_active=True,
            scopes=["read:catalog", "read:admin"],
            label="explorer-generated",
        )
        session.add(api_key)
        session.commit()
        session.refresh(api_key)

        created_at = api_key.created_at or datetime.now(timezone.utc)
        expires_at = created_at + timedelta(days=90)

    return (
        jsonify(
            {
                "api_key": generated.plain_key,
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        ),
        201,
    )
