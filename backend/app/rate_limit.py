from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import RateLimitBucket


logger = logging.getLogger(__name__)
_MEMORY_WINDOWS: dict[str, list[float]] = defaultdict(list)
_last_cleanup_at = 0.0
_database_warning_emitted = False


@dataclass(frozen=True)
class RateState:
    limit: int
    remaining: int
    blocked: bool
    retry_after: int
    backend: str


def _hash_secret() -> bytes:
    secret = (
        os.getenv("RATE_LIMIT_HASH_SECRET")
        or os.getenv("ADMIN_API_KEY")
        or os.getenv("INTERNAL_API_KEY")
        or "dontripit-development-rate-limit-secret"
    )
    return secret.encode("utf-8")


def _identity_hash(identity: str) -> str:
    return hmac.new(_hash_secret(), identity.encode("utf-8"), hashlib.sha256).hexdigest()


def _window(now: datetime) -> tuple[datetime, datetime, int]:
    start = now.replace(second=0, microsecond=0)
    end = start + timedelta(minutes=1)
    retry_after = max(1, int((end - now).total_seconds()) + 1)
    return start, end, retry_after


def _insert_statement(session, *, identity_hash: str, window_start: datetime, expires_at: datetime):
    dialect = session.get_bind().dialect.name
    values = {
        "identity_hash": identity_hash,
        "window_start": window_start,
        "request_count": 1,
        "expires_at": expires_at,
        "updated_at": datetime.now(timezone.utc),
    }
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        return None

    statement = insert(RateLimitBucket).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[RateLimitBucket.identity_hash, RateLimitBucket.window_start],
        set_={
            "request_count": RateLimitBucket.request_count + 1,
            "expires_at": expires_at,
            "updated_at": values["updated_at"],
        },
    ).returning(RateLimitBucket.request_count)


def _cleanup_expired(session, now: datetime) -> None:
    global _last_cleanup_at
    monotonic_now = time.monotonic()
    if monotonic_now - _last_cleanup_at < 300:
        return
    session.execute(delete(RateLimitBucket).where(RateLimitBucket.expires_at < now))
    _last_cleanup_at = monotonic_now


def _database_rate_limit(identity: str, limit: int, now: datetime) -> RateState:
    start, end, retry_after = _window(now)
    digest = _identity_hash(identity)
    with db.SessionLocal() as session:
        statement = _insert_statement(
            session,
            identity_hash=digest,
            window_start=start,
            expires_at=end + timedelta(minutes=5),
        )
        if statement is None:
            raise RuntimeError("unsupported rate limit database dialect")
        count = int(session.execute(statement).scalar_one())
        _cleanup_expired(session, now)
        session.commit()

    return RateState(
        limit=limit,
        remaining=max(limit - count, 0),
        blocked=count > limit,
        retry_after=retry_after,
        backend="database",
    )


def _memory_rate_limit(identity: str, limit: int, now: datetime) -> RateState:
    timestamp = now.timestamp()
    window_start = timestamp - 60
    bucket = _MEMORY_WINDOWS[identity]
    bucket[:] = [item for item in bucket if item > window_start]
    if len(bucket) >= limit:
        oldest = min(bucket) if bucket else timestamp
        retry_after = max(1, int(60 - (timestamp - oldest)) + 1)
        return RateState(limit=limit, remaining=0, blocked=True, retry_after=retry_after, backend="memory")
    bucket.append(timestamp)
    return RateState(
        limit=limit,
        remaining=max(limit - len(bucket), 0),
        blocked=False,
        retry_after=60,
        backend="memory",
    )


def consume_rate_limit(identity: str, limit: int, *, now: datetime | None = None) -> RateState:
    """Consume one shared fixed-window request.

    PostgreSQL/SQLite is the primary store so counters are shared across API
    workers and restarts. The in-memory fallback exists only for first boot,
    migrations and temporary database outages; it fails closed per worker.
    """

    global _database_warning_emitted
    bounded_limit = max(1, int(limit))
    current = now or datetime.now(timezone.utc)
    if db.SessionLocal is not None:
        try:
            return _database_rate_limit(identity, bounded_limit, current)
        except (SQLAlchemyError, RuntimeError):
            if not _database_warning_emitted:
                logger.warning("Shared rate limiter unavailable; using per-worker safety fallback", exc_info=True)
                _database_warning_emitted = True
    return _memory_rate_limit(identity, bounded_limit, current)


def clear_memory_rate_limits() -> None:
    _MEMORY_WINDOWS.clear()
