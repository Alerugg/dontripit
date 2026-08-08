import argparse
import logging
import os

from app import db
from app.ingest.registry import get_connector


def _to_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def run_ingest(
    connector_name: str,
    path: str | None = None,
    *,
    set_code: str | None = None,
    lang: str = "en",
    limit: int | None = None,
    incremental: bool = True,
    fixture: bool = False,
):
    """Run one registered write connector through the central safety policy.

    `get_connector` deliberately rejects quarantined writers such as the legacy
    Scryfall MTG upsert. Source-only MTG snapshot/audit code imports the
    Scryfall connector class directly and never passes through this function.
    """

    if db.SessionLocal is None:
        db.init_engine()

    connector = get_connector(connector_name)
    ingest_path = path or "backend/data/fixtures"
    with db.SessionLocal() as session:
        try:
            stats = connector.run(
                session,
                ingest_path,
                set=set_code,
                lang=lang,
                limit=limit,
                incremental=incremental,
                fixture=fixture,
            )
            session.commit()
            return stats
        except Exception:
            session.rollback()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ingest connector")
    parser.add_argument("connector", help="Connector name (fixture_local|scryfall_mtg|tcgdex_pokemon|ygoprodeck_yugioh|riftbound|onepiece)")
    parser.add_argument("--path", default="backend/data/fixtures", help="Fixture path")
    parser.add_argument("--set", default=None)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--incremental", type=_to_bool, default=True)
    parser.add_argument("--fixture", type=_to_bool, default=False)
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, os.getenv("INGEST_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stats = run_ingest(
        args.connector,
        args.path,
        set_code=args.set,
        lang=args.lang,
        limit=args.limit,
        incremental=args.incremental,
        fixture=args.fixture,
    )

    print(
        f"ingest complete connector={args.connector} files_seen={stats.files_seen} "
        f"files_skipped={stats.files_skipped} inserted={stats.records_inserted} "
        f"updated={stats.records_updated} errors={stats.errors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
