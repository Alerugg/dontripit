from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.user_models import User, UserPasswordResetToken, UserSession


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def validate_registration(*, name: str | None, email: str | None, password: str | None) -> tuple[str, str, str]:
    clean_name = " ".join(str(name or "").strip().split())
    clean_email = normalize_email(email)
    clean_password = str(password or "")

    if len(clean_name) < 2 or len(clean_name) > 120:
        raise ValueError("name_invalid")
    if len(clean_email) > 320 or not _EMAIL_RE.match(clean_email):
        raise ValueError("email_invalid")
    if len(clean_password) < 8 or len(clean_password) > 200:
        raise ValueError("password_invalid")
    return clean_name, clean_email, clean_password


def validate_new_password(password: str | None) -> str:
    clean_password = str(password or "")
    if len(clean_password) < 8 or len(clean_password) > 200:
        raise ValueError("password_invalid")
    return clean_password


def password_hash(password: str) -> str:
    return generate_password_hash(password, method="scrypt")


def password_matches(user: User, password: str) -> bool:
    try:
        return bool(user.password_hash) and check_password_hash(user.password_hash, str(password or ""))
    except (TypeError, ValueError):
        return False


def public_user(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "marketing_consent": bool(user.marketing_consent),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(session, *, user: User, remember: bool, user_agent: str | None = None) -> tuple[str, UserSession]:
    raw_token = secrets.token_urlsafe(48)
    now = utcnow()
    expires_at = now + (timedelta(days=30) if remember else timedelta(days=1))
    row = UserSession(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=expires_at,
        last_seen_at=now,
        user_agent=(str(user_agent or "")[:2000] or None),
    )
    session.add(row)
    return raw_token, row


def issue_password_reset_token(session, *, user: User) -> tuple[str, UserPasswordResetToken]:
    session.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()
    now = utcnow()
    session.execute(delete(UserPasswordResetToken).where(UserPasswordResetToken.user_id == user.id))
    raw_token = secrets.token_urlsafe(48)
    row = UserPasswordResetToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=now + timedelta(minutes=45),
    )
    session.add(row)
    return raw_token, row


def consume_password_reset_token(session, raw_token: str | None) -> tuple[User, UserPasswordResetToken] | None:
    token = str(raw_token or "").strip()
    if not token:
        return None
    now = utcnow()
    row = session.execute(
        select(UserPasswordResetToken, User)
        .join(User, User.id == UserPasswordResetToken.user_id)
        .where(
            UserPasswordResetToken.token_hash == _token_hash(token),
            UserPasswordResetToken.used_at.is_(None),
            User.is_active.is_(True),
        )
        .with_for_update()
    ).first()
    if not row:
        return None
    reset_token, user = row
    expires_at = reset_token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at <= now:
        session.delete(reset_token)
        session.commit()
        return None
    return user, reset_token


def revoke_all_user_sessions(session, user_id: int) -> None:
    session.execute(delete(UserSession).where(UserSession.user_id == user_id))


def resolve_session(session, raw_token: str | None) -> tuple[User, UserSession] | None:
    token = str(raw_token or "").strip()
    if not token:
        return None
    now = utcnow()
    row = session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == _token_hash(token), User.is_active.is_(True))
    ).first()
    if not row:
        return None
    user_session, user = row
    expires_at = user_session.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at <= now:
        session.delete(user_session)
        session.commit()
        return None
    user_session.last_seen_at = now
    return user, user_session


def revoke_session(session, raw_token: str | None) -> None:
    token = str(raw_token or "").strip()
    if not token:
        return
    session.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))


def purge_expired_sessions(session) -> int:
    result = session.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
    return int(result.rowcount or 0)
