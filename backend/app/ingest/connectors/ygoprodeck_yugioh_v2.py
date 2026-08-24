from __future__ import annotations

from pathlib import Path

from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector
from app.models import Print
from app.scripts.build_yugioh_v2_snapshot_canonical import run as build_canonical_snapshot
from app.scripts.reconcile_yugioh_canonical_prints_v1 import _load_source_prints, _plan


class YgoProDeckYugiohV2Connector(YgoProDeckYugiohConnector):
    """YGOPRODeck connector with release-aware incremental reads.

    Daily source reads remain delta-only, but current physical canonical Print
    drift is reconciled after the delta with a bounded insert-only safety gate.
    This catches retrospective source additions such as newly published rarity
    variants without re-enabling the old catalog-wide legacy/image repair.

    The broad inherited repair is still reserved for explicit full
    reconciliation. The bounded current-source reconciler resolves every new
    Print to one existing canonical Card and one existing global Set, rejects
    tuple/id conflicts, refuses more than 500 writes, and returns touched ids so
    SourceConnector can perform targeted Search V2 reindexing.
    """

    name = "ygoprodeck_yugioh"
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

    def _reconcile_current_canonical_prints(self, session, stats: IngestStats) -> dict:
        output_dir = self.canonical_print_reconcile_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        build_canonical_snapshot(output_dir=output_dir)
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

    def repair_legacy_records(self, session, source, stats: IngestStats, **kwargs) -> dict:
        """Self-heal exact current Prints while keeping broad legacy repair out.

        Fixture runs intentionally stay local and deterministic. Remote daily
        incremental runs reconcile only exact current canonical Print identities.
        Explicit full reconciliation retains the inherited broad legacy repair.
        """
        incremental = bool(kwargs.get("incremental", True))
        fixture = bool(kwargs.get("fixture", False))

        if incremental:
            if fixture:
                self.logger.info(
                    "ingest ygoprodeck_v2 canonical_print_reconcile_skipped "
                    "reason=fixture"
                )
                return {}
            self.logger.info(
                "ingest ygoprodeck_v2 canonical_print_reconcile_start "
                "mode=bounded_current_source"
            )
            return self._reconcile_current_canonical_prints(session, stats)

        repair_kwargs = dict(kwargs)
        repair_kwargs["incremental"] = True
        self.logger.info("ingest ygoprodeck_v2 legacy_repair_start mode=full_reconciliation")
        result = super().repair_legacy_records(session, source, stats, **repair_kwargs)
        self.logger.info("ingest ygoprodeck_v2 legacy_repair_done mode=full_reconciliation")
        return result
