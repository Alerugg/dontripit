import json
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
_DEFAULT_DISPLAY_LOCALE = "es-ES"
_DEFAULT_REGIONS = {
    "de": "DE",
    "en": "US",
    "es": "ES",
    "fr": "FR",
    "it": "IT",
    "ja": "JP",
    "ko": "KR",
    "pt": "PT",
    "zh": "CN",
}


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


def _normalize_locale(value: str | None) -> tuple[str, str]:
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        raw = _DEFAULT_DISPLAY_LOCALE
    raw = raw.split(",", 1)[0].split(";", 1)[0].strip()
    parts = [part for part in raw.split("-") if part]
    language = (parts[0] if parts else "es").lower()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        language = "es"
    region = None
    for part in parts[1:]:
        if re.fullmatch(r"[A-Za-z]{2}", part):
            region = part.upper()
            break
    if region is None:
        region = _DEFAULT_REGIONS.get(language)
    normalized = f"{language}-{region}" if region else language
    return normalized, language


def _requested_display_locale() -> tuple[str, str]:
    explicit = request.args.get("locale", "").strip()
    if explicit:
        return _normalize_locale(explicit)
    accept_language = request.headers.get("Accept-Language", "")
    if accept_language:
        return _normalize_locale(accept_language)
    return _normalize_locale(_DEFAULT_DISPLAY_LOCALE)


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _exact_images(ids: list[int]) -> dict[int, dict]:
    sql = text("""
        SELECT p.id,
               (SELECT pi.url FROM print_images pi
                WHERE pi.print_id = p.id AND trim(COALESCE(pi.url, '')) <> ''
                ORDER BY pi.is_primary DESC, pi.id ASC LIMIT 1) AS exact_image_url,
               (SELECT pi.source FROM print_images pi
                WHERE pi.print_id = p.id AND trim(COALESCE(pi.url, '')) <> ''
                ORDER BY pi.is_primary DESC, pi.id ASC LIMIT 1) AS exact_image_source
        FROM prints p
        WHERE p.id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, {"ids": sorted(set(ids))}).mappings().all()
        return {
            int(row["id"]): {
                "url": row["exact_image_url"],
                "source": row["exact_image_source"],
            }
            for row in rows
        }
    except SQLAlchemyError:
        return {}


def _localized_identity(ids: list[int]) -> dict[int, dict]:
    """Return additive localized physical-print identity for any certified source.

    ``print_localizations`` already guarantees one authoritative row per
    print/language. Source is provenance (TCGdex, YGOJSON, ...), not an identity
    filter, so restricting this lookup to TCGdex incorrectly hid Yu-Gi-Oh data.
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
         AND lower(pl.language) = lower(p.language)
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


def _detail_localization_rows(ids: list[int]) -> dict[int, list[dict]]:
    """Load candidate display localizations from the same canonical card only."""
    sql = text("""
        SELECT target.id AS target_print_id,
               lower(COALESCE(target.language, '')) AS physical_language,
               candidate.id AS source_print_id,
               lower(COALESCE(pl.language, '')) AS language,
               pl.source,
               pl.external_id,
               pl.card_name,
               pl.set_name,
               pl.details_json
        FROM prints target
        JOIN prints candidate ON candidate.card_id = target.card_id
        JOIN print_localizations pl ON pl.print_id = candidate.id
        WHERE target.id IN :ids
        ORDER BY target.id ASC,
                 CASE WHEN candidate.id = target.id THEN 0 ELSE 1 END ASC,
                 candidate.id ASC
    """).bindparams(bindparam("ids", expanding=True))
    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, {"ids": sorted(set(ids))}).mappings().all()
    except SQLAlchemyError:
        return {}

    grouped: dict[int, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item["details_json"] = _json_dict(item.get("details_json"))
        grouped.setdefault(int(item["target_print_id"]), []).append(item)
    return grouped


def _localization_payload(row: dict | None, target_print_id: int, *, scope: str | None = None) -> dict | None:
    if not row:
        return None
    details = _json_dict(row.get("details_json"))
    source_print_id = int(row["source_print_id"])
    return {
        "language": str(row.get("language") or "").lower() or None,
        "name": row.get("card_name"),
        "set_name": row.get("set_name"),
        "text": details.get("effect"),
        "effect": details.get("effect"),
        "pendulum_effect": details.get("pendulum_effect"),
        "official": details.get("official") if isinstance(details.get("official"), bool) else None,
        "source": row.get("source"),
        "external_id": row.get("external_id"),
        "source_print_id": source_print_id,
        "scope": scope or ("exact_print" if source_print_id == target_print_id else "card_display"),
    }


def _candidate_score(row: dict, target_print_id: int) -> tuple:
    details = _json_dict(row.get("details_json"))
    has_text = bool(details.get("effect") or details.get("pendulum_effect"))
    official = details.get("official") is True
    return (
        0 if int(row["source_print_id"]) == target_print_id else 1,
        0 if has_text else 1,
        0 if official else 1,
        int(row["source_print_id"]),
    )


def _build_detail_localizations(
    item: dict,
    rows: list[dict],
    *,
    display_locale: str,
    requested_language: str,
) -> tuple[dict, dict]:
    target_print_id = _print_id(item)
    physical_language = str(item.get("language") or item.get("print", {}).get("language") or "").lower()

    printed_row = next(
        (
            row
            for row in rows
            if int(row["source_print_id"]) == target_print_id
            and str(row.get("language") or "").lower() == physical_language
        ),
        None,
    )
    printed = _localization_payload(printed_row, target_print_id, scope="exact_print") or {
        "language": physical_language or None,
        "name": None,
        "set_name": None,
        "text": None,
        "effect": None,
        "pendulum_effect": None,
        "official": None,
        "source": None,
        "external_id": None,
        "source_print_id": target_print_id,
        "scope": "exact_print",
    }
    printed["available"] = printed_row is not None

    language_order = []
    for language in (requested_language, "en", physical_language):
        language = str(language or "").lower()
        if language and language not in language_order:
            language_order.append(language)

    selected = None
    resolved_language = None
    for language in language_order:
        candidates = [row for row in rows if str(row.get("language") or "").lower() == language]
        if candidates:
            selected = sorted(candidates, key=lambda row: _candidate_score(row, target_print_id))[0]
            resolved_language = language
            break

    if selected:
        display = _localization_payload(selected, target_print_id) or {}
        display.update(
            {
                "requested_locale": display_locale,
                "requested_language": requested_language,
                "resolved_language": resolved_language,
                "fallback": resolved_language != requested_language,
                "available": True,
            }
        )
    else:
        canonical_name = (item.get("card") or {}).get("name") or item.get("card_name") or item.get("title")
        canonical_set_name = (item.get("set") or {}).get("name") or item.get("set_name")
        display = {
            "requested_locale": display_locale,
            "requested_language": requested_language,
            "resolved_language": None,
            "name": canonical_name,
            "set_name": canonical_set_name,
            "text": None,
            "effect": None,
            "pendulum_effect": None,
            "official": None,
            "source": "canonical",
            "external_id": None,
            "source_print_id": None,
            "scope": "canonical_identity",
            "fallback": True,
            "available": bool(canonical_name or canonical_set_name),
        }

    return printed, display


def _apply_exact_print_detail_v2(item: dict, exact_image: dict, localization_rows: list[dict]) -> None:
    print_id = _print_id(item)
    display_locale, requested_language = _requested_display_locale()
    printed, display = _build_detail_localizations(
        item,
        localization_rows,
        display_locale=display_locale,
        requested_language=requested_language,
    )
    exact_url = exact_image.get("url")

    item["detail_contract_version"] = 2
    item["physical"] = {
        "print_id": print_id,
        "card_id": (item.get("card") or {}).get("id") or item.get("card_id"),
        "game": item.get("game"),
        "collector_number": item.get("collector_number"),
        "set_code": item.get("set_code"),
        "set_name": item.get("set_name"),
        "rarity": item.get("rarity"),
        "variant": item.get("variant"),
        "is_foil": item.get("is_foil"),
        "print_language": item.get("language"),
    }
    item["image"] = {
        "primary_image_url": exact_url,
        "has_exact_image": bool(exact_url),
        "source": exact_image.get("source"),
    }
    item["printed"] = printed
    item["display"] = display

    # These additive helpers now reflect browser/display locale on detail pages.
    # Canonical identity fields remain untouched for API compatibility.
    if display.get("name"):
        item["display_name"] = display["name"]
    if display.get("set_name"):
        item["display_set_name"] = display["set_name"]


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
    detail_localizations = _detail_localization_rows(ids) if _PRINT_DETAIL.fullmatch(path) else {}

    # Fail closed for image ownership only: if exact ownership cannot be
    # verified, expose no image rather than preserving a borrowed sibling image.
    for item in items:
        pid = _print_id(item)
        exact_image = exact.get(pid) or {"url": None, "source": None}
        exact_url = exact_image.get("url")
        if "image_url" in item:
            item["image_url"] = exact_url
        if "primary_image_url" in item:
            item["primary_image_url"] = exact_url

        identity = localized_identity.get(pid)
        if identity:
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

        if _PRINT_DETAIL.fullmatch(path):
            _apply_exact_print_detail_v2(item, exact_image, detail_localizations.get(pid, []))

    response.set_data(current_app.json.dumps(payload))
    response.content_type = "application/json"
    return response