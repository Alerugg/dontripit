from __future__ import annotations

import os

import requests


def _site_url() -> str:
    return str(os.getenv("PUBLIC_SITE_URL") or "https://dontripit.com").rstrip("/")


def email_delivery_configured() -> bool:
    """Return whether password-recovery email has the minimum required config.

    This is intentionally account-agnostic so callers can report a global
    configuration problem without revealing whether a submitted email exists.
    """
    return bool(str(os.getenv("RESEND_API_KEY") or "").strip() and str(os.getenv("AUTH_EMAIL_FROM") or "").strip())


def send_password_reset_email(*, to_email: str, token: str) -> bool:
    """Send a reset link through Resend without logging or persisting the raw token."""
    api_key = str(os.getenv("RESEND_API_KEY") or "").strip()
    from_address = str(os.getenv("AUTH_EMAIL_FROM") or "").strip()
    if not api_key or not from_address:
        return False

    reset_url = f"{_site_url()}/reset-password?token={token}"
    payload = {
        "from": from_address,
        "to": [to_email],
        "subject": "Recupera tu acceso a Don’tRipIt",
        "html": (
            "<div style='font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#17151f'>"
            "<h2>Recupera tu acceso</h2>"
            "<p>Hemos recibido una solicitud para cambiar la contraseña de tu cuenta Don’tRipIt.</p>"
            f"<p><a href='{reset_url}' style='display:inline-block;padding:12px 18px;border-radius:12px;"
            "background:#6d5dfc;color:white;text-decoration:none;font-weight:700'>Cambiar contraseña</a></p>"
            "<p>El enlace caduca en 45 minutos y solo puede usarse una vez.</p>"
            "<p>Si no lo solicitaste, puedes ignorar este correo.</p>"
            "</div>"
        ),
    }
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8,
        )
        return 200 <= int(response.status_code) < 300
    except requests.RequestException:
        return False
