from __future__ import annotations

import re
from html import unescape

import requests

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


_TAG_RE = re.compile(r"<[^>]+>")
_H3_RE = re.compile(r"^\s*<h3\b[^>]*>.*?</h3>\s*", flags=re.IGNORECASE | re.DOTALL)


def _clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _div_inner(body: str, class_name: str) -> str:
    match = re.search(
        rf'<div\s+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</div>',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _div_value(body: str, class_name: str) -> str:
    inner = _div_inner(body, class_name)
    inner = _H3_RE.sub("", inner)
    return _clean_html(inner)


def _int_or_none(value: str | None) -> int | None:
    text = str(value or "").replace(",", "")
    if not text or text.strip() == "-":
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _attributes_from_modal_body(body: str) -> dict:
    info_inner = _div_inner(body, "infoCol")
    info_parts = [
        _clean_html(value)
        for value in re.findall(r"<span[^>]*>(.*?)</span>", info_inner, flags=re.IGNORECASE | re.DOTALL)
    ]
    category = info_parts[2].title() if len(info_parts) >= 3 and info_parts[2] else None

    cost_or_life = _int_or_none(_div_value(body, "cost"))
    colors_raw = _div_value(body, "color")
    colors = [part.strip() for part in colors_raw.split("/") if part.strip()]

    attribute_inner = _div_inner(body, "attribute")
    attribute_alt = re.search(r'<img[^>]+alt="([^"]+)"', attribute_inner, flags=re.IGNORECASE)
    attribute_text = _div_value(body, "attribute")
    attributes = [
        part.strip()
        for part in (attribute_alt.group(1) if attribute_alt else attribute_text).split("/")
        if part.strip()
    ]

    traits = [part.strip() for part in _div_value(body, "feature").split("/") if part.strip()]
    block_raw = _div_value(body, "block")
    block_match = re.search(r"(?:^|\s)([1-5X])(?:$|\s)", block_raw, flags=re.IGNORECASE)
    block = block_match.group(1).upper() if block_match else None

    result = {
        "card_type": category,
        "colors": colors,
        "attributes": attributes,
        "power": _int_or_none(_div_value(body, "power")),
        "counter": _int_or_none(_div_value(body, "counter")),
        "block": block,
        "traits": traits,
        "effect": _div_value(body, "text") or None,
        "trigger": _div_value(body, "trigger") or None,
    }
    if category and category.lower() == "leader":
        result["life"] = cost_or_life
        result["cost"] = None
    else:
        result["cost"] = cost_or_life
        result["life"] = None
    return result


def parse_onepiece_search_attributes(html: str) -> dict[str, dict]:
    """Map exact official print id (including _pN/_rN) to searchable attributes."""
    result: dict[str, dict] = {}
    for print_id, body in re.findall(
        r'<dl\s+class="modalCol"\s+id="([^"]+)"[^>]*>(.*?)</dl>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        normalized_id = str(print_id or "").strip().upper()
        if normalized_id:
            result[normalized_id] = _attributes_from_modal_body(body)
    return result


def load_onepiece_search_attributes(*, timeout: int = 30) -> dict[str, dict]:
    """Fetch all official English series pages and return exact-print attributes.

    This is a derived-search enrichment pass. Canonical identity remains owned by
    the OnePieceV2 connector and CatalogRelease/PrintRelease data.
    """
    connector = OnePieceV2Connector()
    base_url = connector._env("ONEPIECE_OFFICIAL_CARDLIST_URL", connector._DEFAULT_OFFICIAL_CARDLIST_URL)
    headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}

    index_response = requests.get(base_url, timeout=timeout, headers=headers)
    index_response.raise_for_status()
    series_options = connector._parse_official_series_options(index_response.text)
    if not series_options:
        raise RuntimeError("One Piece Search V2 enrichment found zero official series")

    attributes: dict[str, dict] = {}
    for series_id, _label in series_options:
        response = requests.get(f"{base_url}?series={series_id}", timeout=timeout, headers=headers)
        response.raise_for_status()
        attributes.update(parse_onepiece_search_attributes(response.text))

    if not attributes:
        raise RuntimeError("One Piece Search V2 enrichment parsed zero card attributes")
    return attributes
