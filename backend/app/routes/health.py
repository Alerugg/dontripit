import os

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


def _runtime_name() -> str:
    if os.getenv("K_SERVICE"):
        return "cloud_run"
    if os.getenv("VERCEL") == "1":
        return "vercel"
    return "local"


@health_bp.get("/api/health")
@health_bp.get("/api/v1/health")
def health():
    return jsonify(
        {
            "ok": True,
            "revision": (
                os.getenv("K_REVISION")
                or os.getenv("SOURCE_VERSION")
                or os.getenv("VERCEL_GIT_COMMIT_SHA")
                or "unknown"
            ),
            "runtime": _runtime_name(),
        }
    )
