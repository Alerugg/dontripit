from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select, text

from app import db
from app.auth.service import generate_api_key
from app.models import ApiKey, ApiPlan

admin_bp = Blueprint("admin", __name__)


def _is_localhost_request() -> bool:
    host = (request.host or "").strip().lower()
    target = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    target = target.strip("[]")
    return target in {"localhost", "127.0.0.1", "::1"}


def _resolve_admin_token() -> str:
    configured_token = os.getenv("ADMIN_TOKEN", "").strip()
    if configured_token:
        return configured_token

    flask_env = os.getenv("FLASK_ENV", "").strip().lower()
    if flask_env == "development":
        return "dev_admin_123"

    return ""


def _validate_admin_token():
    configured_token = os.getenv("ADMIN_TOKEN", "").strip()
    is_dev_route = request.path.endswith("/dev/api-keys")

    if not configured_token and _is_localhost_request() and not is_dev_route:
        return None

    expected_token = _resolve_admin_token()
    if not expected_token:
        return jsonify({"error": "admin_token_not_configured"}), 500

    provided_token = request.headers.get("X-Admin-Token", "").strip()
    if not provided_token:
        if is_dev_route:
            return jsonify({"error": "missing_admin_token"}), 401
        return jsonify({"error": "invalid_admin_token"}), 403
    if provided_token != expected_token:
        return jsonify({"error": "invalid_admin_token"}), 403

    return None




@admin_bp.get("/api/v1/admin/search-debug")
def search_debug():
    auth_error = _validate_admin_token()
    if auth_error is not None:
        return auth_error

    q = request.args.get("q", "").strip()
    game = request.args.get("game", "").strip().lower()
    sample_limit = min(max(request.args.get("sample_limit", default=5, type=int) or 5, 1), 25)

    with db.SessionLocal() as session:
        counts = session.execute(
            text(
                """
                SELECT g.slug AS game, sd.doc_type AS doc_type, COUNT(*) AS count
                FROM search_documents sd
                JOIN games g ON g.id = sd.game_id
                GROUP BY g.slug, sd.doc_type
                ORDER BY g.slug ASC, sd.doc_type ASC
                """
            )
        ).mappings().all()

        summary: dict[str, dict[str, int]] = {}
        for row in counts:
            bucket = summary.setdefault(row["game"], {"card": 0, "set": 0, "print": 0, "total": 0})
            bucket[row["doc_type"]] = int(row["count"])
            bucket["total"] += int(row["count"])

        params = {"limit": sample_limit, "game": game}
        sample_docs = session.execute(
            text(
                """
                SELECT g.slug AS game, sd.doc_type, sd.object_id, sd.title, sd.subtitle,
                       left(coalesce(sd.tsv, ''), 200) AS tsv_preview
                FROM search_documents sd
                JOIN games g ON g.id = sd.game_id
                WHERE (:game = '' OR g.slug = :game)
                ORDER BY sd.id DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

        match_rows = []
        if q:
            match_rows = session.execute(
                text(
                    """
                    SELECT g.slug AS game, sd.doc_type, sd.object_id, sd.title, sd.subtitle
                    FROM search_documents sd
                    JOIN games g ON g.id = sd.game_id
                    WHERE (:game = '' OR g.slug = :game)
                      AND (
                            to_tsvector('simple', coalesce(sd.title, '') || ' ' || coalesce(sd.subtitle, '') || ' ' || coalesce(sd.tsv, ''))
                            @@ plainto_tsquery('simple', :q)
                          )
                    ORDER BY sd.title ASC
                    LIMIT :limit
                    """
                ),
                {"q": q, "game": game, "limit": sample_limit},
            ).mappings().all()

    return jsonify(
        {
            "game_filter": game or None,
            "query": q or None,
            "search_documents": summary,
            "sample_documents": [dict(row) for row in sample_docs],
            "sample_matches": [dict(row) for row in match_rows],
        }
    )


@admin_bp.post("/api/v1/admin/reindex-search")
def admin_reindex_search():
    auth_error = _validate_admin_token()
    if auth_error is not None:
        return auth_error

    from app.scripts.reindex_search import rebuild_search_documents

    with db.SessionLocal() as session:
        stats = rebuild_search_documents(session)
        session.commit()

    return jsonify({"ok": True, "reindex": stats})

@admin_bp.post("/api/admin/api-keys")
@admin_bp.post("/api/admin/dev/api-keys")
def create_api_key():
    auth_error = _validate_admin_token()
    if auth_error is not None:
        return auth_error

    with db.SessionLocal() as session:
        plan = session.execute(
            select(ApiPlan).where(ApiPlan.name == "free")
        ).scalar_one_or_none()
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

    status_code = 200 if request.path.endswith("/dev/api-keys") else 201
    return (
        jsonify(
            {
                "api_key": generated.plain_key,
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        ),
        status_code,
    )
