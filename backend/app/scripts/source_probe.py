from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from app.ingest.connectors.onepiece import OnePieceConnector
from app.ingest.connectors.riftbound import RiftboundConnector
from app.ingest.connectors.scryfall_mtg_v2 import ScryfallMtgV2Connector
from app.ingest.connectors.tcgdex_pokemon import TcgdexPokemonConnector
from app.ingest.connectors.ygoprodeck_yugioh import YgoProDeckYugiohConnector


def _probe(name: str, fn) -> dict:
    started = datetime.now(timezone.utc)
    try:
        count = int(fn())
        return {
            "name": name,
            "status": "success",
            "records": count,
            "error": None,
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "status": "failed",
            "records": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        }


def _riftbound_is_configured() -> bool:
    official = bool(
        str(os.getenv("RIFTBOUND_API_BASE_URL") or "").strip()
        and str(os.getenv("RIFTBOUND_API_KEY") or "").strip()
    )
    fallback = bool(str(os.getenv("RIFTBOUND_FALLBACK_BASE_URL") or "").strip())
    return official or fallback


def run_probe() -> dict:
    results = []

    results.append(
        _probe(
            "pokemon",
            lambda: len(TcgdexPokemonConnector().load(None, fixture=False, set="base1", limit=3)),
        )
    )
    results.append(
        _probe(
            "onepiece",
            lambda: len(OnePieceConnector().load(None, fixture=False, limit=5)),
        )
    )
    results.append(
        _probe(
            "mtg",
            lambda: len(ScryfallMtgV2Connector()._load_incremental(limit=5, last_run_at=None)),
        )
    )
    results.append(
        _probe(
            "yugioh",
            lambda: len(YgoProDeckYugiohConnector()._load_remote(limit=5, page_size=5)),
        )
    )

    if _riftbound_is_configured():
        results.append(
            _probe(
                "riftbound",
                lambda: len(RiftboundConnector().load(None, fixture=False, limit=5)),
            )
        )
    else:
        results.append(
            {
                "name": "riftbound",
                "status": "skipped",
                "records": 0,
                "error": "No official credentials or fallback URL configured",
                "duration_seconds": 0.0,
            }
        )

    required = {"pokemon", "onepiece", "mtg", "yugioh"}
    required_failures = [
        row["name"] for row in results if row["name"] in required and row["status"] != "success"
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "results": results,
        "required_failures": required_failures,
        "status": "failed" if required_failures else "success",
        "exit_code": 1 if required_failures else 0,
    }


def main() -> int:
    payload = run_probe()
    print(json.dumps(payload, ensure_ascii=False))
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
