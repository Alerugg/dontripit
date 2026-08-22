from __future__ import annotations

from pathlib import Path

from app.ingest.base import IngestStats
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


class YgoProDeckYugiohV2Connector(YgoProDeckYugiohConnector):
    """YGOPRODeck connector with release-aware incremental reads.

    The legacy remote loader always started at offset zero. With a fixed limit,
    scheduled jobs could repeatedly re-read the same first page forever. API v7
    supports ``sort=new`` plus ``startdate``/``dateregion``; incremental jobs use
    those parameters while full reconciliation keeps the original all-card path.

    The legacy connector also performs a catalog-wide Print repair after every
    incremental batch. That sweep checks print keys and primary images row by row
    across the full Yu-Gi-Oh catalog and can take longer than the production job
    timeout even when only a few dozen cards changed. Current source rows already
    self-heal through ``should_skip_existing_record`` + ``upsert`` and return
    touched entity ids for targeted Search V2 reindexing, so the scheduled V2
    freshness path deliberately leaves broad legacy/image cleanup to its dedicated
    maintenance work instead of coupling it to every daily refresh.
    """

    name = "ygoprodeck_yugioh"

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

    def repair_legacy_records(self, session, source, stats: IngestStats, **kwargs) -> dict:
        """Do not run the inherited catalog-wide repair in the daily V2 path."""
        if not bool(kwargs.get("incremental", True)):
            return super().repair_legacy_records(session, source, stats, **kwargs)

        self.logger.info(
            "ingest ygoprodeck_v2 legacy_repair_skipped "
            "owner=dedicated_legacy_image_maintenance"
        )
        return {}
