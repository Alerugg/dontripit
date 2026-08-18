from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

import requests

from app.ingest.connectors.onepiece import OnePieceConnector
from app.ingest.normalization import normalize_collector_number, normalize_variant


class OnePieceV2Connector(OnePieceConnector):
    """One Piece connector with collector-number Card identity and release provenance.

    Logical Card identity is the base collector number. Exact physical print
    identity preserves the official suffix (P1/P2/P3/R1/...) instead of collapsing
    every P-suffix into the generic word ``parallel``. Commercial series/product
    appearances are retained independently as release provenance.
    """

    name = "onepiece"
    _V2_CODE_RE = re.compile(r"^(OP|ST|EB|PRB)(\d{1,2})-\d{3}$", flags=re.IGNORECASE)
    _PROMO_RE = re.compile(r"^P-\d{3}$", flags=re.IGNORECASE)
    _DETAIL_LABELS = (
        "Cost",
        "Attribute",
        "Power",
        "Counter",
        "Color",
        "Block",
        "Type",
        "Effect",
        "Trigger",
        "Card Set(s)",
    )

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
    def _exact_print_variant(variant_suffix: object) -> str:
        raw = str(variant_suffix or "").strip().lower()
        if not raw or raw == "default":
            return "default"
        # Source suffix is part of physical identity: P1 != P2 != P5.
        raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        return raw or "default"

    @staticmethod
    def _variant_family(exact_variant: str) -> str:
        value = str(exact_variant or "default").lower()
        if re.fullmatch(r"p\d+", value):
            return "parallel"
        if re.fullmatch(r"r\d+", value):
            return "reprint"
        return "default" if value == "default" else value

    @staticmethod
    def _canonical_set_name(set_code: str, source_label: str) -> str:
        if set_code == "P":
            return "Promotion Cards"
        if set_code.startswith("PRB-"):
            return f"Premium Booster {set_code}"
        return str(source_label or set_code).strip() or set_code

    @classmethod
    def _modal_text_lines(cls, body: str) -> list[str]:
        """Turn one official modal into stable text lines without trusting CSS classes.

        The official site exposes a TEXT VIEW with semantic labels such as Effect,
        Trigger, Cost, Power and Type. Parsing the label boundaries is more robust
        than coupling the ingest to presentation-only class names.
        """
        cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", body, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(
            r'<img\b[^>]*\balt=["\']([^"\']*)["\'][^>]*>',
            lambda match: f"\n{match.group(1)}\n",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"<(?:br|hr)\b[^>]*>|</(?:p|div|h[1-6]|li|dt|dd|span|section|article|ul|ol)>",
            "\n",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        plain = unescape(cleaned).replace("\xa0", " ")
        lines = []
        for raw_line in plain.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return lines

    @classmethod
    def _extract_labeled_detail(cls, lines: list[str], label: str) -> str | None:
        labels = {candidate.casefold() for candidate in cls._DETAIL_LABELS}
        start = None
        for index, line in enumerate(lines):
            if line.rstrip(":").strip().casefold() == label.casefold():
                start = index + 1
                break
        if start is None:
            return None

        values: list[str] = []
        for line in lines[start:]:
            normalized = line.rstrip(":").strip().casefold()
            if normalized in labels:
                break
            # Presentation-only noise from the official block icon does not add
            # card rules meaning.
            if normalized == "icon":
                continue
            values.append(line)
        value = re.sub(r"\s+", " ", "\n".join(values)).strip()
        return value or None

    @classmethod
    def _extract_official_details(cls, body: str) -> dict[str, object]:
        lines = cls._modal_text_lines(body)
        field_map = {
            "cost": "Cost",
            "attribute": "Attribute",
            "power": "Power",
            "counter": "Counter",
            "color": "Color",
            "block": "Block",
            "card_type": "Type",
            "effect": "Effect",
            "trigger": "Trigger",
        }
        details: dict[str, object] = {
            key: cls._extract_labeled_detail(lines, label)
            for key, label in field_map.items()
        }
        details["official"] = True
        details["source"] = "onepiece_official"
        return details

    def _parse_official_cards_page(self, html: str, *, base_url: str) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
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
            variant = self._exact_print_variant(variant_suffix)

            records.append(
                {
                    "print_id": raw_collector,
                    "card_id": collector_base,
                    "collector_number": collector_base,
                    "set_code": set_code,
                    "name": name or collector_base,
                    "rarity": rarity,
                    "variant": variant,
                    "variant_family": self._variant_family(variant),
                    "image_url": image_url,
                    "details": self._extract_official_details(body),
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
        if card_row["name"] != entry["name"]:
            aliases = card_row.setdefault("source_name_aliases", [])
            if entry["name"] not in aliases:
                aliases.append(entry["name"])

        identity_key = self._print_identity_key(entry, language=language)
        print_row = next(
            (row for row in card_row["prints"] if row.get("identity_key") == identity_key),
            None,
        )
        incoming_details = dict(entry.get("details") or {})
        if print_row is None:
            print_row = {
                "id": entry["print_id"],
                "identity_key": identity_key,
                "set_code": entry["set_code"],
                "collector_number": entry["collector_number"],
                "rarity": entry["rarity"],
                "variant": entry["variant"],
                "variant_family": entry.get("variant_family") or self._variant_family(entry["variant"]),
                "image_url": entry["image_url"],
                "details": incoming_details,
                "release_appearances": [],
                "alternate_source_images": [],
                "alternate_source_details": [],
            }
            card_row["prints"].append(print_row)
        else:
            if entry.get("image_url") and entry["image_url"] != print_row.get("image_url"):
                if entry["image_url"] not in print_row["alternate_source_images"]:
                    print_row["alternate_source_images"].append(entry["image_url"])
            existing_details = dict(print_row.get("details") or {})
            if incoming_details and not existing_details:
                print_row["details"] = incoming_details
            elif incoming_details and existing_details and incoming_details != existing_details:
                if incoming_details not in print_row["alternate_source_details"]:
                    print_row["alternate_source_details"].append(incoming_details)

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
                    "region": "global-en",
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
            raise ValueError("One Piece V2 official ingest found zero cards")

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

        return {
            "source": "onepiece_official_v2",
            "language": language,
            "sets": sorted(sets_by_code.values(), key=lambda row: row["code"]),
            "releases": releases,
            "cards": cards,
            "diagnostics": {
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