from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.ingest.connectors.onepiece_canonical import OnePieceCanonicalConnector
from app.models import Game, Set


EXPECTED_SET_COUNT = 59


def run() -> dict:
    started_at = datetime.now(timezone.utc)
    connector = OnePieceCanonicalConnector()
    payload = connector._load_official_cardlist_remote(limit=None)
    diagnostics = payload.get("diagnostics") or {}
    unmatched = diagnostics.get("canonical_set_names_unmatched") or []
    if unmatched:
        raise RuntimeError(f"Canonical One Piece set-name source is incomplete: {unmatched}")

    source_names = {
        str(row.get("code") or "").strip().lower(): str(row.get("name") or "").strip()
        for row in payload.get("sets") or []
        if str(row.get("code") or "").strip() and str(row.get("name") or "").strip()
    }
    if len(source_names) != EXPECTED_SET_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_SET_COUNT} canonical One Piece sets, source produced {len(source_names)}"
        )

    db.init_engine()
    changes = []
    with db.SessionLocal() as session:
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        sets = session.execute(select(Set).where(Set.game_id == game.id).order_by(Set.code.asc())).scalars().all()
        if len(sets) != EXPECTED_SET_COUNT:
            raise RuntimeError(f"Expected {EXPECTED_SET_COUNT} One Piece DB sets, found {len(sets)}")

        db_codes = {row.code for row in sets}
        missing_source = sorted(db_codes - set(source_names))
        extra_source = sorted(set(source_names) - db_codes)
        if missing_source or extra_source:
            raise RuntimeError(
                f"Set-code mismatch source/db: missing_source={missing_source} extra_source={extra_source}"
            )

        for set_row in sets:
            canonical_name = source_names[set_row.code]
            if set_row.name != canonical_name:
                changes.append(
                    {
                        "code": set_row.code,
                        "before": set_row.name,
                        "after": canonical_name,
                    }
                )
                set_row.name = canonical_name
        session.commit()

    return {
        "strategy": "onepiece_set_name_repair",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "set_count": len(source_names),
        "changed_count": len(changes),
        "changes": changes,
        "source_diagnostics": {
            "canonical_set_names_resolved": diagnostics.get("canonical_set_names_resolved"),
            "canonical_set_names_unmatched": unmatched,
        },
    }


def main() -> int:
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
