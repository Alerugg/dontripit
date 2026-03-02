from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import PrintFieldProvenance

TRACKED_FIELDS = ("rarity", "language", "collector_number")


def upsert_print_provenance(session, print_id: int, source: str, values: dict[str, str]) -> int:
    conflicts = 0
    now = datetime.now(timezone.utc)
    for field in TRACKED_FIELDS:
        value = str(values.get(field, ""))
        existing = session.execute(
            select(PrintFieldProvenance).where(
                PrintFieldProvenance.print_id == print_id,
                PrintFieldProvenance.field_name == field,
                PrintFieldProvenance.source == source,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                PrintFieldProvenance(
                    print_id=print_id,
                    field_name=field,
                    source=source,
                    value_text=value,
                    updated_at=now,
                )
            )
        else:
            existing.value_text = value
            existing.updated_at = now

        other = session.execute(
            select(PrintFieldProvenance).where(
                PrintFieldProvenance.print_id == print_id,
                PrintFieldProvenance.field_name == field,
                PrintFieldProvenance.source != source,
                PrintFieldProvenance.value_text != value,
            )
        ).first()
        if other:
            conflicts += 1
    return conflicts
