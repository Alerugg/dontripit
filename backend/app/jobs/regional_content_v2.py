from __future__ import annotations

from app.jobs import regional_content as base


CORRECTED_SOURCES = tuple(
    base.OfficialSource(
        source.key,
        source.game,
        source.regions,
        source.locale,
        source.name,
        "https://www.pokemon.com/uk/pokemon-news",
        ("/uk/pokemon-news/", "/uk/news/"),
        source.max_items,
    )
    if source.key == "pokemon_eu"
    else source
    for source in base.SOURCES
)


def ingest_official_regional_content(session, *, strict: bool = True) -> dict:
    original = base.SOURCES
    base.SOURCES = CORRECTED_SOURCES
    try:
        return base.ingest_official_regional_content(session, strict=strict)
    finally:
        base.SOURCES = original
