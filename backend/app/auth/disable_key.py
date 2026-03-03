from __future__ import annotations

import argparse

from app import db
from app.auth.service import disable_key_by_prefix


def main() -> None:
    parser = argparse.ArgumentParser(description="Disable API key by prefix")
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()

    db.init_engine()
    with db.SessionLocal() as session:
        disabled = disable_key_by_prefix(session, args.prefix)

    if not disabled:
        raise SystemExit("key_not_found")

    print(f"disabled:{args.prefix}")


if __name__ == "__main__":
    main()
