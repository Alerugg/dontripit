from __future__ import annotations

from dataclasses import dataclass
import time

import requests
from sqlalchemy import select

from app.models import Card, Game, Print, PrintImage


@dataclass(frozen=True)
class ImageRepairReport:
    game: str
    missing_before: int
    exact_source_ids: int
    inserted: int
    source_without_image: int
    request_failures: int
    missing_after: int

    def summary(self) -> dict:
        return self.__dict__.copy()


def _missing(session, game_slug: str) -> list[Print]:
    return session.execute(
        select(Print)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Card.game_id)
        .where(
            Game.slug == game_slug,
            ~select(PrintImage.id).where(PrintImage.print_id == Print.id).exists(),
        )
        .order_by(Print.id)
    ).scalars().all()


def _get_json(http: requests.Session, url: str, *, attempts: int = 3) -> dict | None:
    for attempt in range(attempts):
        try:
            response = http.get(url, timeout=25)
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                time.sleep(1.0 + attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.5 * (attempt + 1))
    return None


def _valid_image(http: requests.Session, url: str) -> bool:
    try:
        response = http.get(url, timeout=20, stream=True)
        if response.status_code != 200:
            return False
        content_type = str(response.headers.get("content-type") or "").casefold()
        return content_type.startswith("image/") or url.casefold().endswith((".jpg", ".jpeg", ".png", ".webp"))
    except requests.RequestException:
        return False
    finally:
        try:
            response.close()  # type: ignore[name-defined]
        except Exception:
            pass


def repair_pokemon_images(session) -> ImageRepairReport:
    missing = _missing(session, "pokemon")
    candidates = [row for row in missing if (row.tcgdex_id or "").strip()]
    http = requests.Session()
    http.headers.update({"User-Agent": "DontRipItCatalog/1.0", "Accept": "application/json"})
    inserted = no_image = failures = 0
    for row in candidates:
        source_id = str(row.tcgdex_id).strip()
        try:
            payload = _get_json(http, f"https://api.tcgdex.net/v2/en/cards/{source_id}")
        except requests.RequestException:
            failures += 1
            continue
        image_base = str((payload or {}).get("image") or "").strip()
        if not image_base:
            no_image += 1
            continue
        image_url = f"{image_base}/high.webp"
        if not _valid_image(http, image_url):
            no_image += 1
            continue
        if session.execute(select(PrintImage.id).where(PrintImage.print_id == row.id)).first() is None:
            session.add(PrintImage(print_id=row.id, url=image_url, is_primary=True, source="tcgdex"))
            inserted += 1
    session.flush()
    after = len(_missing(session, "pokemon"))
    return ImageRepairReport("pokemon", len(missing), len(candidates), inserted, no_image, failures, after)


def _scryfall_image(payload: dict) -> str | None:
    image_uris = payload.get("image_uris") if isinstance(payload, dict) else None
    if isinstance(image_uris, dict):
        for key in ("normal", "large", "png", "small"):
            value = str(image_uris.get(key) or "").strip()
            if value:
                return value
    faces = payload.get("card_faces") if isinstance(payload, dict) else None
    if isinstance(faces, list):
        for face in faces:
            image_uris = face.get("image_uris") if isinstance(face, dict) else None
            if not isinstance(image_uris, dict):
                continue
            for key in ("normal", "large", "png", "small"):
                value = str(image_uris.get(key) or "").strip()
                if value:
                    return value
    return None


def repair_mtg_images(session) -> ImageRepairReport:
    missing = _missing(session, "mtg")
    candidates = [row for row in missing if (row.scryfall_id or "").strip()]
    http = requests.Session()
    http.headers.update({"User-Agent": "DontRipItCatalog/1.0 (+https://dontripit.com)", "Accept": "application/json"})
    by_source: dict[str, list[Print]] = {}
    for row in candidates:
        by_source.setdefault(str(row.scryfall_id).strip(), []).append(row)
    inserted = no_image = failures = 0
    for index, (source_id, rows) in enumerate(by_source.items()):
        if index:
            time.sleep(0.11)
        try:
            payload = _get_json(http, f"https://api.scryfall.com/cards/{source_id}")
        except requests.RequestException:
            failures += len(rows)
            continue
        image_url = _scryfall_image(payload or {})
        if not image_url or not _valid_image(http, image_url):
            no_image += len(rows)
            continue
        for row in rows:
            if session.execute(select(PrintImage.id).where(PrintImage.print_id == row.id)).first() is None:
                session.add(PrintImage(print_id=row.id, url=image_url, is_primary=True, source="scryfall"))
                inserted += 1
    session.flush()
    after = len(_missing(session, "mtg"))
    return ImageRepairReport("mtg", len(missing), len(candidates), inserted, no_image, failures, after)


def repair_exact_source_images(session) -> dict:
    pokemon = repair_pokemon_images(session)
    mtg = repair_mtg_images(session)
    return {"pokemon": pokemon.summary(), "mtg": mtg.summary()}
