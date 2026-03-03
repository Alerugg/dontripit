from __future__ import annotations

from collections import defaultdict
from sqlalchemy import delete, select

from app import db
from app.models import PriceDailyOHLC, PriceSnapshot


def run() -> int:
    db.init_engine()
    with db.SessionLocal() as session:
        snapshots = session.execute(
            select(
                PriceSnapshot.entity_type,
                PriceSnapshot.entity_id,
                PriceSnapshot.source_id,
                PriceSnapshot.currency,
                PriceSnapshot.as_of,
                PriceSnapshot.price_market,
                PriceSnapshot.quantity,
            ).where(PriceSnapshot.price_market.is_not(None))
        ).all()

        grouped: dict[tuple, list[tuple]] = defaultdict(list)
        for row in snapshots:
            day = row.as_of.date()
            key = (row.entity_type, row.entity_id, row.source_id, row.currency, day)
            grouped[key].append((row.as_of, float(row.price_market), row.quantity or 0))

        session.execute(delete(PriceDailyOHLC))
        for (entity_type, entity_id, source_id, currency, day), values in grouped.items():
            values_sorted = sorted(values, key=lambda x: x[0])
            prices = [item[1] for item in values_sorted]
            session.add(
                PriceDailyOHLC(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    source_id=source_id,
                    currency=currency,
                    day=day,
                    open=prices[0],
                    high=max(prices),
                    low=min(prices),
                    close=prices[-1],
                    volume=sum(item[2] for item in values_sorted),
                )
            )

        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
