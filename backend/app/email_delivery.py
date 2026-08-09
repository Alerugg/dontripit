from __future__ import annotations

import json
import os
from urllib import error, request


def _site_url() -> str:
    return str(os.getenv("PUBLIC_SITE_URL") or "https://dontripit.com").rstrip("/")


def send_password_reset_email(*, to_email: str, token: str) -> bool:
    """Send a reset link through Resend without ever logging or persisting the raw token.

    Returns False when email delivery is not configured. Callers should keep the
    public response generic to avoid account enumeration.
    """
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
    req = request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=8) as response:
            return 200 <= int(response.status) < 300
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        return False
