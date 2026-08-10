from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text


@dataclass(frozen=True)
class OfficialSource:
    key: str
    game: str
    regions: tuple[str, ...]
    locale: str
    name: str
    url: str
    path_tokens: tuple[str, ...]
    max_items: int = 18


SOURCES = (
    OfficialSource(
        "pokemon_us", "pokemon", ("us",), "en-US", "Pokémon TCG US",
        "https://www.pokemon.com/us/pokemon-tcg", ("/us/pokemon-news/", "/us/news/"),
    ),
    OfficialSource(
        "pokemon_eu", "pokemon", ("eu",), "en-GB", "Pokémon TCG Europe",
        "https://www.pokemon.com/uk/pokemon-tcg", ("/uk/pokemon-news/", "/uk/news/"),
    ),
    OfficialSource(
        "pokemon_jp", "pokemon", ("jp",), "ja-JP", "Pokémon Card Japan",
        "https://www.pokemon-card.com/info/", ("/info/",),
    ),
    OfficialSource(
        "onepiece_global", "onepiece", ("us", "eu"), "en", "ONE PIECE CARD GAME Global",
        "https://en.onepiece-cardgame.com/topics/", ("/topics/",),
    ),
    OfficialSource(
        "onepiece_jp", "onepiece", ("jp",), "ja-JP", "ONE PIECE CARD GAME Japan",
        "https://www.onepiece-cardgame.com/topics/", ("/topics/",),
    ),
    OfficialSource(
        "yugioh_us", "yugioh", ("us",), "en-US", "Yu-Gi-Oh! TCG North America",
        "https://www.yugioh-card.com/en/news/", ("/en/",),
    ),
    OfficialSource(
        "yugioh_eu", "yugioh", ("eu",), "en-GB", "Yu-Gi-Oh! TCG Europe",
        "https://www.yugioh-card.com/eu/category/news/", ("/eu/",),
    ),
    OfficialSource(
        "yugioh_jp", "yugioh", ("jp",), "ja-JP", "Yu-Gi-Oh! OCG Japan",
        "https://www.konami.com/yugioh/news/", ("/yugioh/",),
    ),
    OfficialSource(
        "mtg_us", "mtg", ("us",), "en-US", "Magic: The Gathering / Wizards US",
        "https://magic.wizards.com/en/news", ("/en/news/",),
    ),
    OfficialSource(
        "mtg_eu", "mtg", ("eu",), "es-ES", "Magic: The Gathering / Wizards Europe",
        "https://magic.wizards.com/es/news", ("/es/news/",),
    ),
    OfficialSource(
        "mtg_jp", "mtg", ("jp",), "ja-JP", "Magic: The Gathering Japan",
        "https://mtg-jp.com/reading/", ("/reading/", "/products/"),
    ),
)

USER_AGENT = "DontRipItCatalog/1.0 (+https://github.com/Alerugg/dontripit)"
DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b"),
    re.compile(r"\b(20\d{2})年(\d{1,2})月(\d{1,2})日\b"),
)
EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
ES_MONTHS = {
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6,
    "jul": 7, "julio": 7, "ago": 8, "agosto": 8, "sep": 9, "sept": 9,
    "septiembre": 9, "oct": 10, "octubre": 10, "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}
MONTHS = {**EN_MONTHS, **ES_MONTHS}
MONTH_FIRST_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(x) for x in MONTHS), key=len, reverse=True)) +
    r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", re.I,
)
DAY_FIRST_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(sorted((re.escape(x) for x in MONTHS), key=len, reverse=True)) +
    r")[,]?\s+(20\d{2})\b", re.I,
)

RELEASE_TERMS = (
    "release date", "official release", "releases on", "arrives on", "available on",
    "on sale", "booster", "expansion", "starter deck", "structure deck", "display",
    "elite trainer box", "tin pack", "premium card collection", "product", "products",
    "発売日", "商品情報", "拡張パック", "構築デッキ", "ブースタ", "スターターデッキ",
)
SKIP_TITLES = {
    "home", "news", "latest news", "products", "product", "learn more", "read more",
    "more", "all", "see all", "topics", "image", "shop", "events", "play",
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _date_from_text(value: str | None) -> date | None:
    text_value = _clean(value)
    if not text_value:
        return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(text_value)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                pass
    match = MONTH_FIRST_RE.search(text_value)
    if match:
        try:
            return date(int(match.group(3)), MONTHS[match.group(1).casefold()], int(match.group(2)))
        except ValueError:
            pass
    match = DAY_FIRST_RE.search(text_value)
    if match:
        try:
            return date(int(match.group(3)), MONTHS[match.group(2).casefold()], int(match.group(1)))
        except ValueError:
            pass
    return None


def _release_date_from_text(value: str | None) -> date | None:
    text_value = _clean(value)
    folded = text_value.casefold()
    anchors = (
        "release date", "official release", "releases on", "arrives on", "available on",
        "on sale", "発売日", "公式発売日", "先行販売開始日",
    )
    positions = [folded.find(anchor.casefold()) for anchor in anchors]
    positions = [pos for pos in positions if pos >= 0]
    for pos in positions:
        candidate = _date_from_text(text_value[pos : pos + 180])
        if candidate:
            return candidate
    return None


def _kind(title: str, context: str, release_date: date | None) -> str:
    if release_date:
        return "release"
    folded = f"{title} {context}".casefold()
    if any(term.casefold() in folded for term in RELEASE_TERMS):
        return "product"
    return "news"


def _context(anchor) -> str:
    node = anchor
    for _ in range(4):
        node = getattr(node, "parent", None)
        if node is None:
            break
        value = _clean(node.get_text(" ", strip=True))
        if 20 <= len(value) <= 1600:
            return value
    return _clean(anchor.get_text(" ", strip=True))


def _title(anchor) -> str:
    value = _clean(anchor.get_text(" ", strip=True))
    if value and value.casefold() not in SKIP_TITLES and not value.casefold().startswith("image"):
        return value
    for name in ("h1", "h2", "h3", "h4", "strong"):
        heading = anchor.find(name)
        if heading:
            value = _clean(heading.get_text(" ", strip=True))
            if value:
                return value
    parent = anchor.parent
    if parent:
        for name in ("h1", "h2", "h3", "h4"):
            heading = parent.find(name)
            if heading:
                value = _clean(heading.get_text(" ", strip=True))
                if value:
                    return value
    return ""


def _is_candidate(source: OfficialSource, absolute_url: str, title: str) -> bool:
    if not title or len(title) < 8 or title.casefold() in SKIP_TITLES:
        return False
    target = urlparse(absolute_url)
    origin = urlparse(source.url)
    if target.netloc.casefold() != origin.netloc.casefold():
        return False
    if absolute_url.rstrip("/") == source.url.rstrip("/"):
        return False
    return any(token in target.path for token in source.path_tokens)


def _fetch(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.text


def _detail_metadata(http: requests.Session, url: str) -> tuple[date | None, date | None, str]:
    try:
        html = _fetch(http, url)
    except requests.RequestException:
        return None, None, ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_value = _clean(soup.get_text(" ", strip=True))[:20000]
    published = None
    for time_tag in soup.find_all("time"):
        published = _date_from_text(time_tag.get("datetime")) or _date_from_text(time_tag.get_text(" ", strip=True))
        if published:
            break
    published = published or _date_from_text(text_value[:4000])
    release = _release_date_from_text(text_value)
    return published, release, text_value[:4000]


def scrape_source(source: OfficialSource, http: requests.Session | None = None) -> list[dict]:
    http = http or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)
    html = _fetch(http, source.url)
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(source.url, anchor.get("href")).split("#", 1)[0]
        title = _title(anchor)
        if absolute in seen_urls or not _is_candidate(source, absolute, title):
            continue
        context = _context(anchor)
        published = _date_from_text(context)
        release = _release_date_from_text(context)
        candidates.append(
            {
                "item_url": absolute,
                "title": title[:1000],
                "context": context[:2500],
                "published_date": published,
                "release_date": release,
            }
        )
        seen_urls.add(absolute)
        if len(candidates) >= source.max_items * 2:
            break

    # Prefer dated candidates; unresolved dates get a bounded detail request.
    candidates.sort(key=lambda row: (row["published_date"] is not None, row["published_date"] or date.min), reverse=True)
    result = []
    detail_budget = max(6, source.max_items // 2)
    for candidate in candidates:
        published = candidate["published_date"]
        release = candidate["release_date"]
        detail_context = ""
        if (published is None or release is None) and detail_budget > 0:
            detail_published, detail_release, detail_context = _detail_metadata(http, candidate["item_url"])
            published = published or detail_published
            release = release or detail_release
            detail_budget -= 1
        context = candidate["context"] + " " + detail_context
        result.append(
            {
                "item_url": candidate["item_url"],
                "title": candidate["title"],
                "published_date": published,
                "release_date": release,
                "kind": _kind(candidate["title"], context, release),
                "source_context": candidate["context"][:1200],
            }
        )
        if len(result) >= source.max_items:
            break
    return result


def ingest_official_regional_content(session, *, strict: bool = True) -> dict:
    game_ids = {
        str(slug): int(game_id)
        for slug, game_id in session.execute(text("SELECT slug,id FROM games WHERE slug IN ('pokemon','onepiece','mtg','yugioh')")).all()
    }
    missing_games = sorted({source.game for source in SOURCES} - set(game_ids))
    if missing_games:
        raise ValueError(f"Missing canonical games for regional content: {missing_games}")

    http = requests.Session()
    http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8,ja;q=0.6,es;q=0.5"})
    now = datetime.now(timezone.utc)
    source_reports = []
    total_upserts = 0
    failed_sources = []

    for source in SOURCES:
        try:
            items = scrape_source(source, http=http)
            if not items:
                raise ValueError("source yielded zero candidate items")
            dated = sum(1 for item in items if item["published_date"] is not None)
            release_dated = sum(1 for item in items if item["release_date"] is not None)
            for item in items:
                for region in source.regions:
                    session.execute(
                        text(
                            """
                            INSERT INTO regional_tcg_content
                              (game_id,region,locale,kind,source_key,source_name,source_url,item_url,title,
                               published_date,release_date,raw_json,first_seen_at,last_seen_at)
                            VALUES
                              (:game_id,:region,:locale,:kind,:source_key,:source_name,:source_url,:item_url,:title,
                               :published_date,:release_date,CAST(:raw_json AS jsonb),:now,:now)
                            ON CONFLICT (source_key,region,item_url) DO UPDATE SET
                              kind=EXCLUDED.kind,
                              title=EXCLUDED.title,
                              published_date=COALESCE(EXCLUDED.published_date,regional_tcg_content.published_date),
                              release_date=COALESCE(EXCLUDED.release_date,regional_tcg_content.release_date),
                              raw_json=EXCLUDED.raw_json,
                              last_seen_at=EXCLUDED.last_seen_at
                            """
                        ),
                        {
                            "game_id": game_ids[source.game],
                            "region": region,
                            "locale": source.locale,
                            "kind": item["kind"],
                            "source_key": source.key,
                            "source_name": source.name,
                            "source_url": source.url,
                            "item_url": item["item_url"],
                            "title": item["title"],
                            "published_date": item["published_date"],
                            "release_date": item["release_date"],
                            "raw_json": __import__("json").dumps(
                                {
                                    "official": True,
                                    "source_context": item["source_context"],
                                    "regions": list(source.regions),
                                    "fetched_at": now.isoformat(),
                                },
                                ensure_ascii=False,
                            ),
                            "now": now,
                        },
                    )
                    total_upserts += 1
            source_reports.append(
                {
                    "source": source.key,
                    "game": source.game,
                    "regions": list(source.regions),
                    "items": len(items),
                    "dated": dated,
                    "release_dated": release_dated,
                    "ok": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed_sources.append(source.key)
            source_reports.append(
                {
                    "source": source.key,
                    "game": source.game,
                    "regions": list(source.regions),
                    "items": 0,
                    "dated": 0,
                    "release_dated": 0,
                    "ok": False,
                    "error": str(exc),
                }
            )

    if strict and failed_sources:
        raise RuntimeError(f"Official regional sources failed: {failed_sources}; reports={source_reports}")
    session.flush()
    return {
        "fetched_at": now.isoformat(),
        "sources": len(SOURCES),
        "failed_sources": failed_sources,
        "upserts": total_upserts,
        "source_reports": source_reports,
    }
