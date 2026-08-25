from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.models import Card, Game, Print
from app.scripts.build_yugioh_v2_snapshot_canonical import run as build_canonical_snapshot
from app.scripts.reconcile_yugioh_canonical_prints_v1 import _load_source_prints, _plan


class YgoProDeckYugiohV2Connector(YgoProDeckYugiohConnector):
    """YGOPRODeck connector with release-aware incremental reads.

    Daily source reads remain delta-only, but current canonical Card/Print drift
    is reconciled after the delta with bounded insert-only safety gates. Cards
    are reconciled first because YGOPRODeck can publish a canonical Card before
    any physical set/print evidence exists; those zero-print Cards must still be
    materialized by their exact source identity so current-source parity remains
    complete. Prints are then reconciled against the same fresh snapshot.

    The broad inherited repair is still reserved for explicit full
    reconciliation. The bounded current-source Card reconciler never matches by
    name, never rewrites an existing Card, rejects source-id/card-key conflicts,
    refuses more than 50 inserts, and returns touched ids for targeted Search V2
    reindexing. The Print reconciler retains its existing exact identity, tuple,
    and 500-write safeguards.
    """

    name = "ygoprodeck_yugioh"
    canonical_card_reconcile_max_writes = 50
    canonical_print_reconcile_max_writes = 500
    canonical_print_reconcile_output_dir = Path("/tmp/ygo-canonical-print-self-heal-v1")

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = bool(kwargs.get("fixture", False))
        limit = kwargs.get("limit")
        incremental = bool(kwargs.get("incremental", True))

        self.logger.info(
            "ingest ygoprodeck_v2 load_start fixture=%s incremental=%s limit=%s",
            fixture,
            incremental,
            limit,
        )

        if fixture:
            fixture_path = self._resolve_fixture_path(path)
            cards = self._load_fixture(fixture_path, limit=limit)
        elif incremental:
            cards = self._load_incremental_remote(
                limit=limit,
                base_url=kwargs.get("base_url") or self.base_url,
                page_size=kwargs.get("page_size"),
                last_run_at=kwargs.get("last_run_at"),
            )
        else:
            cards = super()._load_remote(
                limit=limit,
                base_url=kwargs.get("base_url") or self.base_url,
                page_size=kwargs.get("page_size"),
            )

        payloads: list[tuple[Path, dict, str]] = []
        for idx, card in enumerate(cards):
            payloads.append(
                (
                    Path(f"yugioh_card_{card.get('id', idx)}.json"),
                    card,
                    self.checksum(card),
                )
            )

        self.logger.info(
            "ingest ygoprodeck_v2 load_done fixture=%s incremental=%s cards=%s limit=%s",
            fixture,
            incremental,
            len(payloads),
            limit,
        )
        return payloads

    def _load_incremental_remote(
        self,
        *,
        limit: int | None = None,
        base_url: str | None = None,
        page_size: int | None = None,
        last_run_at=None,
    ) -> list[dict]:
        endpoint = f"{base_url or self.base_url}/cardinfo.php"
        normalized_page_size = max(int(page_size or 500), 1)
        requested_limit = None if limit is None else max(int(limit), 0)
        if requested_limit == 0:
            return []

        cards: list[dict] = []
        seen_keys: set[str] = set()
        offset = 0

        while requested_limit is None or len(cards) < requested_limit:
            remaining = None if requested_limit is None else requested_limit - len(cards)
            if remaining is not None and remaining <= 0:
                break

            batch_size = normalized_page_size if remaining is None else min(normalized_page_size, remaining)
            params: dict[str, object] = {
                "num": batch_size,
                "offset": offset,
                "sort": "new",
            }
            if last_run_at is not None:
                params["startdate"] = last_run_at.date().isoformat()
                params["dateregion"] = "tcg"

            payload = self._request_json(endpoint, params=params)
            page_cards = payload.get("data") or []
            if not page_cards:
                break

            for card in page_cards:
                dedupe_key = str(card.get("id") or self.checksum(card))
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                cards.append(card)
                if requested_limit is not None and len(cards) >= requested_limit:
                    break

            if len(page_cards) < batch_size:
                break
            offset += batch_size

        return cards

    @staticmethod
    def _load_source_cards(output_dir: Path) -> list[dict]:
        path = output_dir / "cards.jsonl"
        rows: list[dict] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid canonical YGO Card row at line {line_number}")
            rows.append(row)
        return rows

    @staticmethod
    def _merge_reconcile_results(*results: dict) -> dict:
        merged = {"card_ids": set(), "set_ids": set(), "print_ids": set()}
        for result in results:
            for key in merged:
                merged[key].update(result.get(key) or set())
        return merged

    def _reconcile_current_canonical_cards_from_snapshot(
        self,
        session,
        stats: IngestStats,
        output_dir: Path,
    ) -> dict:
        game_id = session.execute(select(Game.id).where(Game.slug == "yugioh")).scalar_one_or_none()
        if game_id is None:
            raise RuntimeError("cannot reconcile current YGO Cards: yugioh Game row is missing")

        source_rows = self._load_source_cards(output_dir)
        existing_rows = session.execute(select(Card).where(Card.game_id == game_id)).scalars().all()
        by_source_id = {
            str(row.yugoprodeck_id).strip(): row
            for row in existing_rows
            if str(row.yugoprodeck_id or "").strip()
        }
        by_card_key = {
            str(row.card_key).strip(): row
            for row in existing_rows
            if str(row.card_key or "").strip()
        }

        missing: list[tuple[dict, str, str, str]] = []
        for row in source_rows:
            source_id = str(row.get("yugoprodeck_id") or row.get("source_card_id") or "").strip()
            card_key = str(row.get("card_key") or "").strip()
            name = str(row.get("name") or "").strip()
            if not source_id or not card_key or not name:
                raise RuntimeError(
                    "invalid current canonical YGO Card identity: "
                    f"source_id={source_id!r} card_key={card_key!r} name={name!r}"
                )

            id_owner = by_source_id.get(source_id)
            key_owner = by_card_key.get(card_key)
            if id_owner is not None and key_owner is not None and id_owner.id != key_owner.id:
                raise RuntimeError(
                    "current canonical YGO Card identity collision: "
                    f"source_id={source_id} id_owner={id_owner.id} "
                    f"card_key={card_key} key_owner={key_owner.id}"
                )

            existing = id_owner or key_owner
            if existing is not None:
                existing_source_id = str(existing.yugoprodeck_id or "").strip()
                existing_card_key = str(existing.card_key or "").strip()
                if existing_source_id != source_id or existing_card_key != card_key:
                    raise RuntimeError(
                        "current canonical YGO Card identity drift requires explicit review: "
                        f"card_id={existing.id} source_id={source_id} "
                        f"existing_source_id={existing_source_id!r} card_key={card_key} "
                        f"existing_card_key={existing_card_key!r}"
                    )
                continue

            missing.append((row, source_id, card_key, name))

        if len(missing) > self.canonical_card_reconcile_max_writes:
            raise RuntimeError(
                "current canonical YGO Card reconcile exceeded bounded write ceiling: "
                f"missing={len(missing)} max_writes={self.canonical_card_reconcile_max_writes}"
            )

        touched_cards: set[int] = set()
        for _row, source_id, card_key, name in missing:
            record = Card(
                game_id=int(game_id),
                name=name,
                yugoprodeck_id=source_id,
                card_key=card_key,
            )
            session.add(record)
            session.flush()
            stats.records_inserted += 1
            touched_cards.add(int(record.id))
            by_source_id[source_id] = record
            by_card_key[card_key] = record

        self.logger.info(
            "ingest ygoprodeck_v2 canonical_card_reconcile_done "
            "source_cards=%s missing_before=%s writes=%s max_writes=%s",
            len(source_rows),
            len(missing),
            len(touched_cards),
            self.canonical_card_reconcile_max_writes,
        )
        return {
            "card_ids": touched_cards,
            "set_ids": set(),
            "print_ids": set(),
        }

    def _reconcile_current_canonical_prints_from_snapshot(
        self,
        session,
        stats: IngestStats,
        output_dir: Path,
    ) -> dict:
        _manifest, source_rows = _load_source_prints(output_dir)
        plan = _plan(
            session,
            source_rows,
            max_writes=self.canonical_print_reconcile_max_writes,
        )

        touched_cards: set[int] = set()
        touched_sets: set[int] = set()
        touched_prints: set[int] = set()
        for item in plan["planned"]:
            row = item["source"]
            record = Print(
                card_id=int(item["card_id"]),
                set_id=int(item["set_id"]),
                collector_number=str(row["collector_number"]),
                language=row.get("language"),
                rarity=row.get("rarity"),
                is_foil=bool(row.get("is_foil", False)),
                variant=str(row["variant"]),
                print_key=str(row["print_key"]),
                yugioh_id=str(row["yugioh_id"]),
            )
            session.add(record)
            session.flush()
            stats.records_inserted += 1
            touched_cards.add(int(record.card_id))
            touched_sets.add(int(record.set_id))
            touched_prints.add(int(record.id))

        self.logger.info(
            "ingest ygoprodeck_v2 canonical_print_reconcile_done "
            "source_prints=%s missing_before=%s writes=%s max_writes=%s",
            len(source_rows),
            plan["missing_before"],
            len(touched_prints),
            self.canonical_print_reconcile_max_writes,
        )
        return {
            "card_ids": touched_cards,
            "set_ids": touched_sets,
            "print_ids": touched_prints,
        }

    def _reconcile_current_canonical_prints(self, session, stats: IngestStats) -> dict:
        """Backward-compatible exact Print-only reconciler used by focused tests."""
        output_dir = self.canonical_print_reconcile_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        build_canonical_snapshot(output_dir=output_dir)
        return self._reconcile_current_canonical_prints_from_snapshot(session, stats, output_dir)

    def _reconcile_current_canonical_catalog(self, session, stats: IngestStats) -> dict:
        """Build one fresh snapshot, then self-heal exact Cards before Prints."""
        output_dir = self.canonical_print_reconcile_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        build_canonical_snapshot(output_dir=output_dir)
        card_result = self._reconcile_current_canonical_cards_from_snapshot(session, stats, output_dir)
        print_result = self._reconcile_current_canonical_prints_from_snapshot(session, stats, output_dir)
        return self._merge_reconcile_results(card_result, print_result)

    def repair_legacy_records(self, session, source, stats: IngestStats, **kwargs) -> dict:
        """Self-heal exact current Cards/Prints while keeping broad repair out.

        Fixture runs intentionally stay local and deterministic. Remote daily
        incremental runs reconcile only exact current canonical identities.
        Explicit full reconciliation retains the inherited broad legacy repair.
        """
        incremental = bool(kwargs.get("incremental", True))
        fixture = bool(kwargs.get("fixture", False))

        if incremental:
            if fixture:
                self.logger.info(
                    "ingest ygoprodeck_v2 canonical_catalog_reconcile_skipped "
                    "reason=fixture"
                )
                return {}
            self.logger.info(
                "ingest ygoprodeck_v2 canonical_catalog_reconcile_start "
                "mode=bounded_current_source"
            )
            return self._reconcile_current_canonical_catalog(session, stats)

        repair_kwargs = dict(kwargs)
        repair_kwargs["incremental"] = True
        self.logger.info("ingest ygoprodeck_v2 legacy_repair_start mode=full_reconciliation")
        result = super().repair_legacy_records(session, source, stats, **repair_kwargs)
        self.logger.info("ingest ygoprodeck_v2 legacy_repair_done mode=full_reconciliation")
        return result
