from __future__ import annotations

"""Production V2 regional sync.

The legacy Pokémon EU release schedule lived on press.pokemon.com and now
returns HTTP 403 from GitHub Actions. V2 keeps the strict/idempotent V1 writer
but replaces only that blocked source with the official Pokemon.es news surface.
Only physical Pokémon TCG/JCC articles are accepted; Pocket and TCG Live are
excluded. No dates or product identities are inferred outside the official
article text.
"""

from datetime import date, datetime, timezone
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.jobs import regional_content as regional
from scripts import sync_regional_content_daily as v1


POKEMON_ES_KEY = "pokemon_eu_pokemon_es"
POKEMON_ES_URL = "https://www.pokemon.com/es/noticias"
POKEMON_ES_NAME = "Pokémon España – JCC oficial"
POKEMON_ES_LOCALE = "es-ES"
POKEMON_ES_REGION = "eu"
POKEMON_ES_MAX_ITEMS = 18
LEGACY_TPCI_KEY = "pokemon_eu_tpci_press"

_TCG_TERMS = (
    "jcc pokémon",
    "jcc pokemon",
    "juego de cartas coleccionables",
)
_DIGITAL_EXCLUSIONS = (
    "jcc pokémon pocket",
    "jcc pokemon pocket",
    "tcg pocket",
    "jcc pokémon live",
    "jcc pokemon live",
    "tcg live",
)
_SPANISH_RELEASE_ANCHORS = (
    "fecha de lanzamiento",
    "lanzamiento:",
    "sale a la venta el",
    "salen a la venta el",
    "disponible el",
    "disponibles el",
    "llega el",
    "llegará el",
    "se lanza el",
    "a partir del",
)


def _is_pokemon_es_article(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "www.pokemon.com"
        and (
            parsed.path.startswith("/es/noticias/")
            or parsed.path.startswith("/es/noticias-pokemon/")
        )
    )


def _is_physical_tcg_text(value: str) -> bool:
    folded = regional._clean(value).casefold()
    return any(term in folded for term in _TCG_TERMS) and not any(
        term in folded for term in _DIGITAL_EXCLUSIONS
    )


def _spanish_release_date(value: str | None) -> date | None:
    text_value = regional._clean(value)
    folded = text_value.casefold()
    for anchor in _SPANISH_RELEASE_ANCHORS:
        pos = folded.find(anchor)
        if pos < 0:
            continue
        candidate = regional._date_from_text(text_value[pos : pos + 220])
        if candidate is not None:
            return candidate
    return None


def _listing_candidates(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(POKEMON_ES_URL, str(anchor.get("href") or "")).split("#", 1)[0]
        if absolute in seen or not _is_pokemon_es_article(absolute):
            continue
        title = regional._title(anchor)
        context = regional._context(anchor)
        if not title or not _is_physical_tcg_text(f"{title} {context}"):
            continue
        seen.add(absolute)
        candidates.append(
            {
                "item_url": absolute,
                "title": title[:1000],
                "context": context[:2500],
                "published_date": regional._date_from_text(context),
                "release_date": _spanish_release_date(context),
            }
        )
        if len(candidates) >= POKEMON_ES_MAX_ITEMS * 2:
            break
    return candidates


def fetch_pokemon_es(http: requests.Session) -> list[dict[str, Any]]:
    html = regional._fetch(http, POKEMON_ES_URL)
    candidates = _listing_candidates(html)
    if not candidates:
        raise RuntimeError("Official Pokemon.es news surface yielded zero physical TCG candidates")

    rows: list[dict[str, Any]] = []
    detail_budget = max(8, POKEMON_ES_MAX_ITEMS)
    for candidate in candidates:
        published = candidate["published_date"]
        release = candidate["release_date"]
        detail_context = ""
        if detail_budget > 0 and (published is None or release is None):
            detail_budget -= 1
            try:
                detail_html = regional._fetch(http, candidate["item_url"])
                detail_soup = BeautifulSoup(detail_html, "html.parser")
                for tag in detail_soup(["script", "style", "noscript"]):
                    tag.decompose()
                detail_context = regional._clean(detail_soup.get_text(" ", strip=True))[:20000]
                published = published or regional._date_from_text(detail_context[:5000])
                release = release or _spanish_release_date(detail_context)
            except requests.RequestException:
                # The listing itself is official evidence. A detail-page transport
                # failure may reduce date coverage but must never create a date.
                detail_context = ""

        combined = f"{candidate['title']} {candidate['context']} {detail_context}"
        if not _is_physical_tcg_text(combined):
            continue
        kind = "release" if release is not None else regional._kind(
            candidate["title"], combined, None
        )
        rows.append(
            {
                "item_url": candidate["item_url"],
                "title": candidate["title"],
                "published_date": published,
                "release_date": release,
                "kind": kind,
                "source_context": candidate["context"][:1200],
            }
        )
        if len(rows) >= POKEMON_ES_MAX_ITEMS:
            break

    if not rows:
        raise RuntimeError("Official Pokemon.es physical TCG filter yielded zero items")
    rows.sort(
        key=lambda row: (
            row["published_date"] is not None,
            row["published_date"] or date.min,
            row["item_url"],
        ),
        reverse=True,
    )
    return rows


def _pokemon_es_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": "pokemon",
        "region": POKEMON_ES_REGION,
        "locale": POKEMON_ES_LOCALE,
        "kind": item["kind"],
        "source_key": POKEMON_ES_KEY,
        "source_name": POKEMON_ES_NAME,
        "source_url": POKEMON_ES_URL,
        "item_url": item["item_url"],
        "title": item["title"][:1000],
        "published_date": item["published_date"],
        "release_date": item["release_date"],
        "raw_json": {
            "official": True,
            "regional_basis": "official_pokemon_spain_tcg_surface_for_eu_operational_region",
            "feed_role": "physical_tcg_news_and_releases",
            "source_context": item["source_context"],
            "regions": [POKEMON_ES_REGION],
        },
    }


def collect_official_content(*, strict: bool = True) -> dict[str, Any]:
    http = requests.Session()
    http.headers.update(
        {
            "User-Agent": regional.USER_AGENT,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.7,ja;q=0.5",
        }
    )
    fetched_at = datetime.now(timezone.utc)
    raw_records: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    failed_sources: list[str] = []

    for source in regional.SOURCES:
        try:
            items = regional.scrape_source(source, http=http)
            if not items:
                raise ValueError("source yielded zero candidate items")
            raw_records.extend(
                v1._record_for_source(source, item, region)
                for item in items
                for region in source.regions
            )
            source_reports.append(v1._source_report(source, items))
        except Exception as exc:
            failed_sources.append(source.key)
            source_reports.append(
                {
                    "source": source.key,
                    "game": source.game,
                    "regions": list(source.regions),
                    "locale": source.locale,
                    "items": 0,
                    "kinds": {},
                    "published_dates": {"count": 0, "min": None, "max": None},
                    "release_dates": {"count": 0, "min": None, "max": None},
                    "ok": False,
                    "error": str(exc),
                }
            )

    try:
        pokemon_es = fetch_pokemon_es(http)
        raw_records.extend(_pokemon_es_record(item) for item in pokemon_es)
        published = sorted(
            item["published_date"] for item in pokemon_es if item["published_date"] is not None
        )
        released = sorted(
            item["release_date"] for item in pokemon_es if item["release_date"] is not None
        )
        kinds: dict[str, int] = {}
        for item in pokemon_es:
            kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
        source_reports.append(
            {
                "source": POKEMON_ES_KEY,
                "game": "pokemon",
                "regions": [POKEMON_ES_REGION],
                "locale": POKEMON_ES_LOCALE,
                "items": len(pokemon_es),
                "kinds": dict(sorted(kinds.items())),
                "published_dates": {
                    "count": len(published),
                    "min": v1._iso(published[0]) if published else None,
                    "max": v1._iso(published[-1]) if published else None,
                },
                "release_dates": {
                    "count": len(released),
                    "min": v1._iso(released[0]) if released else None,
                    "max": v1._iso(released[-1]) if released else None,
                },
                "ok": True,
                "regional_basis": "official_pokemon_spain_tcg_surface_for_eu_operational_region",
            }
        )
    except Exception as exc:
        failed_sources.append(POKEMON_ES_KEY)
        source_reports.append(
            {
                "source": POKEMON_ES_KEY,
                "game": "pokemon",
                "regions": [POKEMON_ES_REGION],
                "locale": POKEMON_ES_LOCALE,
                "items": 0,
                "kinds": {},
                "published_dates": {"count": 0, "min": None, "max": None},
                "release_dates": {"count": 0, "min": None, "max": None},
                "ok": False,
                "error": str(exc),
            }
        )

    if strict and failed_sources:
        raise RuntimeError(f"Official regional sources failed: {failed_sources}; reports={source_reports}")

    identity_keys = [
        (record["source_key"], record["region"], record["item_url"])
        for record in raw_records
    ]
    if len(identity_keys) != len(set(identity_keys)):
        raise RuntimeError("Official regional collection produced duplicate source/region/item identities")

    records, dedupe_decisions = v1._dedupe_records(raw_records)
    canonical_keys = tuple(source.key for source in regional.SOURCES) + (POKEMON_ES_KEY,)
    seen_sources = {report["source"] for report in source_reports if report.get("ok")}
    if strict and seen_sources != set(canonical_keys):
        raise RuntimeError(
            f"Canonical source registry mismatch: missing={sorted(set(canonical_keys) - seen_sources)}"
        )

    return {
        "fetched_at": fetched_at.isoformat(),
        "canonical_sources": len(canonical_keys),
        "failed_sources": failed_sources,
        "raw_records": len(raw_records),
        "records": records,
        "deduplicated_records": len(raw_records) - len(records),
        "dedupe_decisions": dedupe_decisions,
        "source_reports": source_reports,
    }


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
    expected[POKEMON_ES_KEY] = {
        "game": "pokemon",
        "regions": {POKEMON_ES_REGION},
        "locale": POKEMON_ES_LOCALE,
        "source_url": POKEMON_ES_URL,
    }
    return expected


def _install_v2_contract() -> None:
    canonical_keys = tuple(source.key for source in regional.SOURCES) + (POKEMON_ES_KEY,)
    deprecated_keys = tuple(dict.fromkeys((*regional.DEPRECATED_SOURCE_KEYS, LEGACY_TPCI_KEY)))

    # V1 owns the durable writer/verification CLI. Patch only the source registry
    # and collector so all idempotency and fail-closed DB contracts stay shared.
    v1.CANONICAL_SOURCE_KEYS = canonical_keys
    v1.DEPRECATED_SOURCE_KEYS = deprecated_keys
    v1.collect_official_content = collect_official_content
    v1._expected_sources = _expected_sources


_install_v2_contract()


if __name__ == "__main__":
    raise SystemExit(v1.main())
