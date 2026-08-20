from __future__ import annotations

"""V4 regional collector for Pokemon EU using the official UK news surface.

The official UK news page is used only as a URL index. Article identity,
title, physical-TCG classification and dates are taken from each official
detail page. Pokemon TCG Live and Pokemon TCG Pocket are excluded. No release
date is inferred unless it appears explicitly in official article text.
"""

from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.jobs import regional_content as regional
from scripts import sync_regional_content_daily as v1
from scripts import sync_regional_content_daily_v2 as v2


POKEMON_EU_KEY = "pokemon_eu_pokemon_uk"
POKEMON_EU_URL = "https://www.pokemon.com/uk/news"
POKEMON_EU_NAME = "Pokemon UK – TCG official"
POKEMON_EU_LOCALE = "en-GB"
POKEMON_EU_REGION = "eu"
POKEMON_EU_MAX_ITEMS = 18
DEPRECATED_EU_KEYS = ("pokemon_eu", "pokemon_eu_tpci_press", "pokemon_eu_pokemon_es")

_PHYSICAL_TERMS = (
    "pokémon tcg",
    "pokemon tcg",
    "pokémon trading card game",
    "pokemon trading card game",
    "trading card game",
)
_DIGITAL_EXCLUSIONS = (
    "pokémon tcg live",
    "pokemon tcg live",
    "trading card game live",
    "pokémon tcg pocket",
    "pokemon tcg pocket",
    "trading card game pocket",
)
_RELEASE_ANCHORS = (
    "release date",
    "available on",
    "available ",
    "arrives on",
    "arrives ",
    "releases on",
    "launches on",
)


def _is_uk_article(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "www.pokemon.com"
        and parsed.path.startswith("/uk/news/")
    )


def _is_physical_tcg_text(value: str) -> bool:
    folded = regional._clean(value).casefold()
    return any(term in folded for term in _PHYSICAL_TERMS) and not any(
        term in folded for term in _DIGITAL_EXCLUSIONS
    )


def _explicit_release_date(value: str | None) -> date | None:
    text_value = regional._clean(value)
    folded = text_value.casefold()
    for anchor in _RELEASE_ANCHORS:
        pos = folded.find(anchor)
        if pos < 0:
            continue
        candidate = regional._date_from_text(text_value[pos : pos + 220])
        if candidate is not None:
            return candidate
    return regional._release_date_from_text(text_value)


def _listing_candidates(html: str) -> list[dict[str, Any]]:
    """Discover official article URLs without relying on card DOM text."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(POKEMON_EU_URL, str(anchor.get("href") or "")).split("#", 1)[0]
        if absolute in seen or not _is_uk_article(absolute):
            continue
        seen.add(absolute)
        candidates.append(
            {
                "item_url": absolute,
                "listing_context": regional._context(anchor)[:2500],
            }
        )
        if len(candidates) >= POKEMON_EU_MAX_ITEMS * 3:
            break
    return candidates


def _detail_title(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading:
        value = regional._clean(heading.get_text(" ", strip=True))
        if value:
            return value[:1000]
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return regional._clean(og.get("content"))[:1000]
    return ""


def _detail_published_date(soup: BeautifulSoup, text_value: str) -> date | None:
    for time_tag in soup.find_all("time"):
        value = time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
        parsed = regional._date_from_text(str(value or ""))
        if parsed is not None:
            return parsed
    return regional._date_from_text(text_value[:5000])


def _uk_http_session() -> requests.Session:
    """Use source-local transport headers so V2's Spanish locale cannot alter UK DOM."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": regional.USER_AGENT,
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    return session


def fetch_pokemon_eu(http: requests.Session) -> list[dict[str, Any]]:
    # V2 owns the shared session for the other official feeds and currently
    # configures it with an es-ES locale. Pokemon.com varies the rendered DOM
    # by locale, so EU/UK must have a source-local session instead of inheriting
    # that transport state. Keep the parameter for the V2 collector contract.
    del http
    uk_http = _uk_http_session()
    html = regional._fetch(uk_http, POKEMON_EU_URL)
    candidates = _listing_candidates(html)
    if not candidates:
        raise RuntimeError("Official Pokemon UK news index yielded zero article URLs")

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            detail_html = regional._fetch(uk_http, candidate["item_url"])
        except requests.RequestException:
            continue
        detail_soup = BeautifulSoup(detail_html, "html.parser")
        title = _detail_title(detail_soup)
        if not title:
            continue
        published = _detail_published_date(
            detail_soup,
            regional._clean(detail_soup.get_text(" ", strip=True)),
        )
        for tag in detail_soup(["script", "style", "noscript"]):
            tag.decompose()
        detail = regional._clean(detail_soup.get_text(" ", strip=True))[:24000]
        combined = f"{title} {detail}"
        if not _is_physical_tcg_text(combined):
            continue

        release = _explicit_release_date(combined)
        kind = "release" if release is not None else regional._kind(title, combined, None)
        rows.append(
            {
                "item_url": candidate["item_url"],
                "title": title,
                "published_date": published,
                "release_date": release,
                "kind": kind,
                "source_context": candidate["listing_context"][:1200],
            }
        )
        if len(rows) >= POKEMON_EU_MAX_ITEMS:
            break

    if not rows:
        raise RuntimeError("Official Pokemon UK news index yielded zero verified physical TCG articles")
    rows.sort(
        key=lambda row: (
            row["published_date"] is not None,
            row["published_date"] or date.min,
            row["item_url"],
        ),
        reverse=True,
    )
    return rows


def _record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": "pokemon",
        "region": POKEMON_EU_REGION,
        "locale": POKEMON_EU_LOCALE,
        "kind": item["kind"],
        "source_key": POKEMON_EU_KEY,
        "source_name": POKEMON_EU_NAME,
        "source_url": POKEMON_EU_URL,
        "item_url": item["item_url"],
        "title": item["title"][:1000],
        "published_date": item["published_date"],
        "release_date": item["release_date"],
        "raw_json": {
            "official": True,
            "regional_basis": "official_pokemon_uk_news_for_eu_operational_region",
            "feed_role": "physical_tcg_news_and_releases",
            "source_context": item["source_context"],
            "regions": [POKEMON_EU_REGION],
        },
    }


def _patch_v2() -> None:
    v2.POKEMON_ES_KEY = POKEMON_EU_KEY
    v2.POKEMON_ES_URL = POKEMON_EU_URL
    v2.POKEMON_ES_NAME = POKEMON_EU_NAME
    v2.POKEMON_ES_LOCALE = POKEMON_EU_LOCALE
    v2.POKEMON_ES_REGION = POKEMON_EU_REGION
    v2.POKEMON_ES_MAX_ITEMS = POKEMON_EU_MAX_ITEMS
    v2.fetch_pokemon_es = fetch_pokemon_eu
    v2._pokemon_es_record = _record


_patch_v2()


def collect_official_content(*, strict: bool = True) -> dict[str, Any]:
    payload = v2.collect_official_content(strict=strict)
    for report in payload.get("source_reports", []):
        if report.get("source") == POKEMON_EU_KEY:
            report["regional_basis"] = "official_pokemon_uk_news_for_eu_operational_region"
    return payload


def _expected_sources() -> dict[str, dict[str, Any]]:
    expected = {
        source.key: {
            "game": source.game,
            "regions": set(source.regions),
            "locale": source.locale,
            "source_url": source.url,
        }
        for source in regional.SOURCES
    }
    expected[POKEMON_EU_KEY] = {
        "game": "pokemon",
        "regions": {POKEMON_EU_REGION},
        "locale": POKEMON_EU_LOCALE,
        "source_url": POKEMON_EU_URL,
    }
    return expected


CANONICAL_KEYS = tuple(source.key for source in regional.SOURCES) + (POKEMON_EU_KEY,)
v1.CANONICAL_SOURCE_KEYS = CANONICAL_KEYS
v1.DEPRECATED_SOURCE_KEYS = tuple(
    dict.fromkeys((*regional.DEPRECATED_SOURCE_KEYS, *DEPRECATED_EU_KEYS))
)
v1.collect_official_content = collect_official_content
v1._expected_sources = _expected_sources


if __name__ == "__main__":
    raise SystemExit(v1.main())
