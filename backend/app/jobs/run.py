from __future__ import annotations

import argparse

from app.ingest.run import run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a configured ingest job")
    parser.add_argument("--job", required=True, help="Job connector name (fixture_local, scryfall_mtg)")
    parser.add_argument("--path", help="Path to fixtures")
    parser.add_argument("--set", dest="set_code", help="Set code")
    parser.add_argument("--limit", type=int, help="Limit")
    parser.add_argument("--fixture", action="store_true", help="Use fixture mode")
    args = parser.parse_args()

    run_ingest(args.job, args.path, set_code=args.set_code, limit=args.limit, fixture=args.fixture)


if __name__ == "__main__":
    main()
