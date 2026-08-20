from __future__ import annotations

"""V3 Pokemon EU collector using the server-rendered official JCC landing.

`/es/noticias` is accessible but its listing is not reliably rendered in the
HTML returned to GitHub Actions. `/es/jcc-pokemon` is the official physical TCG
landing and exposes links to its news/articles server-side. We use it only as an
index: every candidate article is fetched and must independently prove physical
JCC content. Pocket and Live remain excluded.
"""

from datetime import date
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.jobs import regional_content as regional
from scripts import sync_regional_content_daily_v2 as v2
from scripts import sync_regional_content_daily as v1


POKEMON_ES_JCC_URL = "https://www.pokemon.com/es/jcc-pokemon"


def _listing_candidates(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(POKEMON_ES_JCC_URL, str(anchor.get("href") or "")).split("#", 1)[0]
        if absolute in seen or not v2._is_pokemon_es_article(absolute):
            continue
        title = regional._title(anchor)
        context = regional._context(anchor)
        if not title:
            continue
        seen.add(absolute)
        candidates.append(
            {
                "item_url": absolute,
                "title": title[:1000],
                "context": context[:2500],
                "published_date": regional._date_from_text(context),
                "release_date": v2._spanish_release_date(context),
            }
        )
        if len(candidates) >= v2.POKEMON_ES_MAX_ITEMS * 3:
            break
    return candidates


def fetch_pokemon_es(http: requests.Session) -> list[dict[str, Any]]:
    html = regional._fetch(http, POKEMON_ES_JCC_URL)
    candidates = _listing_candidates(html)
    if not candidates:
        raise RuntimeError("Official Pokemon.es JCC landing yielded zero news candidates")

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            detail_html = regional._fetch(http, candidate["item_url"])
        except requests.RequestException as exc:
            # Candidate identity is known, but physical-vs-digital classification
            # is not. Fail closed for that row rather than trusting landing copy.
            continue

        detail_soup = BeautifulSoup(detail_html, "html.parser")
        for tag in detail_soup(["script", "style", "noscript"]):
            tag.decompose()
        detail_context = regional._clean(detail_soup.get_text(" ", strip=True))[:24000]
        combined = f"{candidate['title']} {candidate['context']} {detail_context}"
        if not v2._is_physical_tcg_text(combined):
            continue

        published = candidate["published_date"] or regional._date_from_text(detail_context[:6000])
        release = candidate["release_date"] or v2._spanish_release_date(detail_context)
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
        if len(rows) >= v2.POKEMON_ES_MAX_ITEMS:
            break

    if not rows:
        raise RuntimeError("Official Pokemon.es JCC landing yielded zero verified physical TCG articles")
    rows.sort(
        key=lambda row: (
            row["published_date"] is not None,
            row["published_date"] or date.min,
            row["item_url"],
        ),
        reverse=True,
    )
    return rows


# Patch V2's source surface while retaining all durable writer/provenance logic.
v2.POKEMON_ES_URL = POKEMON_ES_JCC_URL
v2._listing_candidates = _listing_candidates
v2.fetch_pokemon_es = fetch_pokemon_es

# V2 installed V1's collector/registry at import time. Its functions read the
# module globals above dynamically; refresh the expected-source hook explicitly.
v1.collect_official_content = v2.collect_official_content
v1._expected_sources = v2._expected_sources


if __name__ == "__main__":
    raise SystemExit(v1.main())
