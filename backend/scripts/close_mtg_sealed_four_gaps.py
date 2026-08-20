#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import db
from app.jobs.mtg_sealed_gap_closure import (
    apply_mtg_sealed_gap_closure_plan,
    build_mtg_sealed_gap_closure_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Close the four certified 2026-08-20 MTG Cardmarket sealed canonical gaps. Dry-run by default."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    db.init_engine(database_url)

    with db.SessionLocal() as session:
        plan = build_mtg_sealed_gap_closure_plan(session)
        payload = {
            "mode": "apply" if args.apply else "dry_run",
            "preflight": plan.summary(),
            "decisions": [item.as_dict() for item in plan.decisions],
        }
        if not plan.summary()["write_ready"]:
            session.rollback()
            payload["status"] = "blocked"
            code = 2
        elif args.apply:
            payload["apply"] = apply_mtg_sealed_gap_closure_plan(session, plan)
            post = build_mtg_sealed_gap_closure_plan(session)
            payload["postflight"] = post.summary()
            payload["post_decisions"] = [item.as_dict() for item in post.decisions]
            if post.summary()["already_closed"] != 4 or not post.summary()["write_ready"]:
                session.rollback()
                raise RuntimeError(f"Postflight did not close all four targets: {post.summary()}")
            session.commit()
            payload["status"] = "applied"
            code = 0
        else:
            session.rollback()
            payload["status"] = "ready"
            code = 0

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
