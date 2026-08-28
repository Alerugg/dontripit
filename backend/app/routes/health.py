import os

from flask import Blueprint, jsonify

from app.search_v2.contract import (
    SEARCH_V2_BRANDS,
    SEARCH_V2_DEFAULT_LIMIT,
    SEARCH_V2_ENABLED,
    SEARCH_V2_ENGINE,
    SEARCH_V2_MAX_LIMIT,
    SEARCH_V2_SITE_URL,
)

health_bp = Blueprint("health", __name__)


@health_bp.get("/api/health")
@health_bp.get("/api/v1/health")
def health():
    return jsonify(
        {
            "ok": True,
            "revision": os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("SOURCE_VERSION") or "unknown",
            "runtime": "vercel" if os.getenv("VERCEL") == "1" else "local",
            "site_url": SEARCH_V2_SITE_URL,
            "search_v2_enabled": SEARCH_V2_ENABLED,
            "search_v2_engine": SEARCH_V2_ENGINE,
            "search_v2_brands": SEARCH_V2_BRANDS,
            "search_v2_default_limit": SEARCH_V2_DEFAULT_LIMIT,
            "search_v2_max_limit": SEARCH_V2_MAX_LIMIT,
        }
    )
