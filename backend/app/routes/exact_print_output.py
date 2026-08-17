import re

from flask import current_app, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db

_CARD_DETAIL = re.compile(r"^/api(?:/v1)?/cards/\d+$")
_CARD_PRINT_LIST = re.compile(r"^/api/v1/cards/\d+/prints$")
_PRINT_LIST = re.compile(r"^/api(?:/v1)?/prints$")
_PRINT_DETAIL = re.compile(r"^/api(?:/v1)?/prints/\d+$")
_PRINT_RESOLVE = re.compile(r"^/api(?:/v1)?/(?:catalog/)?prints/resolve$")


def _items(path, payload):
    if _CARD_DETAIL.fullmatch(path):
        return payload.get("prints", []) if isinstance(payload, dict) else []
    if _CARD_PRINT_LIST.fullmatch(path):
        return payload.get("items", []) if isinstance(payload, dict) else []
    if _PRINT_LIST.fullmatch(path):
        if isinstance(payload, list):
            return payload
        return payload.get("items", []) if isinstance(payload, dict) else []
    if _PRINT_DETAIL.fullmatch(path):
        return [payload] if isinstance(payload, dict) else []
    if _PRINT_RESOLVE.fullmatch(path) and isinstance(payload, dict):
        resolved = payload.get("prints", [])
        return [
            item.get("catalog")
            for item in resolved
            if isinstance(item, dict) and isinstance(item.get("catalog"), dict)
        ]
    return []


def _print_id(item):
    try:
        return int(item.get("print_id", item.get("id")))
    except (AttributeError, TypeError, ValueError):
        return None


def _is_guarded_path(path: str) -> bool:
    return bool(
        _CARD_DETAIL.fullmatch(path)
        or _CARD_PRINT_LIST.fullmatch(path)
        or _PRINT_LIST.fullmatch(path)
        or _PRINT_DETAIL.fullmatch(path)
        or _PRINT_RESOLVE.fullmatch(path)
    )


def _exact_images(ids: list[int]) -> dict[int, str | None]:
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
        return {int(row["id"]): row["exact_image_url"] for row in rows}
    except SQLAlchemyError:
        return {}


def _localized_identity(ids: list[int]) -> dict[int, dict]:
    """Return additive localized display data without weakening canonical identity.

    This lookup is intentionally isolated from exact-image enforcement. During a
    rolling deploy where code starts before migration 33, a missing localization
    table must not make exact image verification fail closed for otherwise valid
    catalog responses.
    """
    sql = text("""
        SELECT p.id,
               c.name AS canonical_card_name,
               s.name AS canonical_set_name,
               pl.card_name AS localized_card_name,
               pl.set_name AS localized_set_name
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        LEFT JOIN print_localizations pl
          ON pl.print_id = p.id
         AND pl.language = p.language
         AND pl.source = 'tcgdex'
        WHERE p.id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, {"ids": sorted(set(ids))}).mappings().all()
        return {int(row["id"]): dict(row) for row in rows}
    except SQLAlchemyError:
        # Backward-compatible rolling deploy: canonical response remains usable
        # even before migration 33 has created print_localizations.
        return {}


def enforce_exact_print_image_response(response):
    if not response.is_json or not 200 <= response.status_code < 300:
        return response
    path = request.path
    if not _is_guarded_path(path):
        return response
    payload = response.get_json(silent=True)
    items = _items(path, payload)
    ids = [pid for pid in (_print_id(item) for item in items) if pid is not None]
    if not ids:
        return response

    exact = _exact_images(ids)
    localized_identity = _localized_identity(ids)

    # Fail closed for image ownership only: if exact ownership cannot be
    # verified, expose no image rather than preserving a borrowed sibling image.
    for item in items:
        pid = _print_id(item)
        exact_url = exact.get(pid)
        if "image_url" in item:
            item["image_url"] = exact_url
        if "primary_image_url" in item:
            item["primary_image_url"] = exact_url

        identity = localized_identity.get(pid)
        if not identity:
            continue
        canonical_card_name = identity.get("canonical_card_name")
        canonical_set_name = identity.get("canonical_set_name")
        localized_card_name = identity.get("localized_card_name")
        localized_set_name = identity.get("localized_set_name")

        # Never replace existing canonical fields. These are additive display
        # helpers so old API clients keep exactly the same identity contract.
        if canonical_card_name is not None:
            item.setdefault("card_name", canonical_card_name)
        if canonical_set_name is not None:
            item.setdefault("set_name", canonical_set_name)
        item["localized_card_name"] = localized_card_name
        item["localized_set_name"] = localized_set_name
        item["display_name"] = localized_card_name or canonical_card_name
        item["display_set_name"] = localized_set_name or canonical_set_name

    response.set_data(current_app.json.dumps(payload))
    response.content_type = "application/json"
    return response
