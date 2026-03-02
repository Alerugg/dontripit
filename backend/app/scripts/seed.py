from sqlalchemy import select

from app import db
from app.models import Game, Source

SEED_GAMES = [
    {"slug": "pokemon", "name": "Pokémon"},
    {"slug": "mtg", "name": "Magic: The Gathering"},
]


def run_seed() -> int:
    db.init_engine()
    inserted = 0

    with db.SessionLocal() as session:
        for item in SEED_GAMES:
            exists = session.execute(select(Game).where(Game.slug == item["slug"])).scalar_one_or_none()
            if exists:
                continue

            session.add(Game(slug=item["slug"], name=item["name"]))
            inserted += 1

        fixture_source = session.execute(select(Source).where(Source.name == "fixture_local")).scalar_one_or_none()
        if fixture_source is None:
            session.add(Source(name="fixture_local", description="Local JSON fixture connector"))

        session.commit()

    return inserted


if __name__ == "__main__":
    created = run_seed()
    print(f"seed complete; inserted={created}")
