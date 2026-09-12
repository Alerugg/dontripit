from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.models import Card, Game, Print, PrintImage, Set


def _payload_with_converging_ygo_variants() -> dict:
    """Two YGOPRODeck source rows normalize differently but resolve to one Print.

    The historical production bug only occurs with autoflush disabled: the first
    source row stages a primary image and the second SQL lookup cannot see that
    pending row yet. The connector must reuse the staged image in memory.
    """
    return {
        "id": 85482105,
        "name": "Speedroid Darumaknocker",
        "card_images": [
            {"image_url": "https://images.ygoprodeck.com/images/cards/85482105.jpg"}
        ],
        "card_sets": [
            {
                "set_name": "Legendary Arc-V Decks",
                "set_code": "LAVD-ENS15",
                "set_rarity": "Ultra Rare",
                "set_language": "en",
            },
            # Same physical identity, alternate rarity spelling. The connector's
            # existing canonical resolver may converge both rows onto one Print.
            {
                "set_name": "Legendary Arc-V Decks",
                "set_code": "LAVD-ENS15",
                "set_rarity": "Ultra-Rare",
                "set_language": "en",
            },
        ],
    }


def _primary_count(session, print_id: int) -> int:
    return len(
        session.execute(
            select(PrintImage.id).where(
                PrintImage.print_id == print_id,
                PrintImage.is_primary.is_(True),
            )
        ).all()
    )


def test_ygo_primary_image_is_transaction_idempotent_with_autoflush_disabled():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)

    connector = YgoProDeckYugiohConnector()
    payload = connector.validate_payload_contract(connector.normalize(_payload_with_converging_ygo_variants()))

    with Session() as session:
        stats = IngestStats()
        result = connector.upsert(session, payload, stats)
        print_ids = sorted(result["print_ids"])
        assert print_ids, "fixture must materialize at least one physical Print"

        # Flush only after the full upsert. This is the exact condition that used
        # to allow two pending primary rows for the same print.
        session.flush()
        rows = session.execute(
            select(PrintImage)
            .where(PrintImage.print_id.in_(print_ids), PrintImage.is_primary.is_(True))
            .order_by(PrintImage.print_id, PrintImage.id)
        ).scalars().all()
        counts = {}
        for row in rows:
            counts[row.print_id] = counts.get(row.print_id, 0) + 1
        assert all(count == 1 for count in counts.values())
        assert all(_primary_count(session, print_id) <= 1 for print_id in print_ids)

        # Repeating the same canonical upsert in the same transaction must remain
        # idempotent and must not create another primary image.
        connector.upsert(session, payload, stats)
        session.flush()
        assert all(_primary_count(session, print_id) <= 1 for print_id in print_ids)

        # Sanity-check the fixture exercises the intended YGO identity.
        card = session.execute(select(Card).where(Card.name == "Speedroid Darumaknocker")).scalar_one()
        game = session.execute(select(Game).where(Game.id == card.game_id)).scalar_one()
        assert game.slug == "yugioh"
        prints = session.execute(select(Print).where(Print.card_id == card.id)).scalars().all()
        assert prints
        set_ids = {row.set_id for row in prints}
        sets = session.execute(select(Set).where(Set.id.in_(set_ids))).scalars().all()
        assert any(row.code == "lavd-ens15" for row in sets)
