from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app import db
from app.auth.service import generate_api_key
from app.models import ApiKey, ApiPlan

admin_bp = Blueprint("admin", __name__)


def _resolve_admin_token() -> str:
    configured_token = os.getenv("ADMIN_TOKEN", "").strip()
    if configured_token:
        return configured_token

    flask_env = os.getenv("FLASK_ENV", "").strip().lower()
    if flask_env == "development":
        return "dev_admin_123"

    return ""


def _validate_admin_token():
    expected_token = _resolve_admin_token()
    if not expected_token:
        return jsonify({"error": "admin_token_not_configured"}), 500

    provided_token = request.headers.get("X-Admin-Token", "").strip()
    if not provided_token:
        return jsonify({"error": "missing_admin_token"}), 401

    if provided_token != expected_token:
        return jsonify({"error": "invalid_admin_token"}), 403

    return None


@admin_bp.post("/api/admin/api-keys")
@admin_bp.post("/api/admin/dev/api-keys")
def create_api_key():
    auth_error = _validate_admin_token()
    if auth_error is not None:
        return auth_error

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
        200,
    )
