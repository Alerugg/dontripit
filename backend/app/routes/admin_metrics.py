from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import desc, func, select

from app import db
from app.auth.service import current_period_ym
from app.models import ApiRequestMetric

admin_metrics_bp = Blueprint("admin_metrics", __name__)


@admin_metrics_bp.get("/api/admin/metrics")
@admin_metrics_bp.get("/api/v1/admin/metrics")
def metrics_summary():
    period = current_period_ym()
    with db.SessionLocal() as session:
        totals = session.execute(
            select(ApiRequestMetric.endpoint, func.count(ApiRequestMetric.id).label("requests_total"))
            .group_by(ApiRequestMetric.endpoint)
            .order_by(ApiRequestMetric.endpoint.asc())
        ).all()
        latency = session.execute(
            select(ApiRequestMetric.endpoint, func.avg(ApiRequestMetric.latency_ms).label("avg_latency_ms"))
            .group_by(ApiRequestMetric.endpoint)
            .order_by(ApiRequestMetric.endpoint.asc())
        ).all()
        top_keys = session.execute(
            select(ApiRequestMetric.api_key_prefix, func.count(ApiRequestMetric.id).label("request_count"))
            .where(ApiRequestMetric.period_ym == period, ApiRequestMetric.api_key_prefix.is_not(None))
            .group_by(ApiRequestMetric.api_key_prefix)
            .order_by(desc("request_count"), ApiRequestMetric.api_key_prefix.asc())
            .limit(10)
        ).all()

    return jsonify(
        {
            "period_ym": period,
            "requests_total": [dict(row._mapping) for row in totals],
            "average_latency_ms": [
                {"endpoint": row.endpoint, "avg_latency_ms": float(row.avg_latency_ms or 0)} for row in latency
            ],
            "top_api_keys_month": [
                {"prefix": row.api_key_prefix, "request_count": row.request_count} for row in top_keys
            ],
        }
    )
