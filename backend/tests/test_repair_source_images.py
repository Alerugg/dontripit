from app import db
from app.jobs.repair_source_images import _pokemon_exact_sources
from app.models import Card, Game, Print, PrintIdentifier, Set


def _seed_print(session, *, language: str, legacy_tcgdex_id: str | None = None):
    game = Game(slug="pokemon", name="Pokemon")
    session.add(game)
    session.flush()
    set_row = Set(game_id=game.id, code=f"t-{language}", name=f"Test {language}")
    session.add(set_row)
    session.flush()
    card = Card(game_id=game.id, name=f"Card {language}", card_key=f"pokemon:{language}:card")
    session.add(card)
    session.flush()
    print_row = Print(
        set_id=set_row.id,
        card_id=card.id,
        collector_number="1",
        language=language,
        is_foil=False,
        variant="default",
        print_key=f"pokemon:{language}:print",
        tcgdex_id=legacy_tcgdex_id,
    )
    session.add(print_row)
    session.flush()
    return print_row


def test_es_and_ja_use_only_language_scoped_identifier(client):
    with db.SessionLocal() as session:
        es = _seed_print(session, language="es", legacy_tcgdex_id="wrong-global-es")
        ja = _seed_print(session, language="ja", legacy_tcgdex_id="wrong-global-ja")
        session.add(PrintIdentifier(print_id=es.id, source="tcgdex:es", external_id="es-exact"))
        session.add(PrintIdentifier(print_id=ja.id, source="tcgdex:ja", external_id="ja-exact"))
        session.flush()

        resolved = _pokemon_exact_sources(session, [es, ja])
        assert [(row.id, lang, source_id) for row, lang, source_id in resolved] == [
            (es.id, "es", "es-exact"),
            (ja.id, "ja", "ja-exact"),
        ]


def test_non_en_never_falls_back_to_global_tcgdex_id(client):
    with db.SessionLocal() as session:
        ja = _seed_print(session, language="ja", legacy_tcgdex_id="colliding-en-id")
        session.flush()
        assert _pokemon_exact_sources(session, [ja]) == []


def test_en_can_use_legacy_exact_tcgdex_id(client):
    with db.SessionLocal() as session:
        en = _seed_print(session, language="en", legacy_tcgdex_id="en-exact")
        session.flush()
        resolved = _pokemon_exact_sources(session, [en])
        assert [(row.id, lang, source_id) for row, lang, source_id in resolved] == [
            (en.id, "en", "en-exact")
        ]
