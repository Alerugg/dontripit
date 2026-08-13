import re

from flask import current_app, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db

_CARD_DETAIL = re.compile(r"^/api(?:/v1)?/cards/\d+$")
_PRINT_LIST = re.compile(r"^/api(?:/v1)?/prints$")
_PRINT_DETAIL = re.compile(r"^/api(?:/v1)?/prints/\d+$")


def _items(path, payload):
    if _CARD_DETAIL.fullmatch(path):
        return payload.get("prints", []) if isinstance(payload, dict) else []
    if _PRINT_LIST.fullmatch(path):
        if isinstance(payload, list):
            return payload
        return payload.get("items", []) if isinstance(payload, dict) else []
    if _PRINT_DETAIL.fullmatch(path):
        return [payload] if isinstance(payload, dict) else []
    return []


def _print_id(item):
    try:
        return int(item.get("print_id", item.get("id")))
    except (AttributeError, TypeError, ValueError):
        return None


def enforce_exact_print_image_response(response):
    if not response.is_json or not 200 <= response.status_code < 300:
        return response
    path = request.path
    if not (_CARD_DETAIL.fullmatch(path) or _PRINT_LIST.fullmatch(path) or _PRINT_DETAIL.fullmatch(path)):
        return response
    payload = response.get_json(silent=True)
    items = _items(path, payload)
    ids = [pid for pid in (_print_id(item) for item in items) if pid is not None]
    if not ids:
        return response
    sql = text("""
        SELECT p.id,
               (SELECT pi.url FROM print_images pi
                WHERE pi.print_id = p.id AND trim(COALESCE(pi.url, '')) <> ''
                ORDER BY pi.is_primary DESC, pi.id ASC LIMIT 1) AS exact_image_url
        FROM prints p
        WHERE p.id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, {"ids": sorted(set(ids))}).mappings().all()
        exact = {int(row["id"]): row["exact_image_url"] for row in rows}
    except SQLAlchemyError:
        exact = {}

    # Fail closed: if exact ownership cannot be verified, expose no image rather
    # than preserving a potentially borrowed sibling-print image from legacy SQL.
    for item in items:
        pid = _print_id(item)
        exact_url = exact.get(pid)
        if "image_url" in item:
            item["image_url"] = exact_url
        if "primary_image_url" in item:
            item["primary_image_url"] = exact_url
    response.set_data(current_app.json.dumps(payload))
    response.content_type = "application/json"
    return response
