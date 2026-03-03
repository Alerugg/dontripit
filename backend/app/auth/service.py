from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey, ApiPlan, ApiUsage


@dataclass
class GeneratedApiKey:
    plain_key: str
    prefix: str
    key_hash: str


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> GeneratedApiKey:
    raw = f"ak_{secrets.token_urlsafe(32)}"
    return GeneratedApiKey(plain_key=raw, prefix=raw[:8], key_hash=hash_api_key(raw))


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return ["read:catalog"]
    scopes = [item.strip() for item in raw_scopes.split(",") if item.strip()]
    return scopes or ["read:catalog"]


def find_active_key(session: Session, raw_key: str) -> ApiKey | None:
    key_hash = hash_api_key(raw_key)
    stmt = select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    return session.execute(stmt).scalar_one_or_none()


def find_key_by_prefix(session: Session, prefix: str) -> ApiKey | None:
    stmt = select(ApiKey).where(ApiKey.prefix == prefix).order_by(ApiKey.id.desc())
    return session.execute(stmt).scalars().first()


def disable_key_by_prefix(session: Session, prefix: str) -> bool:
    api_key = find_key_by_prefix(session, prefix)
    if not api_key:
        return False
    api_key.is_active = False
    session.commit()
    return True


def rotate_key_by_prefix(session: Session, prefix: str) -> GeneratedApiKey | None:
    current = find_key_by_prefix(session, prefix)
    if not current:
        return None

    generated = generate_api_key()
    current.is_active = False
    session.add(
        ApiKey(
            key_hash=generated.key_hash,
            prefix=generated.prefix,
            plan_id=current.plan_id,
            label=current.label,
            is_active=True,
            scopes=current.scopes or ["read:catalog"],
        )
    )
    session.commit()
    return generated


def get_or_create_usage(session: Session, api_key_id: int, period_ym: str) -> ApiUsage:
    usage = session.execute(
        select(ApiUsage).where(ApiUsage.api_key_id == api_key_id, ApiUsage.period_ym == period_ym)
    ).scalar_one_or_none()
    if usage:
        return usage

    usage = ApiUsage(api_key_id=api_key_id, period_ym=period_ym, request_count=0)
    session.add(usage)
    session.flush()
    return usage


def current_period_ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def touch_last_used(api_key: ApiKey) -> None:
    api_key.last_used_at = datetime.now(timezone.utc)


def ensure_default_plans(session: Session) -> int:
    defaults: list[dict] = [
        {"name": "free", "monthly_quota_requests": 5000, "burst_rpm": 60},
        {"name": "pro", "monthly_quota_requests": 100000, "burst_rpm": 600},
        {"name": "enterprise", "monthly_quota_requests": None, "burst_rpm": 3000},
    ]
    inserted = 0
    for item in defaults:
        exists = session.execute(select(ApiPlan).where(ApiPlan.name == item["name"])).scalar_one_or_none()
        if exists:
            continue
        session.add(ApiPlan(**item))
        inserted += 1

    if inserted:
        session.commit()
    return inserted
