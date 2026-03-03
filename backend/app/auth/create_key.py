from __future__ import annotations

import argparse

from sqlalchemy import select

from app import db
from app.auth.service import ensure_default_plans, generate_api_key
from app.models import ApiKey, ApiPlan


def main() -> None:
    parser = argparse.ArgumentParser(description="Create API key")
    parser.add_argument("--plan", required=True, choices=["free", "pro", "enterprise"])
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    db.init_engine()
    with db.SessionLocal() as session:
        ensure_default_plans(session)
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == args.plan)).scalar_one()
        generated = generate_api_key()
        session.add(
            ApiKey(
                key_hash=generated.key_hash,
                prefix=generated.prefix,
                plan_id=plan.id,
                label=args.label,
                is_active=True,
            )
        )
        session.commit()

    print(generated.plain_key)


if __name__ == "__main__":
    main()
