from datetime import date

from scripts.sync_regional_content_daily_v2 import (
    LEGACY_TPCI_KEY,
    POKEMON_ES_KEY,
    POKEMON_ES_LOCALE,
    POKEMON_ES_REGION,
    POKEMON_ES_URL,
    _listing_candidates,
    _pokemon_es_record,
    _spanish_release_date,
)
from scripts import sync_regional_content_daily as v1


def test_spanish_release_date_is_explicit_only():
    assert _spanish_release_date("Fecha de lanzamiento 28 ago 2026") == date(2026, 8, 28)
    assert _spanish_release_date("Disponible el 7 agosto 2026") == date(2026, 8, 7)
    assert _spanish_release_date("Artículo publicado el 3 agosto 2026") is None


def test_listing_accepts_physical_jcc_and_excludes_digital_products():
    html = """
    <html><body>
      <article><a href="/es/noticias/productos-jcc-agosto">Echa un vistazo a todos los productos de JCC Pokémon que salen a la venta en agosto de 2026</a><p>3 ago 2026</p></article>
      <article><a href="/es/noticias/jcc-pokemon-live-mundial">JCC Pokémon Live se une al Mundial</a><p>6 ago 2026</p></article>
      <article><a href="/es/noticias/jcc-pokemon-pocket-cielos">JCC Pokémon Pocket: Dominador de los Cielos</a><p>29 jul 2026</p></article>
      <article><a href="/es/noticias/pokemon-go">Pokémon GO añade un nuevo evento</a><p>4 ago 2026</p></article>
    </body></html>
    """
    rows = _listing_candidates(html)
    assert [row["item_url"] for row in rows] == [
        "https://www.pokemon.com/es/noticias/productos-jcc-agosto"
    ]


def test_pokemon_es_record_keeps_explicit_provenance():
    record = _pokemon_es_record(
        {
            "kind": "release",
            "item_url": "https://www.pokemon.com/es/noticias/productos-jcc-agosto",
            "title": "Productos JCC Pokémon agosto",
            "published_date": date(2026, 8, 3),
            "release_date": date(2026, 8, 28),
            "source_context": "JCC Pokémon",
        }
    )
    assert record["source_key"] == POKEMON_ES_KEY
    assert record["source_url"] == POKEMON_ES_URL
    assert record["region"] == POKEMON_ES_REGION
    assert record["locale"] == POKEMON_ES_LOCALE
    assert record["raw_json"]["official"] is True
    assert record["raw_json"]["regional_basis"] == "official_pokemon_spain_tcg_surface_for_eu_operational_region"
    assert "fetched_at" not in record["raw_json"]


def test_v2_registry_replaces_blocked_tpci_source_and_marks_it_deprecated():
    assert POKEMON_ES_KEY in v1.CANONICAL_SOURCE_KEYS
    assert LEGACY_TPCI_KEY not in v1.CANONICAL_SOURCE_KEYS
    assert LEGACY_TPCI_KEY in v1.DEPRECATED_SOURCE_KEYS
