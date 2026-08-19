from __future__ import annotations

import hashlib
import json

VERIFIED_AT_UTC = "2026-08-19"
SOURCE = "Cardmarket first-party public Yu-Gi-Oh OCG product/expansion pages"

# This is a frozen external-evidence certificate, intentionally separate from
# runtime HTTP access. GitHub-hosted runners currently receive Cardmarket
# 403/rate-limit responses even though the same public first-party pages are
# independently reachable and were reviewed before this certificate was
# frozen. No unsupported rarity geometry is present here.
EVIDENCE = {
    "DOCS": {
        "idExpansion": "4680",
        "pages": [
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V1-Ultra-Rare",
                "observed": "Scarlight Red Dragon Archfiend (V.1 - Ultra Rare) — Dimension of Chaos (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/it/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V2-Secret-Rare",
                "observed": "Scarlight Red Dragon Archfiend (V.2 - Secret Rare) — Dimension of Chaos (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Dimension-of-Chaos-Japanese/Scarlight-Red-Dragon-Archfiend-V4-Holographic-Rare",
                "observed": "Scarlight Red Dragon Archfiend V.4 = Holographic Rare; the same first-party page exposes V.3 = Ultimate Rare in its product-image carousel",
            },
        ],
        "contracts": {
            "secret|ultra|ultimate": ["ultra", "secret", "ultimate"],
            "ghost|secret|ultra|ultimate": ["ultra", "secret", "ultimate", "ghost"],
        },
        "excluded_geometries": ["secret|super"],
    },
    "LTGY": {
        "idExpansion": "4725",
        "pages": [
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Mecha-Phantom-Beast-Dracossack-V1-Ultra-Rare",
                "observed": "Mecha Phantom Beast Dracossack (V.1 - Ultra Rare) — Lord of the Tachyon Galaxy (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Mecha-Phantom-Beast-Dracossack-V2-Ultimate-Rare",
                "observed": "Mecha Phantom Beast Dracossack (V.2 - Ultimate Rare) — Lord of the Tachyon Galaxy (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V1-Ultra-Rare",
                "observed": "Number 107: Galaxy-Eyes Tachyon Dragon (V.1 - Ultra Rare) — Lord of the Tachyon Galaxy (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V2-Ultimate-Rare",
                "observed": "Number 107: Galaxy-Eyes Tachyon Dragon (V.2 - Ultimate Rare) — Lord of the Tachyon Galaxy (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Lord-of-the-Tachyon-Galaxy-OCG/Number-107-Galaxy-Eyes-Tachyon-Dragon-V3-Holographic-Rare",
                "observed": "Number 107: Galaxy-Eyes Tachyon Dragon (V.3 - Holographic Rare) — Lord of the Tachyon Galaxy (OCG)",
            },
        ],
        "contracts": {
            "ultra|ultimate": ["ultra", "ultimate"],
            "ghost|ultra|ultimate": ["ultra", "ultimate", "ghost"],
        },
        "excluded_geometries": [],
    },
    "CSOC": {
        "idExpansion": "4809",
        "pages": [
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V1-Ultra-Rare",
                "observed": "Black Rose Dragon (V.1 - Ultra Rare) — Crossroads of Chaos (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V2-Ultimate-Rare",
                "observed": "Black Rose Dragon (V.2 - Ultimate Rare) — Crossroads of Chaos (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese/Black-Rose-Dragon-V3-Holographic-Rare",
                "observed": "Black Rose Dragon (V.3 - Holographic Rare) — Crossroads of Chaos (OCG)",
            },
            {
                "url": "https://www.cardmarket.com/en/YuGiOh/Products/Singles/Crossroads-of-Chaos-Japanese",
                "observed": "The OCG expansion page independently lists V.1 Ultra / V.2 Ultimate rows for Revived King Ha Des and Doomkaiser Dragon, and the three Black Rose Dragon OCG versions",
            },
        ],
        "contracts": {
            "ultra|ultimate": ["ultra", "ultimate"],
            "ghost|ultra|ultimate": ["ultra", "ultimate", "ghost"],
        },
        "excluded_geometries": ["common|commonparallel"],
    },
}


def contract_payload() -> dict:
    return {
        "verified_at_utc": VERIFIED_AT_UTC,
        "source": SOURCE,
        "evidence": EVIDENCE,
    }


def contract_sha256() -> str:
    raw = json.dumps(contract_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
