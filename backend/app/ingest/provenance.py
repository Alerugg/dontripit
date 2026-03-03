from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import FieldProvenance


TRACKED_FIELDS = ("rarity", "language", "collector_number", "oracle_id")


def upsert_field_provenance(
    session,
    entity_type: str,
    entity_id: int,
    source: str,
    values: dict[str, str | dict | None],
) -> int:
    conflicts = 0
    now = datetime.now(timezone.utc)

    for field, value in values.items():
        value_text = value if isinstance(value, str) else None
        value_json = value if isinstance(value, dict) else None

        existing = session.execute(
            select(FieldProvenance).where(
                FieldProvenance.entity_type == entity_type,
                FieldProvenance.entity_id == entity_id,
                FieldProvenance.field_name == field,
                FieldProvenance.source == source,
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                FieldProvenance(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    field_name=field,
                    source=source,
                    value_text=value_text,
                    value_json=value_json,
                    updated_at=now,
                )
            )
        else:
            existing.value_text = value_text
            existing.value_json = value_json
            existing.updated_at = now

        other = session.execute(
            select(FieldProvenance).where(
                FieldProvenance.entity_type == entity_type,
                FieldProvenance.entity_id == entity_id,
                FieldProvenance.field_name == field,
                FieldProvenance.source != source,
                FieldProvenance.value_text != value_text,
            )
        ).first()
        if other:
            conflicts += 1
    return conflicts
