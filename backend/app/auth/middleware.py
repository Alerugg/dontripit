from __future__ import annotations

import os
import time

from flask import Flask, g, jsonify, request
from sqlalchemy import select

from app import db
from app.auth.service import consume_request_quota, current_period_ym, find_active_key, touch_last_used
from app.models import ApiPlan, ApiRequestMetric
from app.rate_limit import clear_memory_rate_limits, consume_rate_limit


class _LegacyRateWindows:
    """Compatibility shim for historical tests that clear the old limiter."""

    def clear(self) -> None:
        clear_memory_rate_limits()


_RATE_WINDOWS = _LegacyRateWindows()


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


def _extract_admin_header_key() -> str | None:
    direct = request.headers.get("X-API-Key")
    if not direct:
        return None
    token = direct.strip()
    return token or None


def _is_user_session_route(path: str) -> bool:
    """Routes whose credential is a Don’tRipIt user session, not a catalog API key.

    Registration, login and password recovery are public identity endpoints and
    are IP-rate-limited below. ``/auth/me``, logout and ``/me/*`` enforce the
    opaque user bearer token inside their route handlers. Keeping these paths
    out of the catalog API-key guard prevents a valid user session or password
    reset request from being mistaken for an API-product request.
    """
    if path in {
        "/api/v2/auth/register",
        "/api/v2/auth/login",
        "/api/v2/auth/forgot-password",
        "/api/v2/auth/reset-password",
        "/api/v2/auth/me",
        "/api/v2/auth/logout",
    }:
        return True
    return path.startswith("/api/v2/me/")


def _required_scope(path: str) -> str | None:
    if not path.startswith("/api/"):
        return None
    if _is_user_session_route(path):
        return None
    if path in {"/api/health", "/api/v1/health"}:
        return None
    if path in {"/api/admin/api-keys", "/api/admin/dev/api-keys"}:
        return None
    if path.startswith("/api/admin/") or path.startswith("/api/v1/admin/"):
        return "read:admin"
    return "read:catalog"


def _is_safe_public_catalog_request(required_scope: str | None) -> bool:
    return required_scope == "read:catalog" and request.method in {"GET", "HEAD", "OPTIONS"}


def _public_catalog_enabled(required_scope: str | None) -> bool:
    if not _is_safe_public_catalog_request(required_scope):
        return False

    explicit = os.getenv("PUBLIC_HUB_CATALOG_ENABLED")
    if explicit is not None:
        return _as_bool(explicit, default=False)

    # The Vercel-hosted HUB is a public catalog. Keep local/legacy deployments
    # on the existing PUBLIC_API_ENABLED switch unless explicitly opted in.
    if os.getenv("VERCEL"):
        return True

    return _as_bool(os.getenv("PUBLIC_API_ENABLED"), default=False)


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _set_headers(
    response,
    plan: str | None,
    rate_limit: int | None,
    remaining: int | None,
    quota_limit,
    quota_used,
    retry_after: int | None = None,
):
    if plan is not None:
        response.headers["X-Plan"] = str(plan)
    if rate_limit is not None:
        response.headers["X-RateLimit-Limit"] = str(rate_limit)
    if remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(max(remaining, 0))
        response.headers["RateLimit-Remaining"] = str(max(remaining, 0))
    if rate_limit is not None:
        response.headers["RateLimit-Limit"] = str(rate_limit)
    if retry_after is not None:
        response.headers["Retry-After"] = str(max(1, retry_after))
    response.headers["X-Quota-Monthly"] = "unlimited" if quota_limit is None else str(quota_limit)
    response.headers["X-Quota-Used"] = "n/a" if quota_used is None else str(quota_used)


def register_api_product_middleware(flask_app: Flask) -> None:
    @flask_app.before_request
    def api_product_guard():
        g.api_meta = {}
        g.request_started = time.perf_counter()
        g.api_key_prefix = None

        path = request.path

        # User identity and personal-library APIs have a different trust model
        # from the developer/catalog API. Public auth is IP-rate-limited; private
        # user routes validate the bearer session in their own handlers. Never
        # interpret a user bearer token as a catalog API key.
        if _is_user_session_route(path):
            auth_entry = path in {
                "/api/v2/auth/register",
                "/api/v2/auth/login",
                "/api/v2/auth/forgot-password",
                "/api/v2/auth/reset-password",
            }
            default_limit = 15 if auth_entry else 120
            env_name = "USER_AUTH_IP_RATE_LIMIT_RPM" if auth_entry else "USER_SESSION_IP_RATE_LIMIT_RPM"
            limit = max(1, int(os.getenv(env_name, str(default_limit))))
            route_bucket = path if auth_entry else "session"
            rate = consume_rate_limit(f"user:{_client_ip()}:{route_bucket}", limit)
            if rate.blocked:
                response = jsonify({"error": "rate_limited"})
                response.status_code = 429
                _set_headers(response, "user", rate.limit, rate.remaining, None, None, rate.retry_after)
                return response
            g.api_meta = {
                "plan": "user",
                "rate_limit": rate.limit,
                "rate_remaining": rate.remaining,
                "quota_limit": None,
                "quota_used": None,
                "retry_after": None,
            }
            return None

        required_scope = _required_scope(path)
        if required_scope is None:
            return None

        public_enabled = _public_catalog_enabled(required_scope)
        is_admin_route = path.startswith("/api/admin/") or path.startswith("/api/v1/admin/")

        env_admin_key = os.getenv("ADMIN_API_KEY", "").strip()
        if is_admin_route and env_admin_key:
            provided_admin_header = _extract_admin_header_key()
            if provided_admin_header == env_admin_key:
                g.api_meta = {
                    "plan": "admin-env",
                    "rate_limit": None,
                    "rate_remaining": None,
                    "quota_limit": None,
                    "quota_used": None,
                }
                g.api_key_prefix = "adminenv"
                return None

        provided_key = _extract_admin_header_key() if is_admin_route else _extract_api_key()

        if provided_key is None and not public_enabled:
            return jsonify({"error": "missing_api_key"}), 401

        if provided_key:
            with db.SessionLocal() as session:
                api_key = find_active_key(session, provided_key)
                if api_key:
                    scopes = set(api_key.scopes or ["read:catalog"])
                    if required_scope == "read:admin":
                        if "read:admin" not in scopes and "admin" not in scopes:
                            return jsonify({"error": "insufficient_scope"}), 403
                    elif required_scope not in scopes and "admin" not in scopes and "read:admin" not in scopes:
                        return jsonify({"error": "insufficient_scope"}), 403

                    plan = session.execute(select(ApiPlan).where(ApiPlan.id == api_key.plan_id)).scalar_one_or_none()
                    if not plan:
                        return jsonify({"error": "invalid_api_key"}), 401

                    rate = consume_rate_limit(f"key:{api_key.id}", plan.burst_rpm)
                    if rate.blocked and plan.burst_rpm > 0:
                        response = jsonify({"error": "rate_limited"})
                        response.status_code = 429
                        _set_headers(
                            response,
                            plan.name,
                            rate.limit,
                            rate.remaining,
                            plan.monthly_quota_requests,
                            None,
                            rate.retry_after,
                        )
                        return response

                    quota = consume_request_quota(
                        session,
                        api_key.id,
                        current_period_ym(),
                        plan.monthly_quota_requests,
                    )
                    if quota.blocked:
                        response = jsonify({"error": "quota_exceeded"})
                        response.status_code = 429
                        _set_headers(
                            response,
                            plan.name,
                            rate.limit,
                            rate.remaining,
                            plan.monthly_quota_requests,
                            quota.used,
                        )
                        session.rollback()
                        return response

                    touch_last_used(api_key)
                    session.commit()

                    g.api_meta = {
                        "plan": plan.name,
                        "rate_limit": rate.limit,
                        "rate_remaining": rate.remaining,
                        "quota_limit": plan.monthly_quota_requests,
                        "quota_used": quota.used,
                        "retry_after": None,
                    }
                    g.api_key_prefix = api_key.prefix
                    return None

                # The HUB used a historical shared key during the Railway era.
                # For safe public catalog reads only, an obsolete key must not
                # break the public site; fall through to the IP rate limiter.
                if not public_enabled:
                    return jsonify({"error": "invalid_api_key"}), 401

        rate = consume_rate_limit(f"ip:{_client_ip()}", int(os.getenv("PUBLIC_IP_RATE_LIMIT_RPM", "30")))
        if rate.blocked:
            response = jsonify({"error": "rate_limited"})
            response.status_code = 429
            _set_headers(response, "public", rate.limit, rate.remaining, None, None, rate.retry_after)
            return response

        g.api_meta = {
            "plan": "public",
            "rate_limit": rate.limit,
            "rate_remaining": rate.remaining,
            "quota_limit": None,
            "quota_used": None,
            "retry_after": None,
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
            meta.get("retry_after"),
        )
        return response
