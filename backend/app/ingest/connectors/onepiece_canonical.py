from __future__ import annotations

import re
from collections import defaultdict

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


class OnePieceCanonicalConnector(OnePieceV2Connector):
    """Production One Piece catalog connector.

    Canonical Card/Print identity comes from Bandai's official card list. Product
    names are release provenance, while Set remains the collector-number family.
    This distinction matters for hybrid releases such as ``OP14-EB04`` where one
    commercial product legitimately contains more than one canonical set code.
    """

    name = "onepiece"
    _RELEASE_CODE_RE = re.compile(r"(OP|ST|EB|PRB)[\s\-_]?(\d{1,2})", re.IGNORECASE)

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
                # Multiple products legitimately use this set code. Keep Set
                # neutral and let CatalogRelease/PrintRelease carry product names.
                set_row["name"] = cls._neutral_set_name(code)
                ambiguous[code] = list(release_names)
            else:
                unmatched.append(code)

        diagnostics = payload.setdefault("diagnostics", {})
        diagnostics["canonical_set_names_resolved"] = len(payload.get("sets") or []) - len(unmatched)
        diagnostics["canonical_set_names_unmatched"] = sorted(unmatched)
        diagnostics["canonical_set_names_ambiguous"] = dict(sorted(ambiguous.items()))
        return payload

    def _load_official_cardlist_remote(self, *, limit: int | None = None) -> dict:
        payload = super()._load_official_cardlist_remote(limit=limit)
        return self._canonicalize_set_names(payload)

    def _load_remote(self, *, limit: int | None = None) -> dict:
        return self._load_official_cardlist_remote(limit=limit)
