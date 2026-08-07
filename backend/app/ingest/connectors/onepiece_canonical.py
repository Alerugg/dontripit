from __future__ import annotations

import re

from app.ingest.connectors.onepiece_v2 import OnePieceV2Connector


class OnePieceCanonicalConnector(OnePieceV2Connector):
    """Production One Piece catalog connector.

    Canonical Card/Print identity must come from the official Bandai card list
    parsed by the V2 identity model. Secondary structured sources may enrich
    Search V2 attributes, but they must never replace canonical identity.

    Set names are resolved from the release whose own label carries the same
    commercial code. This prevents an OP-05 card reappearing in PRB-02 from
    accidentally renaming the canonical OP-05 set to the PRB-02 product name.
    """

    name = "onepiece"
    _RELEASE_CODE_RE = re.compile(r"\b(OP|ST|EB|PRB)[\s\-_]?(\d{1,2})\b", re.IGNORECASE)

    @classmethod
    def _release_set_code(cls, label: object) -> str | None:
        match = cls._RELEASE_CODE_RE.search(str(label or ""))
        if not match:
            return None
        family, number = match.groups()
        return f"{family.upper()}-{int(number):02d}"

    @classmethod
    def _canonicalize_set_names(cls, payload: dict) -> dict:
        release_name_by_code: dict[str, str] = {}
        for release in payload.get("releases") or []:
            name = str(release.get("name") or "").strip()
            code = cls._release_set_code(name)
            if code and name:
                release_name_by_code.setdefault(code, name)

        unmatched: list[str] = []
        for set_row in payload.get("sets") or []:
            code = str(set_row.get("code") or "").strip().upper()
            if code == "P":
                set_row["name"] = "Promotion Cards"
                continue
            canonical_name = release_name_by_code.get(code)
            if canonical_name:
                set_row["name"] = canonical_name
            else:
                unmatched.append(code)

        diagnostics = payload.setdefault("diagnostics", {})
        diagnostics["canonical_set_names_resolved"] = len(payload.get("sets") or []) - len(unmatched)
        diagnostics["canonical_set_names_unmatched"] = sorted(unmatched)
        return payload

    def _load_official_cardlist_remote(self, *, limit: int | None = None) -> dict:
        payload = super()._load_official_cardlist_remote(limit=limit)
        return self._canonicalize_set_names(payload)

    def _load_remote(self, *, limit: int | None = None) -> dict:
        return self._load_official_cardlist_remote(limit=limit)
