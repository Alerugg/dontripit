from __future__ import annotations

import argparse

from app import db
from app.auth.service import rotate_key_by_prefix


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate API key by prefix")
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()

    db.init_engine()
    with db.SessionLocal() as session:
        generated = rotate_key_by_prefix(session, args.prefix)

    if not generated:
        raise SystemExit("key_not_found")

    print(generated.plain_key)


if __name__ == "__main__":
    main()
