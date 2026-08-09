from __future__ import annotations

from sqlalchemy import select

from app import db
from app.routes import user_auth
from app.user_models import User, UserPasswordResetToken, UserSession


def _register(client, *, email="eva@example.com", password="CorrectHorseBattery1!"):
    return client.post(
        "/api/v2/auth/register",
        json={
            "name": "Eva Test",
            "email": email,
            "password": password,
            "terms_accepted": True,
            "marketing_consent": False,
        },
    )


def test_register_logout_login_roundtrip_uses_same_password(client):
    password = "CorrectHorseBattery1!"
    registered = _register(client, password=password)
    assert registered.status_code == 201
    original_token = registered.get_json()["session_token"]
    assert original_token

    logged_out = client.post(
        "/api/v2/auth/logout",
        headers={"Authorization": f"Bearer {original_token}"},
    )
    assert logged_out.status_code == 200

    wrong = client.post(
        "/api/v2/auth/login",
        json={"email": "eva@example.com", "password": "definitely-wrong"},
    )
    assert wrong.status_code == 401

    logged_in = client.post(
        "/api/v2/auth/login",
        json={"email": " EVA@EXAMPLE.COM ", "password": password, "remember": True},
    )
    assert logged_in.status_code == 200
    assert logged_in.get_json()["user"]["email"] == "eva@example.com"


def test_password_reset_is_one_time_changes_password_and_revokes_sessions(client, monkeypatch):
    old_password = "OldPassword123!"
    new_password = "NewPassword456!"
    registered = _register(client, password=old_password)
    assert registered.status_code == 201
    first_session = registered.get_json()["session_token"]

    captured = {}
    monkeypatch.setattr(user_auth, "email_delivery_configured", lambda: True)

    def fake_send(*, to_email, token):
        captured["to_email"] = to_email
        captured["token"] = token
        return True

    monkeypatch.setattr(user_auth, "send_password_reset_email", fake_send)

    requested = client.post("/api/v2/auth/forgot-password", json={"email": "eva@example.com"})
    assert requested.status_code == 200
    assert requested.get_json()["ok"] is True
    assert captured["to_email"] == "eva@example.com"
    raw_reset_token = captured["token"]
    assert raw_reset_token

    with db.SessionLocal() as session:
        reset_row = session.execute(select(UserPasswordResetToken)).scalar_one()
        assert reset_row.token_hash != raw_reset_token
        assert len(reset_row.token_hash) == 64

    reset = client.post(
        "/api/v2/auth/reset-password",
        json={"token": raw_reset_token, "password": new_password},
    )
    assert reset.status_code == 200

    stale_me = client.get(
        "/api/v2/auth/me",
        headers={"Authorization": f"Bearer {first_session}"},
    )
    assert stale_me.status_code == 401

    reused = client.post(
        "/api/v2/auth/reset-password",
        json={"token": raw_reset_token, "password": "AnotherPassword789!"},
    )
    assert reused.status_code == 400
    assert reused.get_json()["error"] == "reset_token_invalid"

    old_login = client.post(
        "/api/v2/auth/login",
        json={"email": "eva@example.com", "password": old_password},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/v2/auth/login",
        json={"email": "eva@example.com", "password": new_password},
    )
    assert new_login.status_code == 200

    with db.SessionLocal() as session:
        user = session.execute(select(User).where(User.email == "eva@example.com")).scalar_one()
        sessions = session.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars().all()
        assert len(sessions) == 1


def test_forgot_password_does_not_reveal_account_when_delivery_is_configured(client, monkeypatch):
    assert _register(client).status_code == 201
    monkeypatch.setattr(user_auth, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(user_auth, "send_password_reset_email", lambda **_: False)

    existing = client.post("/api/v2/auth/forgot-password", json={"email": "eva@example.com"})
    missing = client.post("/api/v2/auth/forgot-password", json={"email": "missing@example.com"})

    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.get_json() == missing.get_json()

    with db.SessionLocal() as session:
        assert session.execute(select(UserPasswordResetToken)).scalars().all() == []


def test_forgot_password_reports_global_delivery_configuration_without_account_lookup(client, monkeypatch):
    assert _register(client).status_code == 201
    monkeypatch.setattr(user_auth, "email_delivery_configured", lambda: False)

    existing = client.post("/api/v2/auth/forgot-password", json={"email": "eva@example.com"})
    missing = client.post("/api/v2/auth/forgot-password", json={"email": "missing@example.com"})

    assert existing.status_code == 503
    assert missing.status_code == 503
    assert existing.get_json() == missing.get_json()
    assert existing.get_json()["error"] == "password_reset_delivery_unavailable"
