import argparse

from app import db
from app.ingest.registry import get_connector


def _to_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ingest connector")
    parser.add_argument("connector", help="Connector name (fixture_local|scryfall_mtg|tcgdex_pokemon)")
    parser.add_argument("--path", default="backend/data/fixtures", help="Fixture path")
    parser.add_argument("--set", default=None)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--incremental", type=_to_bool, default=True)
    parser.add_argument("--fixture", type=_to_bool, default=False)
    args = parser.parse_args()

    db.init_engine()
    connector = get_connector(args.connector)

    with db.SessionLocal() as session:
        stats = connector.run(
            session,
            args.path,
            set=args.set,
            lang=args.lang,
            limit=args.limit,
            incremental=args.incremental,
            fixture=args.fixture,
        )
        session.commit()

    print(
        f"ingest complete connector={args.connector} files_seen={stats.files_seen} "
        f"files_skipped={stats.files_skipped} inserted={stats.records_inserted} "
        f"updated={stats.records_updated} errors={stats.errors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
