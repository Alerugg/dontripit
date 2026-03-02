from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app import db
from app.ingest.registry import get_connector
from app.models import Source, SourceRecord


def _ensure_source(session, name: str) -> Source:
    source = session.execute(select(Source).where(Source.name == name)).scalar_one_or_none()
    if source is None:
        source = Source(name=name, description=f"Connector {name}")
        session.add(source)
        session.flush()
    return source


def run_ingest(connector_name: str, path: str) -> dict:
    connector = get_connector(connector_name)
    db.init_engine()

    aggregate = {
        "files_total": 0,
        "files_skipped": 0,
        "games": 0,
        "sets": 0,
        "cards": 0,
        "prints": 0,
        "images": 0,
        "identifiers": 0,
        "updates": 0,
    }

    with db.SessionLocal() as session:
        source = _ensure_source(session, connector.name)

        for file_path, payload in connector.load(Path(path)):
            aggregate["files_total"] += 1
            raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            checksum = hashlib.sha256(raw_bytes).hexdigest()

            existing_record = session.execute(
                select(SourceRecord).where(SourceRecord.source_id == source.id, SourceRecord.checksum == checksum)
            ).scalar_one_or_none()
            if existing_record:
                aggregate["files_skipped"] += 1
                print(f"[ingest] skip {file_path.name}: checksum already ingested")
                continue

            session.add(SourceRecord(source_id=source.id, checksum=checksum, raw_json=payload))
            normalized = connector.normalize(payload)
            stats = connector.upsert(session, normalized)
            for key in ("games", "sets", "cards", "prints", "images", "identifiers", "updates"):
                aggregate[key] += stats.get(key, 0)
            print(f"[ingest] upserted {file_path.name}: {stats}")

        session.commit()

    print(f"[ingest] done: {aggregate}")
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ingest connector")
    parser.add_argument("connector", help="connector name")
    parser.add_argument("--path", required=True, help="Path to JSON fixture file or directory")
    args = parser.parse_args()

    run_ingest(args.connector, args.path)


if __name__ == "__main__":
    main()
