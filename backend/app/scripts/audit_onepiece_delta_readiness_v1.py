from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from app import db
from app.ingest.connectors.onepiece_incremental_guard import SelfHealingOnePieceCanonicalConnector


MAX_DELTA_RATIO = 0.25


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only One Piece canonical delta readiness audit")
    parser.add_argument("--report", default="/tmp/onepiece-delta-readiness-v1.json")
    args = parser.parse_args()

    connector = SelfHealingOnePieceCanonicalConnector()
    loaded = connector.load(fixture=False, incremental=True)
    rows: list[dict] = []

    with db.SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        for source_path, payload, checksum in loaded:
            delta = connector._delta_payload(session, payload)
            info = dict((delta.get("diagnostics") or {}).get("incremental_delta") or {})
            source_prints = int(info.get("source_prints") or 0)
            delta_prints = int(info.get("delta_prints") or 0)
            ratio = (delta_prints / source_prints) if source_prints else 1.0
            rows.append(
                {
                    "source_path": str(source_path),
                    "checksum": checksum,
                    "region": info.get("region"),
                    "language": info.get("language"),
                    "source_cards": int(info.get("source_cards") or 0),
                    "source_prints": source_prints,
                    "delta_cards": int(info.get("delta_cards") or 0),
                    "delta_prints": delta_prints,
                    "delta_sets": int(info.get("delta_sets") or 0),
                    "delta_ratio": round(ratio, 6),
                    "change_reasons": info.get("change_reasons") or {},
                    "status": "pass" if source_prints > 0 and ratio <= MAX_DELTA_RATIO else "fail",
                }
            )
        session.rollback()

    report = {
        "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "fail",
        "production_writes": 0,
        "max_delta_ratio": MAX_DELTA_RATIO,
        "regions": rows,
    }
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))

    if report["status"] != "pass":
        failed = [
            f"{row['region']}: {row['delta_prints']}/{row['source_prints']} ({row['delta_ratio']:.1%}) reasons={row['change_reasons']}"
            for row in rows
            if row["status"] != "pass"
        ]
        raise SystemExit("One Piece delta readiness failed: " + "; ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
