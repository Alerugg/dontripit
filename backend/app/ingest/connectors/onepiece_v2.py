from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

import requests

from app.ingest.connectors.onepiece import OnePieceConnector
from app.ingest.normalization import normalize_collector_number, normalize_variant


class OnePieceV2Connector(OnePieceConnector):
    """One Piece connector with collector-number Card identity and release provenance.

    The legacy official fallback grouped logical cards by visible character name
    and accepted only OP/ST/EB collector families. V2 treats the base collector
    number as the logical Card identity, accepts P/PRB families, deduplicates exact
    print identities, and preserves every official series appearance separately.
    """

    name = "onepiece"
    _V2_CODE_RE = re.compile(r"^(OP|ST|EB|PRB)(\d{1,2})-\d{3}$", flags=re.IGNORECASE)
    _PROMO_RE = re.compile(r"^P-\d{3}$", flags=re.IGNORECASE)

    @classmethod
    def _extract_v2_set_code(cls, collector_base: str | None) -> str | None:
        raw = str(collector_base or "").strip().upper().replace("_", "-")
        if not raw:
            return None
        if cls._PROMO_RE.fullmatch(raw):
            return "P"
        match = cls._V2_CODE_RE.fullmatch(raw)
        if not match:
            return None
        family, number = match.groups()
        return f"{family.upper()}-{int(number):02d}"

    @staticmethod
    def _logical_card_key(collector_base: str | None) -> str:
        normalized = normalize_collector_number(collector_base)
        return f"onepiece:{normalized}" if normalized else ""

    @staticmethod
    def _canonical_set_name(set_code: str, source_label: str) -> str:
        if set_code == "P":
            return "Promotion Cards"
        if set_code.startswith("PRB-"):
            return f"Premium Booster {set_code}"
        return str(source_label or set_code).strip() or set_code

    def _parse_official_cards_page(self, html: str, *, base_url: str) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        blocks = re.findall(
            r'<dl\s+class="modalCol"\s+id="([^"]+)"[^>]*>(.*?)</dl>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for print_id, body in blocks:
            raw_collector = str(print_id or "").strip().upper()
            if not raw_collector:
                continue
            collector_base, _, variant_suffix = raw_collector.partition("_")
            set_code = self._extract_v2_set_code(collector_base)
            if not set_code:
                continue

            name_match = re.search(
                r'<div\s+class="cardName">(.*?)</div>',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            rarity_match = re.search(
                r'<div\s+class="infoCol">.*?<span>[^<]+</span>\s*\|\s*<span>([^<]+)</span>',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            image_match = re.search(r'data-src="([^"]+)"', body, flags=re.IGNORECASE)
            if image_match is None:
                image_match = re.search(r'<img[^>]+src="([^"]+)"', body, flags=re.IGNORECASE)
            if image_match is None:
                continue

            image_url = urljoin(base_url, unescape(image_match.group(1))).split("?", 1)[0]
            name = re.sub(
                r"\s+",
                " ",
                unescape(re.sub(r"<[^>]+>", " ", name_match.group(1) if name_match else collector_base)),
            ).strip()
            rarity = re.sub(
                r"\s+",
                " ",
                unescape(rarity_match.group(1) if rarity_match else ""),
            ).strip() or None
            variant = self._normalize_onepiece_variant(variant_suffix or "default")

            records.append(
                {
                    "print_id": raw_collector,
                    "card_id": collector_base,
                    "collector_number": collector_base,
                    "set_code": set_code,
                    "name": name or collector_base,
                    "rarity": rarity,
                    "variant": variant,
                    "image_url": image_url,
                }
            )
        return records

    @staticmethod
    def _print_identity_key(entry: dict, *, language: str) -> str:
        return ":".join(
            [
                str(entry.get("set_code") or "").strip().lower(),
                normalize_collector_number(entry.get("collector_number")),
                str(language or "en").strip().lower(),
                normalize_variant(entry.get("variant")),
            ]
        )

    @staticmethod
    def _appearance_key(*, series_id: str, source_print_id: str) -> tuple[str, str]:
        return (str(series_id), str(source_print_id).strip().upper())

    def _merge_official_entry(
        self,
        *,
        cards_by_key: dict[str, dict],
        entry: dict,
        series_id: str,
        series_label: str,
        language: str,
    ) -> None:
        card_key = self._logical_card_key(entry.get("collector_number"))
        if not card_key:
            return

        card_row = cards_by_key.setdefault(
            card_key,
            {
                "id": card_key,
                "name": entry["name"],
                "collector_number": entry["collector_number"],
                "prints": [],
            },
        )
        # The official source should not rename one collector-number definition.
        # Keep the first name but preserve conflicts as metadata for later audit.
        if card_row["name"] != entry["name"]:
            aliases = card_row.setdefault("source_name_aliases", [])
            if entry["name"] not in aliases:
                aliases.append(entry["name"])

        identity_key = self._print_identity_key(entry, language=language)
        print_row = next(
            (row for row in card_row["prints"] if row.get("identity_key") == identity_key),
            None,
        )
        if print_row is None:
            print_row = {
                "id": entry["print_id"],
                "identity_key": identity_key,
                "set_code": entry["set_code"],
                "collector_number": entry["collector_number"],
                "rarity": entry["rarity"],
                "variant": entry["variant"],
                "image_url": entry["image_url"],
                "release_appearances": [],
                "alternate_source_images": [],
            }
            card_row["prints"].append(print_row)
        elif entry.get("image_url") and entry["image_url"] != print_row.get("image_url"):
            if entry["image_url"] not in print_row["alternate_source_images"]:
                print_row["alternate_source_images"].append(entry["image_url"])

        appearance = {
            "release_external_id": str(series_id),
            "release_name": series_label,
            "source_print_id": entry["print_id"],
        }
        existing_keys = {
            self._appearance_key(
                series_id=row["release_external_id"],
                source_print_id=row["source_print_id"],
            )
            for row in print_row["release_appearances"]
        }
        if self._appearance_key(series_id=series_id, source_print_id=entry["print_id"]) not in existing_keys:
            print_row["release_appearances"].append(appearance)

    def _load_official_cardlist_remote(self, *, limit: int | None = None) -> dict:
        base_url = self._env("ONEPIECE_OFFICIAL_CARDLIST_URL", self._DEFAULT_OFFICIAL_CARDLIST_URL)
        timeout = self._http_timeout()
        card_limit = self._coerce_limit(limit)
        headers = {"User-Agent": "TCGCatalogV2/1.0 (+https://github.com/Alerugg/dontripit)"}

        index_response = requests.get(base_url, timeout=timeout, headers=headers)
        index_response.raise_for_status()
        series_options = self._parse_official_series_options(index_response.text)
        if not series_options:
            raise ValueError("One Piece V2 official ingest found zero series options")

        language = "en"
        sets_by_code: dict[str, dict] = {}
        releases: list[dict] = []
        cards_by_key: dict[str, dict] = {}

        for series_id, label in series_options:
            releases.append(
                {
                    "source": "onepiece_official",
                    "external_id": str(series_id),
                    "name": label,
                    "language": language,
                    "region": "global-en",
                }
            )
            series_url = f"{base_url}?series={series_id}"
            series_response = requests.get(series_url, timeout=timeout, headers=headers)
            series_response.raise_for_status()
            entries = self._parse_official_cards_page(series_response.text, base_url=base_url)
            for entry in entries:
                set_code = entry["set_code"]
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
            raise ValueError("One Piece V2 official ingest found zero cards")

        cards = list(cards_by_key.values())
        if card_limit is not None:
            cards = cards[:card_limit]
            used_set_codes = {
                str(print_row.get("set_code") or "").strip()
                for card in cards
                for print_row in card.get("prints") or []
            }
            sets_by_code = {
                code: row for code, row in sets_by_code.items() if code in used_set_codes
            }
            used_release_ids = {
                appearance["release_external_id"]
                for card in cards
                for print_row in card.get("prints") or []
                for appearance in print_row.get("release_appearances") or []
            }
            releases = [row for row in releases if row["external_id"] in used_release_ids]

        physical_identity_conflicts = []
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

        return {
            "source": "onepiece_official_v2",
            "language": language,
            "sets": sorted(sets_by_code.values(), key=lambda row: row["code"]),
            "releases": releases,
            "cards": cards,
            "diagnostics": {
                "series_count": len(releases),
                "logical_card_count": len(cards),
                "print_count": sum(len(card.get("prints") or []) for card in cards),
                "release_link_count": sum(
                    len(print_row.get("release_appearances") or [])
                    for card in cards
                    for print_row in card.get("prints") or []
                ),
                "physical_identity_conflicts": physical_identity_conflicts,
            },
        }
