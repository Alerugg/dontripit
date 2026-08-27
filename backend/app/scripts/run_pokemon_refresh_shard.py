from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
import traceback

import requests

from app.ingest.run import run_ingest


def _shard_config() -> tuple[int, int]:
    try:
        shard_count = int(os.getenv("POKEMON_SHARD_COUNT", "1"))
        shard_index = int(os.getenv("POKEMON_SHARD_INDEX", "0"))
    except ValueError as exc:
        raise RuntimeError("Pokemon shard configuration must be integer-valued") from exc
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise RuntimeError(
            f"Invalid Pokemon shard configuration: index={shard_index} count={shard_count}"
        )
    return shard_index, shard_count


def _diagnostic_path(language: str, shard_index: int, shard_count: int) -> Path:
    if shard_count == 1:
        return Path(f"pokemon-refresh-diagnostic-{language}.json")
    return Path(
        f"pokemon-refresh-diagnostic-{language}-shard-{shard_index}-of-{shard_count}.json"
    )


def main() -> int:
    language = os.environ["POKEMON_LANGUAGE"]
    shard_index, shard_count = _shard_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    diagnostic_path = _diagnostic_path(language, shard_index, shard_count)
    started = time.monotonic()
    current_attempt = None

    try:
        print(
            json.dumps(
                {
                    "event": "pokemon_refresh_start",
                    "language": language,
                    "shard_index": shard_index,
                    "shard_count": shard_count,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        for attempt in range(1, 4):
            current_attempt = attempt
            try:
                stats = run_ingest(
                    "tcgdex_pokemon",
                    lang=language,
                    incremental=True,
                    fixture=False,
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt == 3:
                    raise
                wait_seconds = 5 * (2 ** (attempt - 1))
                print(
                    json.dumps(
                        {
                            "event": "pokemon_refresh_transport_retry",
                            "language": language,
                            "shard_index": shard_index,
                            "shard_count": shard_count,
                            "attempt": attempt,
                            "wait_seconds": wait_seconds,
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                time.sleep(wait_seconds)

        result = {
            "status": "pass",
            "language": language,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "files_seen": int(stats.files_seen),
            "files_skipped": int(stats.files_skipped),
            "inserted": int(stats.records_inserted),
            "updated": int(stats.records_updated),
            "errors": int(stats.errors),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        if result["errors"] != 0:
            raise RuntimeError(f"Pokemon {language} refresh reported errors: {result!r}")
        diagnostic_path.write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        diagnostic = {
            "status": "fail",
            "language": language,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "attempt": current_attempt,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        diagnostic_path.write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(diagnostic, indent=2, sort_keys=True), flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
