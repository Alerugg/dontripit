from __future__ import annotations

"""V5 regional writer: tolerate the certified legacy production identity safely.

Production predates the current Alembic identity and still carries a unique
legacy (source, url) constraint. V4 collection remains unchanged. This writer
resolves both the current canonical identity and, only when production exposes
those legacy columns, the exact same source+URL identity. Legacy columns are
kept synchronized on every write so the historical constraint cannot reject
otherwise-canonical rows. Any split identity or cross-game/cross-region
collision fails closed.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any

from sqlalchemy import create_engine, text

from scripts import sync_regional_content_daily as v1
from scripts import sync_regional_content_daily_v4 as v4  # noqa: F401 - applies V4 source patches


def _same_identity(current: dict[str, Any], record: dict[str, Any]) -> bool:
    return (
        str(current.get("source_key") or "") == str(record["source_key"])
        and str(current.get("region") or "") == str(record["region"])
        and str(current.get("item_url") or "") == str(record["item_url"])
    )


def _same_legacy_identity(current: dict[str, Any], record: dict[str, Any]) -> bool:
    return (
        str(current.get("source") or "") == str(record["source_key"])
        and str(current.get("url") or "") == str(record["item_url"])
    )


def _legacy_identity_columns(conn) -> bool:
    if conn.dialect.name != "postgresql":
        return False
    columns = set(
        conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'regional_tcg_content'
                """
            )
        ).scalars()
    )
    return {"source", "url"}.issubset(columns)


def _find_current_row(
    conn,
    record: dict[str, Any],
    *,
    expected_game_id: int,
    has_legacy_identity: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    legacy_fields = ", source, url" if has_legacy_identity else ""
    select_fields = f"""
        id, game_id, region, locale, kind, source_key, source_name, source_url,
        item_url, title, published_date, release_date, raw_json{legacy_fields}
    """
    canonical_row = conn.execute(
        text(
            f"""
            SELECT {select_fields}
            FROM regional_tcg_content
            WHERE source_key = :source_key
              AND region = :region
              AND item_url = :item_url
            """
        ),
        {
            "source_key": record["source_key"],
            "region": record["region"],
            "item_url": record["item_url"],
        },
    ).mappings().one_or_none()
    canonical = dict(canonical_row) if canonical_row is not None else None

    legacy_rows: list[Any] = []
    if has_legacy_identity:
        legacy_rows = conn.execute(
            text(
                f"""
                SELECT {select_fields}
                FROM regional_tcg_content
                WHERE source = :source_key
                  AND url = :item_url
                """
            ),
            {
                "source_key": record["source_key"],
                "item_url": record["item_url"],
            },
        ).mappings().all()
        if len(legacy_rows) > 1:
            raise RuntimeError(
                "Legacy regional identity is not unique for "
                f"{record['source_key']} {record['item_url']}"
            )

    legacy = dict(legacy_rows[0]) if legacy_rows else None
    if canonical is not None and legacy is not None and int(canonical["id"]) != int(legacy["id"]):
        raise RuntimeError(
            "Refusing split canonical/legacy regional identity: "
            f"source={record['source_key']} region={record['region']} url={record['item_url']} "
            f"canonical_id={canonical['id']} legacy_id={legacy['id']}"
        )

    current = canonical or legacy
    if current is None:
        return None, None

    if int(current["game_id"]) != int(expected_game_id) or str(current["region"]) != str(record["region"]):
        raise RuntimeError(
            "Refusing to adopt regional row across canonical identity: "
            f"source={record['source_key']} url={record['item_url']} "
            f"expected_game_id={expected_game_id} actual_game_id={current['game_id']} "
            f"expected_region={record['region']} actual_region={current['region']}"
        )
    return current, "canonical" if canonical is not None else "legacy"


def apply_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")

    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    unchanged = 0
    legacy_adoptions = 0

    with engine.begin() as conn:
        game_ids = {
            str(row.slug): int(row.id)
            for row in conn.execute(
                text("SELECT slug,id FROM games WHERE slug IN ('pokemon','onepiece','mtg','yugioh')")
            )
        }
        missing = sorted(set(v1.ACTIVE_GAMES) - set(game_ids))
        if missing:
            raise RuntimeError(f"Missing canonical games for regional content: {missing}")

        has_legacy_identity = _legacy_identity_columns(conn)

        for record in records:
            expected_game_id = game_ids[record["game"]]
            current, identity_origin = _find_current_row(
                conn,
                record,
                expected_game_id=expected_game_id,
                has_legacy_identity=has_legacy_identity,
            )
            target = v1._material_state(record, game_id=expected_game_id, current=current)

            identity_params = {
                "region": record["region"],
                "source_key": record["source_key"],
                "item_url": record["item_url"],
            }
            if has_legacy_identity:
                identity_params.update(
                    {
                        "legacy_source": record["source_key"],
                        "legacy_url": record["item_url"],
                    }
                )

            if current is None:
                legacy_insert_columns = ", source, url" if has_legacy_identity else ""
                legacy_insert_values = ", :legacy_source, :legacy_url" if has_legacy_identity else ""
                conn.execute(
                    text(
                        f"""
                        INSERT INTO regional_tcg_content
                          (game_id, region, locale, kind, source_key, source_name, source_url,
                           item_url, title, published_date, release_date, raw_json,
                           first_seen_at, last_seen_at{legacy_insert_columns})
                        VALUES
                          (:game_id, :region, :locale, :kind, :source_key, :source_name, :source_url,
                           :item_url, :title, :published_date, :release_date, CAST(:raw_json AS jsonb),
                           :now, :now{legacy_insert_values})
                        """
                    ),
                    {
                        **target,
                        **identity_params,
                        "raw_json": json.dumps(target["raw_json"], ensure_ascii=False, sort_keys=True),
                        "now": now,
                    },
                )
                inserted += 1
                continue

            legacy_identity_matches = not has_legacy_identity or _same_legacy_identity(current, record)
            if v1._same_material(current, target) and _same_identity(current, record) and legacy_identity_matches:
                unchanged += 1
                continue

            legacy_update = (
                ",\n                        source = :legacy_source,\n                        url = :legacy_url"
                if has_legacy_identity
                else ""
            )
            conn.execute(
                text(
                    f"""
                    UPDATE regional_tcg_content
                    SET game_id = :game_id,
                        region = :region,
                        locale = :locale,
                        kind = :kind,
                        source_key = :source_key,
                        source_name = :source_name,
                        source_url = :source_url,
                        item_url = :item_url,
                        title = :title,
                        published_date = :published_date,
                        release_date = :release_date,
                        raw_json = CAST(:raw_json AS jsonb),
                        last_seen_at = :now{legacy_update}
                    WHERE id = :id
                    """
                ),
                {
                    **target,
                    **identity_params,
                    "id": int(current["id"]),
                    "raw_json": json.dumps(target["raw_json"], ensure_ascii=False, sort_keys=True),
                    "now": now,
                },
            )
            updated += 1
            if identity_origin == "legacy":
                legacy_adoptions += 1

    return {
        "applied_at": now.isoformat(),
        "records": len(records),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "material_writes": inserted + updated,
        "legacy_identity_columns": has_legacy_identity,
        "legacy_adoptions": legacy_adoptions,
    }


v1.apply_records = apply_records


if __name__ == "__main__":
    raise SystemExit(v1.main())
