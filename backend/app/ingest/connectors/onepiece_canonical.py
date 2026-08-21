from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import requests
from sqlalchemy import select

from app.ingest.base import IngestStats
from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector
from app.ingest.normalization import normalize_collector_number, normalize_variant
from app.models import Card, Game, Print, Set


class OnePieceCanonicalConnector(OnePieceV2Connector):
    """Production One Piece catalog connector.

    Canonical Card/Print identity comes from Bandai's official card lists. The
    global English catalog remains the preferred canonical display source, while
    official Asia-English and Japanese catalogs close regional release/promo gaps
    without inventing cards or translations.

    Product names are release provenance, while Set remains the collector-number
    family. This distinction matters for hybrid releases such as ``OP14-EB04``
    where one commercial product legitimately contains more than one canonical
    set code.
    """

    name = "onepiece"
    _RELEASE_CODE_RE = re.compile(r"(OP|ST|EB|PRB)[\s\-_]?(\d{1,2})", re.IGNORECASE)
    _DEFAULT_ASIA_EN_CARDLIST_URL = "https://asia-en.onepiece-cardgame.com/cardlist/"
    _DEFAULT_JP_CARDLIST_URL = "https://www.onepiece-cardgame.com/cardlist/"

    @classmethod
    def _release_set_codes(cls, label: object) -> list[str]:
        result: list[str] = []
        for family, number in cls._RELEASE_CODE_RE.findall(str(label or "")):
            code = f"{family.upper()}-{int(number):02d}"
            if code not in result:
                result.append(code)
        return result

    @classmethod
    def _release_set_code(cls, label: object) -> str | None:
        codes = cls._release_set_codes(label)
        return codes[0] if codes else None

    @staticmethod
    def _neutral_set_name(code: str) -> str:
        if code.startswith("OP-"):
            return f"Booster Series [{code}]"
        if code.startswith("ST-"):
            return f"Starter Deck Series [{code}]"
        if code.startswith("EB-"):
            return f"Extra Booster Series [{code}]"
        if code.startswith("PRB-"):
            return f"Premium Booster Series [{code}]"
        return code

    @classmethod
    def _canonicalize_set_names(cls, payload: dict) -> dict:
        release_names_by_code: dict[str, list[str]] = defaultdict(list)
        for release in payload.get("releases") or []:
            name = str(release.get("name") or "").strip()
            if not name:
                continue
            for code in cls._release_set_codes(name):
                if name not in release_names_by_code[code]:
                    release_names_by_code[code].append(name)

        unmatched: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        for set_row in payload.get("sets") or []:
            code = str(set_row.get("code") or "").strip().upper()
            if code == "P":
                set_row["name"] = "Promotion Cards"
                continue

            release_names = release_names_by_code.get(code, [])
            if len(release_names) == 1:
                set_row["name"] = release_names[0]
            elif len(release_names) > 1:
                set_row["name"] = cls._neutral_set_name(code)
                ambiguous[code] = list(release_names)
            else:
                unmatched.append(code)

        diagnostics = payload.setdefault("diagnostics", {})
        diagnostics["canonical_set_names_resolved"] = len(payload.get("sets") or []) - len(unmatched)
        diagnostics["canonical_set_names_unmatched"] = sorted(unmatched)
        diagnostics["canonical_set_names_ambiguous"] = dict(sorted(ambiguous.items()))
        return payload

    @staticmethod
    def _promo_collectors(payload: dict) -> set[str]:
        collectors: set[str] = set()
        for card in payload.get("cards") or []:
            for print_row in card.get("prints") or []:
                if str(print_row.get("set_code") or "").strip().upper() != "P":
                    continue
                collector = str(print_row.get("collector_number") or "").strip().upper()
                if collector:
                    collectors.add(collector)
        return collectors

    def _load_official_regional_cardlist_remote(
        self,
        *,
        base_url: str,
        language: str,
        region: str,
        limit: int | None = None,
    ) -> dict:
        """Load one official Bandai region using the certified V2 identity rules."""

        timeout = self._http_timeout()
        card_limit = self._coerce_limit(limit)
        headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}

        index_response = requests.get(base_url, timeout=timeout, headers=headers)
        index_response.raise_for_status()
        series_options = self._parse_official_series_options(index_response.text)
        if not series_options:
            raise ValueError(f"One Piece official ingest found zero series options for region={region}")

        sets_by_code: dict[str, dict] = {}
        releases_by_external_id: dict[str, dict] = {}
        cards_by_key: dict[str, dict] = {}

        for series_id, label in series_options:
            releases_by_external_id.setdefault(
                str(series_id),
                {
                    "source": "onepiece_official",
                    "external_id": str(series_id),
                    "name": label,
                    "language": language,
                    "region": region,
                },
            )
            series_url = f"{base_url}?series={series_id}"
            series_response = requests.get(series_url, timeout=timeout, headers=headers)
            series_response.raise_for_status()
            entries = self._parse_official_cards_page(series_response.text, base_url=base_url)
            for entry in entries:
                set_code = str(entry["set_code"])
                sets_by_code.setdefault(
                    set_code,
                    {
                        "id": set_code.lower(),
                        "code": set_code,
                        "name": self._canonical_set_name(set_code, label),
                    },
                )
                self._merge_official_entry(
                    cards_by_key=cards_by_key,
                    entry=entry,
                    series_id=series_id,
                    series_label=label,
                    language=language,
                )
                if card_limit is not None and len(cards_by_key) >= card_limit:
                    break
            if card_limit is not None and len(cards_by_key) >= card_limit:
                break

        if not cards_by_key:
            raise ValueError(f"One Piece official ingest found zero cards for region={region}")

        releases = list(releases_by_external_id.values())
        cards = list(cards_by_key.values())
        if card_limit is not None:
            cards = cards[:card_limit]
            used_set_codes = {
                str(print_row.get("set_code") or "").strip()
                for card in cards
                for print_row in card.get("prints") or []
            }
            sets_by_code = {code: row for code, row in sets_by_code.items() if code in used_set_codes}
            used_release_ids = {
                appearance["release_external_id"]
                for card in cards
                for print_row in card.get("prints") or []
                for appearance in print_row.get("release_appearances") or []
            }
            releases = [row for row in releases if row["external_id"] in used_release_ids]

        physical_identity_conflicts = []
        source_text_conflicts = []
        for card in cards:
            for print_row in card.get("prints") or []:
                alternate_images = print_row.get("alternate_source_images") or []
                if alternate_images:
                    physical_identity_conflicts.append(
                        {
                            "card_key": card["id"],
                            "collector_number": print_row["collector_number"],
                            "variant": print_row["variant"],
                            "primary_image": print_row["image_url"],
                            "alternate_images": alternate_images,
                            "release_appearances": print_row["release_appearances"],
                        }
                    )
                alternate_details = print_row.get("alternate_source_details") or []
                if alternate_details:
                    source_text_conflicts.append(
                        {
                            "card_key": card["id"],
                            "collector_number": print_row["collector_number"],
                            "variant": print_row["variant"],
                            "primary_details": print_row.get("details") or {},
                            "alternate_details": alternate_details,
                            "release_appearances": print_row["release_appearances"],
                        }
                    )

        payload = {
            "source": "onepiece_official_v2",
            "language": language,
            "region": region,
            "sets": sorted(sets_by_code.values(), key=lambda row: row["code"]),
            "releases": releases,
            "cards": cards,
            "diagnostics": {
                "source_region": region,
                "source_language": language,
                "source_option_rows": len(series_options),
                "unique_release_ids": len(releases_by_external_id),
                "series_count": len(releases),
                "logical_card_count": len(cards),
                "print_count": sum(len(card.get("prints") or []) for card in cards),
                "release_link_count": sum(
                    len(print_row.get("release_appearances") or [])
                    for card in cards
                    for print_row in card.get("prints") or []
                ),
                "physical_identity_conflicts": physical_identity_conflicts,
                "source_text_conflicts": source_text_conflicts,
                "effect_print_count": sum(
                    1
                    for card in cards
                    for print_row in card.get("prints") or []
                    if str((print_row.get("details") or {}).get("effect") or "").strip()
                ),
                "trigger_print_count": sum(
                    1
                    for card in cards
                    for print_row in card.get("prints") or []
                    if str((print_row.get("details") or {}).get("trigger") or "").strip()
                ),
            },
        }
        return self._canonicalize_set_names(payload)

    def _load_official_cardlist_remote(self, *, limit: int | None = None) -> dict:
        payload = super()._load_official_cardlist_remote(limit=limit)
        payload["region"] = "global-en"
        payload.setdefault("diagnostics", {})["source_region"] = "global-en"
        return self._canonicalize_set_names(payload)

    def _load_official_asia_en_cardlist_remote(self, *, limit: int | None = None) -> dict:
        base_url = self._env("ONEPIECE_OFFICIAL_ASIA_EN_CARDLIST_URL", self._DEFAULT_ASIA_EN_CARDLIST_URL)
        return self._load_official_regional_cardlist_remote(
            base_url=base_url,
            language="en",
            region="asia-en",
            limit=limit,
        )

    def _load_official_jp_cardlist_remote(self, *, limit: int | None = None) -> dict:
        base_url = self._env("ONEPIECE_OFFICIAL_JP_CARDLIST_URL", self._DEFAULT_JP_CARDLIST_URL)
        return self._load_official_regional_cardlist_remote(
            base_url=base_url,
            language="ja",
            region="jp",
            limit=limit,
        )

    def _annotate_regional_promo_audit(self, payloads: list[dict]) -> dict:
        by_region = {
            str(payload.get("region") or "unknown"): self._promo_collectors(payload)
            for payload in payloads
        }
        global_promos = by_region.get("global-en", set())
        asia_promos = by_region.get("asia-en", set())
        jp_promos = by_region.get("jp", set())
        regional_union = asia_promos | jp_promos
        audit = {
            "global_en_count": len(global_promos),
            "asia_en_count": len(asia_promos),
            "jp_count": len(jp_promos),
            "regional_only_vs_global": sorted(regional_union - global_promos),
            "asia_only_vs_global": sorted(asia_promos - global_promos),
            "jp_only_vs_global": sorted(jp_promos - global_promos),
        }
        for payload in payloads:
            payload.setdefault("diagnostics", {})["regional_promo_audit"] = audit
        self._regional_promo_audit = audit
        self.logger.info(
            "ingest onepiece regional_promo_audit global_en=%s asia_en=%s jp=%s regional_only=%s",
            audit["global_en_count"],
            audit["asia_en_count"],
            audit["jp_count"],
            len(audit["regional_only_vs_global"]),
        )
        return audit

    def load(self, path: str | Path | None = None, **kwargs) -> list[tuple[Path, dict, str]]:
        fixture = kwargs.get("fixture")
        source_mode = self._source_mode(fixture=fixture)
        if source_mode != "remote" or (isinstance(path, str) and path.startswith(("https://", "http://"))):
            return super().load(path, **kwargs)

        limit = self._coerce_limit(kwargs.get("limit"))
        payloads = [
            self._load_official_cardlist_remote(limit=limit),
            self._load_official_asia_en_cardlist_remote(limit=limit),
            self._load_official_jp_cardlist_remote(limit=limit),
        ]
        self._annotate_regional_promo_audit(payloads)
        names = ["onepiece_official_global_en.json", "onepiece_official_asia_en.json", "onepiece_official_jp.json"]
        return [
            (Path(name), payload, self.checksum(payload))
            for name, payload in zip(names, payloads, strict=True)
        ]

    def _ensure_official_card_keys(self, session, payload: dict, stats: IngestStats) -> None:
        """Pre-seed official Card keys so generic legacy name fallback cannot merge identities."""

        if str(payload.get("source") or "") != "onepiece_official_v2":
            return
        game = self._ensure_game(session, stats)
        rows = [
            (str(item.get("id") or "").strip().lower(), str(item.get("name") or "").strip())
            for item in payload.get("cards") or []
        ]
        rows = [(key, name) for key, name in rows if key and name]
        if not rows:
            return
        keys = [key for key, _name in rows]
        existing_keys = set(
            session.execute(
                select(Card.card_key).where(Card.game_id == game.id, Card.card_key.in_(keys))
            ).scalars().all()
        )
        for card_key, card_name in rows:
            if card_key in existing_keys:
                continue
            session.add(Card(game_id=game.id, name=card_name, card_key=card_key))
            session.flush()
            existing_keys.add(card_key)
            stats.records_inserted += 1

    def _preserve_preferred_canonical_names(self, session, payload: dict) -> dict:
        """Prevent lower-priority regional sources from overwriting preferred names.

        Priority is global English > Asia English > Japanese. A regional-only card
        may use its official regional name until a higher-priority source publishes
        that same collector identity on a later refresh.
        """

        region = str(payload.get("region") or "global-en").strip().lower()
        if region == "global-en":
            return payload

        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        if game is None:
            return payload

        card_keys = [str(row.get("id") or "").strip().lower() for row in payload.get("cards") or []]
        card_keys = [key for key in card_keys if key]
        existing_card_names = dict(
            session.execute(
                select(Card.card_key, Card.name).where(Card.game_id == game.id, Card.card_key.in_(card_keys))
            ).all()
        ) if card_keys else {}

        set_codes = [str(row.get("code") or "").strip().lower() for row in payload.get("sets") or []]
        set_codes = [code for code in set_codes if code]
        existing_set_names = dict(
            session.execute(
                select(Set.code, Set.name).where(Set.game_id == game.id, Set.code.in_(set_codes))
            ).all()
        ) if set_codes else {}

        copied = dict(payload)
        copied_cards = []
        for row in payload.get("cards") or []:
            item = dict(row)
            existing_name = existing_card_names.get(str(item.get("id") or "").strip().lower())
            if existing_name:
                item["name"] = existing_name
            copied_cards.append(item)
        copied["cards"] = copied_cards

        copied_sets = []
        for row in payload.get("sets") or []:
            item = dict(row)
            existing_name = existing_set_names.get(str(item.get("code") or "").strip().lower())
            if existing_name:
                item["name"] = existing_name
            copied_sets.append(item)
        copied["sets"] = copied_sets
        return copied

    def _sanitize_lower_priority_prints(self, session, payload: dict) -> dict:
        """Keep regional source ids/images from stealing higher-priority EN ownership."""

        region = str(payload.get("region") or "global-en").strip().lower()
        if region == "global-en":
            return payload

        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one_or_none()
        existing_en: set[tuple[str, str, str]] = set()
        if game is not None and region == "asia-en":
            existing_en = {
                (
                    str(set_code or "").strip().lower(),
                    normalize_collector_number(collector),
                    normalize_variant(variant),
                )
                for set_code, collector, variant in session.execute(
                    select(Set.code, Print.collector_number, Print.variant)
                    .join(Print, Print.set_id == Set.id)
                    .where(Set.game_id == game.id, Print.language == "en")
                ).all()
            }

        copied = dict(payload)
        copied_cards = []
        for card in payload.get("cards") or []:
            card_copy = dict(card)
            copied_prints = []
            for print_row in card.get("prints") or []:
                print_copy = dict(print_row)
                # The generic writer has one legacy PrintIdentifier namespace.
                # Regional ids would collide across languages, so only the
                # highest-priority global source owns that legacy identifier.
                print_copy["id"] = None
                if region == "asia-en":
                    identity = (
                        str(print_copy.get("set_code") or "").strip().lower(),
                        normalize_collector_number(print_copy.get("collector_number")),
                        normalize_variant(print_copy.get("variant")),
                    )
                    if identity in existing_en:
                        # Do not replace a global-English primary image with the
                        # lower-priority Asia mirror for the same physical print.
                        print_copy["image_url"] = ""
                copied_prints.append(print_copy)
            card_copy["prints"] = copied_prints
            copied_cards.append(card_copy)
        copied["cards"] = copied_cards
        return copied

    def _assert_promo_materialized(self, session, payload: dict) -> None:
        expected: set[tuple[str, str]] = set()
        for card in payload.get("cards") or []:
            for print_row in card.get("prints") or []:
                if str(print_row.get("set_code") or "").strip().upper() != "P":
                    continue
                collector = normalize_collector_number(print_row.get("collector_number"))
                variant = normalize_variant(print_row.get("variant"))
                if collector:
                    expected.add((collector, variant))
        if not expected:
            return

        language = self._normalize_language(str(payload.get("language") or "en"))
        game = session.execute(select(Game).where(Game.slug == "onepiece")).scalar_one()
        promo_set = session.execute(
            select(Set).where(Set.game_id == game.id, Set.code == "p")
        ).scalar_one_or_none()
        if promo_set is None:
            raise RuntimeError(f"One Piece promo materialization failed language={language}: missing Set P")

        actual = {
            (normalize_collector_number(collector), normalize_variant(variant))
            for collector, variant in session.execute(
                select(Print.collector_number, Print.variant).where(
                    Print.set_id == promo_set.id,
                    Print.language == language,
                )
            ).all()
        }
        missing = sorted(expected - actual)
        if missing:
            raise RuntimeError(
                f"One Piece promo materialization failed language={language} missing={missing[:20]} total_missing={len(missing)}"
            )
        self.logger.info(
            "ingest onepiece promo_materialization_ok language=%s expected=%s",
            language,
            len(expected),
        )

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        self._ensure_official_card_keys(session, payload, stats)
        prepared = self._preserve_preferred_canonical_names(session, payload)
        prepared = self._sanitize_lower_priority_prints(session, prepared)
        touched = super().upsert(session, prepared, stats, **kwargs)
        self._assert_promo_materialized(session, prepared)
        return touched

    def _load_remote(self, *, limit: int | None = None) -> dict:
        # Kept for backwards-compatible callers that explicitly request one
        # payload. Production ``load`` above is the regional EN+Asia+JP path.
        return self._load_official_cardlist_remote(limit=limit)
