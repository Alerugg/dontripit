from __future__ import annotations

from app.jobs import regional_content as base


CORRECTED_SOURCES = tuple(
    base.OfficialSource(
        source.key,
        source.game,
        source.regions,
        source.locale,
        "Pokémon Center UK TCG",
        "https://www.pokemoncenter.com/en-gb/search/tcg-cards",
        ("/en-gb/product/",),
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
        report = base.ingest_official_regional_content(session, strict=strict)
        for source_report in report.get("source_reports", []):
            if source_report.get("source") == "pokemon_eu":
                source_report["regional_basis"] = "official_pokemon_center_uk_tcg_catalog"
        return report
    finally:
        base.SOURCES = original
