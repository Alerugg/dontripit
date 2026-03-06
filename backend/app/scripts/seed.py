from sqlalchemy import select

from app import db
from app.auth.service import ensure_default_plans
from app.models import Game, Source
from app.scripts.reindex_search import rebuild_search_documents

SEED_GAMES = [
    {"slug": "pokemon", "name": "Pokémon"},
    {"slug": "mtg", "name": "Magic: The Gathering"},
    {"slug": "yugioh", "name": "Yu-Gi-Oh!"},
    {"slug": "riftbound", "name": "Riftbound"},
]

SEED_SOURCES = [
    {"name": "fixture_local", "description": "Local JSON fixture connector"},
    {"name": "tcgdex_pokemon", "description": "TCGdex Pokémon connector"},
    {"name": "scryfall_mtg", "description": "Scryfall MTG connector"},
    {"name": "ygoprodeck_yugioh", "description": "YGOProDeck Yu-Gi-Oh! connector"},
    {"name": "riftbound", "description": "Riftbound connector"},
]


def run_seed() -> int:
    db.init_engine()
    inserted = 0

    with db.SessionLocal() as session:
        inserted += ensure_default_plans(session)

        for item in SEED_GAMES:
            exists = session.execute(select(Game).where(Game.slug == item["slug"])).scalar_one_or_none()
            if exists:
                continue

            session.add(Game(slug=item["slug"], name=item["name"]))
            inserted += 1

        for source in SEED_SOURCES:
            existing_source = session.execute(select(Source).where(Source.name == source["name"])).scalar_one_or_none()
            if existing_source is None:
                session.add(Source(name=source["name"], description=source["description"]))
                inserted += 1
                continue

            description = source["description"]
            if existing_source.description != description:
                existing_source.description = description

        rebuild_search_documents(session)
        session.commit()

    return inserted


if __name__ == "__main__":
    created = run_seed()
    print(f"seed complete; inserted={created}")
