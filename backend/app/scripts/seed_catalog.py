from datetime import date

from sqlalchemy import select

from app import db
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set

SET_FIXTURE = {"code": "SV1", "name": "Scarlet & Violet", "release_date": date(2023, 3, 31)}
CARDS_FIXTURE = ["Pikachu", "Charizard"]
PRINTS_FIXTURE = [
    {
        "card_name": "Pikachu",
        "collector_number": "001",
        "language": "EN",
        "rarity": "common",
        "is_foil": False,
        "images": [
            {"url": "https://example.com/images/pokemon/sv1/001-en.png", "is_primary": True},
            {"url": "https://example.com/images/pokemon/sv1/001-en-alt.png", "is_primary": False},
        ],
        "identifiers": [{"source": "fixture_local", "external_id": "sv1-001-en-nf"}],
    },
    {
        "card_name": "Pikachu",
        "collector_number": "001",
        "language": "ES",
        "rarity": "common",
        "is_foil": True,
        "images": [{"url": "https://example.com/images/pokemon/sv1/001-es-foil.png", "is_primary": True}],
        "identifiers": [{"source": "fixture_local", "external_id": "sv1-001-es-f"}],
    },
    {
        "card_name": "Charizard",
        "collector_number": "004",
        "language": "EN",
        "rarity": "rare",
        "is_foil": True,
        "images": [{"url": "https://example.com/images/pokemon/sv1/004-en-foil.png", "is_primary": True}],
        "identifiers": [{"source": "fixture_local", "external_id": "sv1-004-en-f"}],
    },
]


def _get_or_create_set(session, game_id: int):
    set_row = session.execute(select(Set).where(Set.game_id == game_id, Set.code == SET_FIXTURE["code"])).scalar_one_or_none()
    if set_row:
        return set_row, False

    set_row = Set(game_id=game_id, **SET_FIXTURE)
    session.add(set_row)
    session.flush()
    return set_row, True


def _get_or_create_card(session, game_id: int, name: str):
    card_row = session.execute(select(Card).where(Card.game_id == game_id, Card.name == name)).scalar_one_or_none()
    if card_row:
        return card_row, False

    card_row = Card(game_id=game_id, name=name)
    session.add(card_row)
    session.flush()
    return card_row, True


def _ensure_images(session, print_row: Print, images: list[dict]) -> int:
    created = 0
    primary = session.execute(
        select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.is_primary.is_(True))
    ).scalar_one_or_none()

    for image in images:
        existing = session.execute(
            select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == image["url"])
        ).scalar_one_or_none()
        if existing:
            continue

        should_be_primary = bool(image.get("is_primary", False)) and primary is None
        if should_be_primary:
            primary = True

        session.add(
            PrintImage(
                print_id=print_row.id,
                url=image["url"],
                is_primary=should_be_primary,
                source="fixture_local",
            )
        )
        created += 1

    return created


def _ensure_identifiers(session, print_row: Print, identifiers: list[dict]) -> int:
    created = 0
    for item in identifiers:
        exists = session.execute(
            select(PrintIdentifier).where(
                PrintIdentifier.source == item["source"],
                PrintIdentifier.external_id == item["external_id"],
            )
        ).scalar_one_or_none()
        if exists:
            continue

        session.add(
            PrintIdentifier(
                print_id=print_row.id,
                source=item["source"],
                external_id=item["external_id"],
            )
        )
        created += 1

    return created


def run_seed_catalog() -> dict:
    db.init_engine()
    stats = {"sets": 0, "cards": 0, "prints": 0, "images": 0, "identifiers": 0}

    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "pokemon")).scalar_one_or_none()
        if game is None:
            raise RuntimeError("pokemon game not found. Run python -m app.scripts.seed first.")

        set_row, created = _get_or_create_set(session, game.id)
        stats["sets"] += int(created)

        cards_map = {}
        for card_name in CARDS_FIXTURE:
            card_row, card_created = _get_or_create_card(session, game.id, card_name)
            cards_map[card_name] = card_row
            stats["cards"] += int(card_created)

        for item in PRINTS_FIXTURE:
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.collector_number == item["collector_number"],
                    Print.language == item["language"],
                    Print.is_foil.is_(item["is_foil"]),
                )
            ).scalar_one_or_none()

            if print_row is None:
                print_row = Print(
                    card_id=cards_map[item["card_name"]].id,
                    set_id=set_row.id,
                    collector_number=item["collector_number"],
                    language=item["language"],
                    rarity=item["rarity"],
                    is_foil=item["is_foil"],
                )
                session.add(print_row)
                session.flush()
                stats["prints"] += 1

            stats["images"] += _ensure_images(session, print_row, item.get("images", []))
            stats["identifiers"] += _ensure_identifiers(session, print_row, item.get("identifiers", []))

        session.commit()

    return stats


if __name__ == "__main__":
    result = run_seed_catalog()
    print(f"seed_catalog complete; {result}")
