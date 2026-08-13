from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app import db
from app.email_delivery import email_delivery_configured, send_password_reset_email
from app.user_auth_service import (
    consume_password_reset_token,
    issue_password_reset_token,
    issue_session,
    normalize_email,
    password_hash,
    password_matches,
    public_user,
    resolve_session,
    revoke_all_user_sessions,
    revoke_session,
    utcnow,
    validate_new_password,
    validate_registration,
)
from app.user_models import User


user_auth_bp = Blueprint("user_auth", __name__)
_DUMMY_PASSWORD_HASH = password_hash("dontripit-dummy-auth-check")


def _bearer_token() -> str | None:
    value = str(request.headers.get("Authorization") or "").strip()
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        return token or None
    return None


def _auth_required(session):
    resolved = resolve_session(session, _bearer_token())
    if not resolved:
        return None
    user, user_session = resolved
    return user, user_session


@user_auth_bp.post("/api/v2/auth/register")
def register_user():
    body = request.get_json(silent=True) or {}
    if body.get("terms_accepted") is not True:
        return jsonify({"error": "terms_required", "message": "Debes aceptar los términos y la política de privacidad."}), 400

    try:
        name, email, password = validate_registration(
            name=body.get("name"),
            email=body.get("email"),
            password=body.get("password"),
        )
    except ValueError as exc:
        code = str(exc)
        messages = {
            "name_invalid": "Introduce un nombre válido.",
            "email_invalid": "Introduce un correo electrónico válido.",
            "password_invalid": "La contraseña debe tener entre 8 y 200 caracteres.",
        }
        return jsonify({"error": code, "message": messages.get(code, "Datos de registro no válidos.")}), 400

    now = utcnow()

    with db.SessionLocal() as session:
        existing = session.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            return jsonify({"error": "email_in_use", "message": "Ya existe una cuenta con ese correo."}), 409

        user = User(
            name=name,
            email=email,
            password_hash=password_hash(password),
            marketing_consent=False,
            marketing_consent_at=None,
            terms_accepted_at=now,
            is_active=True,
        )
        session.add(user)
        try:
            session.flush()
            raw_token, user_session = issue_session(
                session,
                user=user,
                remember=True,
                user_agent=request.headers.get("User-Agent"),
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            return jsonify({"error": "email_in_use", "message": "Ya existe una cuenta con ese correo."}), 409

        return jsonify(
            {
                "user": public_user(user),
                "session_token": raw_token,
                "expires_at": user_session.expires_at.isoformat(),
            }
        ), 201


@user_auth_bp.post("/api/v2/auth/login")
def login_user():
    body = request.get_json(silent=True) or {}
    email = normalize_email(body.get("email"))
    password = str(body.get("password") or "")
    remember = body.get("remember") is True
    if not email or not password:
        return jsonify({"error": "invalid_credentials", "message": "Correo o contraseña incorrectos."}), 401

    with db.SessionLocal() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user:
            check_password_hash(_DUMMY_PASSWORD_HASH, password)
            return jsonify({"error": "invalid_credentials", "message": "Correo o contraseña incorrectos."}), 401
        if not user.is_active or not password_matches(user, password):
            return jsonify({"error": "invalid_credentials", "message": "Correo o contraseña incorrectos."}), 401

        user.last_login_at = utcnow()
        raw_token, user_session = issue_session(
            session,
            user=user,
            remember=remember,
            user_agent=request.headers.get("User-Agent"),
        )
        session.commit()
        return jsonify(
            {
                "user": public_user(user),
                "session_token": raw_token,
                "expires_at": user_session.expires_at.isoformat(),
            }
        )


@user_auth_bp.post("/api/v2/auth/forgot-password")
def forgot_password():
    body = request.get_json(silent=True) or {}
    email = normalize_email(body.get("email"))
    generic = {
        "ok": True,
        "message": "Si existe una cuenta con ese correo, recibirás un enlace para cambiar tu contraseña.",
    }
    unavailable = {
        "error": "password_reset_delivery_unavailable",
        "message": "La recuperación por email está temporalmente en configuración. Inténtalo de nuevo más tarde.",
    }

    if not email_delivery_configured():
        return jsonify(unavailable), 503

    if not email or len(email) > 320 or "@" not in email:
        return jsonify(generic)

    with db.SessionLocal() as session:
        user = session.execute(select(User).where(User.email == email, User.is_active.is_(True))).scalar_one_or_none()
        if not user:
            check_password_hash(_DUMMY_PASSWORD_HASH, "dontripit-password-reset-check")
            return jsonify(generic)

        raw_token, _ = issue_password_reset_token(session, user=user)
        session.flush()
        if not send_password_reset_email(to_email=user.email, token=raw_token):
            session.rollback()
            return jsonify(generic)
        session.commit()
        return jsonify(generic)


@user_auth_bp.post("/api/v2/auth/reset-password")
def reset_password():
    body = request.get_json(silent=True) or {}
    raw_token = str(body.get("token") or "").strip()
    try:
        new_password = validate_new_password(body.get("password"))
    except ValueError:
        return jsonify({"error": "password_invalid", "message": "La nueva contraseña debe tener entre 8 y 200 caracteres."}), 400

    with db.SessionLocal() as session:
        resolved = consume_password_reset_token(session, raw_token)
        if not resolved:
            session.rollback()
            return jsonify({"error": "reset_token_invalid", "message": "Este enlace ha caducado o ya fue utilizado."}), 400
        user, reset_token = resolved
        if password_matches(user, new_password):
            session.rollback()
            return jsonify({"error": "password_reused", "message": "Elige una contraseña diferente a la actual."}), 400
        user.password_hash = password_hash(new_password)
        reset_token.used_at = utcnow()
        revoke_all_user_sessions(session, user.id)
        session.commit()
    return jsonify({"ok": True, "message": "Contraseña actualizada. Ya puedes iniciar sesión."})


@user_auth_bp.get("/api/v2/auth/me")
def current_user():
    with db.SessionLocal() as session:
        resolved = _auth_required(session)
        if not resolved:
            return jsonify({"error": "authentication_required"}), 401
        user, _ = resolved
        payload = public_user(user)
        session.commit()
    return jsonify({"user": payload})


@user_auth_bp.delete("/api/v2/auth/account")
def delete_account():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password") or "")
    confirmation = str(body.get("confirmation") or "").strip().upper()
    if confirmation != "ELIMINAR":
        return jsonify({"error": "confirmation_required", "message": "Escribe ELIMINAR para confirmar el borrado definitivo."}), 400
    if not password:
        return jsonify({"error": "password_required", "message": "Introduce tu contraseña para confirmar."}), 400

    with db.SessionLocal() as session:
        resolved = _auth_required(session)
        if not resolved:
            return jsonify({"error": "authentication_required"}), 401
        user, _ = resolved
        if not password_matches(user, password):
            return jsonify({"error": "invalid_credentials", "message": "La contraseña no es correcta."}), 401

        # All user-owned records (sessions, reset tokens, collection and wishlist)
        # reference users.id with ON DELETE CASCADE. The transaction ensures the
        # account is either removed completely or not changed at all.
        session.delete(user)
        session.commit()

    return jsonify({"ok": True, "message": "Tu cuenta y tus datos asociados se han eliminado."})


@user_auth_bp.post("/api/v2/auth/logout")
def logout_user():
    with db.SessionLocal() as session:
        revoke_session(session, _bearer_token())
        session.commit()
    return jsonify({"ok": True})
