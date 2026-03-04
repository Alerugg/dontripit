from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass

from flask import Flask, g, jsonify, request
from sqlalchemy import select

from app import db
from app.auth.service import current_period_ym, find_active_key, get_or_create_usage, touch_last_used
from app.models import ApiPlan, ApiRequestMetric

_RATE_WINDOWS: dict[str, list[float]] = defaultdict(list)


@dataclass
class RateState:
    limit: int
    remaining: int
    blocked: bool


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_api_key() -> str | None:
    direct = request.headers.get("X-API-Key")
    if direct:
        return direct.strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def _required_scope(path: str) -> str | None:
    if not path.startswith("/api/"):
        return None
    if path in {"/api/health", "/api/v1/health"}:
        return None
    if path in {"/api/admin/api-keys", "/api/admin/dev/api-keys"}:
        return None
    if path.startswith("/api/admin/") or path.startswith("/api/v1/admin/"):
        return "read:admin"
    return "read:catalog"


def _rate_limit(identity: str, limit: int) -> RateState:
    now = time.time()
    window_start = now - 60
    bucket = _RATE_WINDOWS[identity]
    bucket[:] = [item for item in bucket if item > window_start]
    if len(bucket) >= limit:
        return RateState(limit=limit, remaining=0, blocked=True)
    bucket.append(now)
    return RateState(limit=limit, remaining=max(limit - len(bucket), 0), blocked=False)


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _set_headers(response, plan: str | None, rate_limit: int | None, remaining: int | None, quota_limit, quota_used):
    if plan is not None:
        response.headers["X-Plan"] = str(plan)
    if rate_limit is not None:
        response.headers["X-RateLimit-Limit"] = str(rate_limit)
    if remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(max(remaining, 0))
    response.headers["X-Quota-Monthly"] = "unlimited" if quota_limit is None else str(quota_limit)
    response.headers["X-Quota-Used"] = "n/a" if quota_used is None else str(quota_used)


def register_api_product_middleware(flask_app: Flask) -> None:
    @flask_app.before_request
    def api_product_guard():
        g.api_meta = {}
        g.request_started = time.perf_counter()
        g.api_key_prefix = None

        path = request.path
        required_scope = _required_scope(path)
        if required_scope is None:
            return None

        public_enabled = _as_bool(os.getenv("PUBLIC_API_ENABLED"), default=False)
        provided_key = _extract_api_key()

        if provided_key is None and not public_enabled:
            return jsonify({"error": "missing_api_key"}), 401

        if provided_key:
            with db.SessionLocal() as session:
                api_key = find_active_key(session, provided_key)
                if not api_key:
                    return jsonify({"error": "invalid_api_key"}), 401

                scopes = set(api_key.scopes or ["read:catalog"])
                if required_scope not in scopes:
                    return jsonify({"error": "insufficient_scope"}), 403

                plan = session.execute(select(ApiPlan).where(ApiPlan.id == api_key.plan_id)).scalar_one_or_none()
                if not plan:
                    return jsonify({"error": "invalid_api_key"}), 401

                rate = _rate_limit(f"key:{api_key.id}", plan.burst_rpm)
                if rate.blocked and plan.burst_rpm > 0:
                    response = jsonify({"error": "rate_limited"})
                    response.status_code = 429
                    _set_headers(response, plan.name, rate.limit, rate.remaining, plan.monthly_quota_requests, None)
                    return response

                usage = get_or_create_usage(session, api_key.id, current_period_ym())
                if plan.monthly_quota_requests is not None and usage.request_count >= plan.monthly_quota_requests:
                    response = jsonify({"error": "quota_exceeded"})
                    response.status_code = 429
                    _set_headers(
                        response,
                        plan.name,
                        rate.limit,
                        rate.remaining,
                        plan.monthly_quota_requests,
                        usage.request_count,
                    )
                    return response

                usage.request_count += 1
                usage.last_request_at = datetime.now(timezone.utc)
                touch_last_used(api_key)
                session.commit()

                g.api_meta = {
                    "plan": plan.name,
                    "rate_limit": rate.limit,
                    "rate_remaining": rate.remaining,
                    "quota_limit": plan.monthly_quota_requests,
                    "quota_used": usage.request_count,
                }
                g.api_key_prefix = api_key.prefix
            return None

        rate = _rate_limit(f"ip:{_client_ip()}", int(os.getenv("PUBLIC_IP_RATE_LIMIT_RPM", "30")))
        if rate.blocked:
            response = jsonify({"error": "rate_limited"})
            response.status_code = 429
            _set_headers(response, "public", rate.limit, rate.remaining, None, None)
            return response

        g.api_meta = {
            "plan": "public",
            "rate_limit": rate.limit,
            "rate_remaining": rate.remaining,
            "quota_limit": None,
            "quota_used": None,
        }
        return None

    @flask_app.after_request
    def append_api_headers(response):
        path = request.path
        if path.startswith("/api/"):
            latency_ms = int((time.perf_counter() - getattr(g, "request_started", time.perf_counter())) * 1000)
            try:
                with db.SessionLocal() as session:
                    session.add(
                        ApiRequestMetric(
                            endpoint=path,
                            status_code=response.status_code,
                            latency_ms=max(latency_ms, 0),
                            period_ym=current_period_ym(),
                            api_key_prefix=getattr(g, "api_key_prefix", None),
                        )
                    )
                    session.commit()
            except Exception:
                pass

        meta = getattr(g, "api_meta", None)
        if not meta:
            return response
        _set_headers(
            response,
            meta.get("plan"),
            meta.get("rate_limit"),
            meta.get("rate_remaining"),
            meta.get("quota_limit"),
            meta.get("quota_used"),
        )
        return response
