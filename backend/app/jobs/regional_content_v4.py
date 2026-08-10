from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.jobs import regional_content as base


TPCI_SCHEDULE_URL = "https://press.pokemon.com/en/Items/_SchedulePage/lvyoogoqvy"
BASE_SOURCES = tuple(source for source in base.SOURCES if source.key != "pokemon_eu")


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:160] or "pokemon-tcg"


def _fetch_tpci_eu_schedule(http: requests.Session) -> list[dict]:
    response = http.get(TPCI_SCHEDULE_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict] = []
    seen: set[str] = set()
    for tr in soup.find_all("tr"):
        cells = [base._clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        title = cells[0]
        if "pokémon tcg" not in title.casefold() and "pokemon tcg" not in title.casefold() and "trading card game" not in title.casefold():
            continue
        release = None
        for cell in reversed(cells[1:]):
            release = base._date_from_text(cell)
            if release:
                break
        identity = f"{title}|{release.isoformat() if release else 'tba'}"
        if identity in seen:
            continue
        seen.add(identity)
        anchor = tr.find("a", href=True)
        item_url = str(anchor.get("href")) if anchor else ""
        if item_url.startswith("/"):
            item_url = "https://press.pokemon.com" + item_url
        if not item_url.startswith("http"):
            item_url = TPCI_SCHEDULE_URL + "#" + quote(_slug(identity), safe="-")
        rows.append(
            {
                "title": title,
                "release_date": release,
                "item_url": item_url,
                "raw_cells": cells,
            }
        )
    if not rows:
        raise RuntimeError("TPCI official product schedule yielded zero Pokemon TCG rows")
    return rows


def _upsert_tpci_eu(session, rows: list[dict], now: datetime) -> int:
    game_id = session.execute(text("SELECT id FROM games WHERE slug='pokemon' LIMIT 1")).scalar_one()
    for row in rows:
        session.execute(
            text(
                """
                INSERT INTO regional_tcg_content
                  (game_id,region,locale,kind,source_key,source_name,source_url,item_url,title,
                   published_date,release_date,raw_json,first_seen_at,last_seen_at)
                VALUES
                  (:game_id,'eu','en-GB','release','pokemon_eu_tpci_press',
                   'The Pokemon Company International Official Press Site',:source_url,:item_url,:title,
                   NULL,:release_date,CAST(:raw_json AS jsonb),:now,:now)
                ON CONFLICT (source_key,region,item_url) DO UPDATE SET
                  title=EXCLUDED.title,
                  release_date=COALESCE(EXCLUDED.release_date,regional_tcg_content.release_date),
                  raw_json=EXCLUDED.raw_json,
                  last_seen_at=EXCLUDED.last_seen_at
                """
            ),
            {
                "game_id": int(game_id),
                "source_url": TPCI_SCHEDULE_URL,
                "item_url": row["item_url"],
                "title": row["title"][:1000],
                "release_date": row["release_date"],
                "raw_json": json.dumps(
                    {
                        "official": True,
                        "regional_basis": "tpci_manages_pokemon_outside_asia",
                        "feed_role": "europe_product_release_schedule",
                        "raw_cells": row["raw_cells"],
                        "fetched_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                ),
                "now": now,
            },
        )
    return len(rows)


def ingest_official_regional_content(session, *, strict: bool = True) -> dict:
    original = base.SOURCES
    base.SOURCES = BASE_SOURCES
    try:
        report = base.ingest_official_regional_content(session, strict=strict)
    finally:
        base.SOURCES = original

    http = requests.Session()
    http.headers.update({
        "User-Agent": base.USER_AGENT,
        "Accept-Language": "en-GB,en;q=0.9",
    })
    now = datetime.now(timezone.utc)
    try:
        eu_rows = _fetch_tpci_eu_schedule(http)
        count = _upsert_tpci_eu(session, eu_rows, now)
        report["upserts"] = int(report.get("upserts") or 0) + count
        report.setdefault("source_reports", []).append(
            {
                "source": "pokemon_eu_tpci_press",
                "game": "pokemon",
                "regions": ["eu"],
                "items": count,
                "dated": 0,
                "release_dated": sum(1 for row in eu_rows if row["release_date"] is not None),
                "ok": True,
                "regional_basis": "official_tpci_product_schedule_for_market_outside_asia",
            }
        )
    except Exception as exc:  # noqa: BLE001
        report.setdefault("failed_sources", []).append("pokemon_eu_tpci_press")
        report.setdefault("source_reports", []).append(
            {
                "source": "pokemon_eu_tpci_press",
                "game": "pokemon",
                "regions": ["eu"],
                "items": 0,
                "dated": 0,
                "release_dated": 0,
                "ok": False,
                "error": str(exc),
            }
        )
        if strict:
            raise RuntimeError(f"Official Pokemon EU TPCI schedule failed: {exc}") from exc
    session.flush()
    return report
