from __future__ import annotations

import hashlib
import json
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.ingest.normalized_schema import NormalizedPayloadError, parse_normalized_payload
from app.models import IngestRun, Source, SourceRecord, SourceSyncState
from app.scripts.reindex_search import rebuild_search_documents


@dataclass
class IngestStats:
    files_seen: int = 0
    files_skipped: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    errors: int = 0


class SourceConnector:
    name = "base"
    uses_normalized_payload = False
    logger = logging.getLogger("app.ingest")

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        if path is None:
            return []
        root = Path(path)
        if not root.exists():
            repo_root = Path(__file__).resolve().parents[3]
            for candidate in (repo_root / str(path), repo_root / "backend" / str(path)):
                if candidate.exists():
                    root = candidate
                    break
        payloads: list[tuple[Path, dict, str]] = []
        for file_path in sorted(root.glob("*.json")):
            raw = file_path.read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            payloads.append((file_path, json.loads(raw.decode("utf-8")), checksum))
        return payloads

    def normalize(self, payload: dict, **kwargs) -> dict:
        return payload

    def ensure_source(self, session):
        source = session.execute(select(Source).where(Source.name == self.name)).scalar_one_or_none()
        if source:
            return source
        source = Source(name=self.name, description=f"Connector source for {self.name}")
        session.add(source)
        session.flush()
        return source

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        raise NotImplementedError

    def validate_payload_contract(self, payload: dict) -> dict:
        if not self.uses_normalized_payload:
            return payload
        try:
            parsed = parse_normalized_payload(payload)
        except NormalizedPayloadError as exc:
            raise ValueError(f"connector={self.name} emitted invalid normalized payload: {exc}") from exc
        return payload | {"_normalized_contract": parsed}

    def touched_entity_ids(self) -> dict[str, set[int]]:
        return {}

    @staticmethod
    def collect_touched_entity_ids(result: dict | None) -> dict[str, set[int]]:
        touched = {"card_ids": set(), "set_ids": set(), "print_ids": set()}
        if not isinstance(result, dict):
            return touched

        aliases = {
            "card": "card_ids",
            "cards": "card_ids",
            "card_id": "card_ids",
            "card_ids": "card_ids",
            "set": "set_ids",
            "sets": "set_ids",
            "set_id": "set_ids",
            "set_ids": "set_ids",
            "print": "print_ids",
            "prints": "print_ids",
            "print_id": "print_ids",
            "print_ids": "print_ids",
        }

        for key, value in result.items():
            target = aliases.get(key)
            if target is None or value is None:
                continue
            if isinstance(value, (set, list, tuple)):
                touched[target].update(item for item in value if isinstance(item, int))
            elif isinstance(value, int):
                touched[target].add(value)

        return touched

    def default_cursor(self, **kwargs) -> dict:
        return {}

    def should_skip_existing_record(self, existing_record: SourceRecord, **kwargs) -> bool:
        return True

    def should_bootstrap(self, session, source: Source, **kwargs) -> bool:
        return False

    def repair_legacy_records(self, session, source: Source, stats: IngestStats, **kwargs) -> dict:
        return {}

    def run(self, session, path: str | Path | None = None, **kwargs) -> IngestStats:
        stats = IngestStats()
        source = self.ensure_source(session)

        sync_state = session.execute(select(SourceSyncState).where(SourceSyncState.source_id == source.id)).scalar_one_or_none()
        if sync_state is None:
            sync_state = SourceSyncState(source_id=source.id, cursor_json={})
            session.add(sync_state)
            session.flush()

        ingest_run = IngestRun(source_id=source.id, status="running", counts_json={})
        session.add(ingest_run)
        session.flush()
        ingest_run_id = ingest_run.id

        # Persist the "running" state immediately so async refresh status can observe
        # a newly started ingest run before connector work completes.
        session.commit()

        last_run_at = sync_state.last_run_at

        def _counts_payload() -> dict[str, int]:
            return {
                "inserted": stats.records_inserted,
                "updated": stats.records_updated,
                "skipped": stats.files_skipped,
                "errors": stats.errors,
                "files_seen": stats.files_seen,
            }

        try:
            bootstrap = self.should_bootstrap(session, source, **kwargs)
            payloads = self.load(path, session=session, last_run_at=last_run_at, bootstrap=bootstrap, **kwargs)
            touched_ids = {"card_ids": set(), "set_ids": set(), "print_ids": set()}
            processed_payloads = 0
            for file_path, payload, checksum in payloads:
                stats.files_seen += 1
                existing_record = session.execute(
                    select(SourceRecord).where(SourceRecord.source_id == source.id, SourceRecord.checksum == checksum)
                ).scalar_one_or_none()
                incremental = bool(kwargs.get("incremental", True))
                if incremental and existing_record and self.should_skip_existing_record(existing_record, session=session, **kwargs):
                    stats.files_skipped += 1
                    self.logger.info(
                        "ingest skip connector=%s file=%s reason=existing_by_checksum checksum=%s",
                        self.name,
                        file_path,
                        checksum,
                    )
                    continue

                if existing_record is None:
                    session.add(SourceRecord(source_id=source.id, checksum=checksum, raw_json=payload))
                normalized = self.normalize(payload, **kwargs)
                normalized = self.validate_payload_contract(normalized)
                upsert_result = self.upsert(session, normalized, stats, source_name=source.name, **kwargs)
                processed_payloads += 1
                if processed_payloads == 1 or processed_payloads % 10 == 0:
                    self.logger.info(
                        "ingest progress connector=%s processed=%s files_seen=%s inserted=%s updated=%s skipped=%s",
                        self.name,
                        processed_payloads,
                        stats.files_seen,
                        stats.records_inserted,
                        stats.records_updated,
                        stats.files_skipped,
                    )
                touched_from_upsert = self.collect_touched_entity_ids(upsert_result)
                for key in touched_ids:
                    touched_ids[key].update(touched_from_upsert[key])

            repair_result = self.repair_legacy_records(
                session,
                source,
                stats,
                **kwargs,
            )
            touched_from_repair = self.collect_touched_entity_ids(repair_result)
            for key in touched_ids:
                touched_ids[key].update(touched_from_repair[key])

            if incremental:
                if any(touched_ids.values()):
                    self.logger.info(
                        "ingest reindex_start connector=%s mode=targeted card_ids=%s set_ids=%s print_ids=%s",
                        self.name,
                        len(touched_ids["card_ids"]),
                        len(touched_ids["set_ids"]),
                        len(touched_ids["print_ids"]),
                    )
                    rebuild_search_documents(
                        session,
                        card_ids=touched_ids["card_ids"],
                        set_ids=touched_ids["set_ids"],
                        print_ids=touched_ids["print_ids"],
                    )
                    self.logger.info("ingest reindex_done connector=%s mode=targeted", self.name)
                elif processed_payloads > 0:
                    self.logger.info("ingest reindex_start connector=%s mode=full_fallback", self.name)
                    rebuild_search_documents(session)
                    self.logger.info("ingest reindex_done connector=%s mode=full_fallback", self.name)
            else:
                self.logger.info("ingest reindex_start connector=%s mode=full_refresh", self.name)
                rebuild_search_documents(session)
                self.logger.info("ingest reindex_done connector=%s mode=full_refresh", self.name)

            sync_state.last_run_at = datetime.now(timezone.utc)
            sync_state.cursor_json = self.default_cursor(last_run_at=sync_state.last_run_at, bootstrap=bootstrap, **kwargs)
            sync_state.updated_at = datetime.now(timezone.utc)

            ingest_run.status = "success"
            ingest_run.finished_at = datetime.now(timezone.utc)
            ingest_run.counts_json = _counts_payload()
        except Exception as exc:
            stats.errors += 1
            session.rollback()

            failed_run = session.get(IngestRun, ingest_run_id)
            if failed_run is not None:
                failed_run.status = "fail"
                failed_run.finished_at = datetime.now(timezone.utc)
                failed_run.error_summary = "\n".join(traceback.format_exception_only(type(exc), exc)).strip()
                failed_run.counts_json = _counts_payload()
                session.commit()
            raise

        return stats

    @staticmethod
    def checksum(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
