import os

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.get("/api/health")
@health_bp.get("/api/v1/health")
def health():
    return jsonify(
        {
            "ok": True,
            "revision": os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("SOURCE_VERSION") or "unknown",
            "runtime": "vercel" if os.getenv("VERCEL") == "1" else "local",
        }
    )
