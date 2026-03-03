from sqlalchemy import select

from app import db
from app.auth.service import ensure_default_plans
from app.models import Game, Source

SEED_GAMES = [
    {"slug": "pokemon", "name": "Pokémon"},
    {"slug": "mtg", "name": "Magic: The Gathering"},
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

        fixture_source = session.execute(select(Source).where(Source.name == "fixture_local")).scalar_one_or_none()
        if not fixture_source:
            session.add(Source(name="fixture_local", description="Local JSON fixture connector"))
            inserted += 1

        session.commit()

    return inserted


if __name__ == "__main__":
    created = run_seed()
    print(f"seed complete; inserted={created}")
