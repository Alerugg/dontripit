from __future__ import annotations

import argparse
import os
import time

from apscheduler.schedulers.background import BackgroundScheduler

from app.ingest.run import run_ingest


def _parse_jobs(raw: str) -> list[tuple[str, str]]:
    jobs = []
    for item in (raw or "").split(","):
        if not item.strip() or ":" not in item:
            continue
        name, mode = item.split(":", 1)
        jobs.append((name.strip(), mode.strip()))
    return jobs


def run_once() -> None:
    for name, mode in _parse_jobs(os.getenv("SCHEDULER_JOBS", "scryfall_mtg:daily,fixture_local:manual")):
        if mode == "manual":
            continue
        run_ingest(name, fixture=name == "scryfall_mtg")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scheduler runner")
    parser.add_argument("--once", action="store_true", help="Run jobs once and exit")
    args = parser.parse_args()

    if os.getenv("ENABLE_SCHEDULER", "false").lower() != "true":
        return

    if args.once:
        run_once()
        return

    scheduler = BackgroundScheduler()
    for name, mode in _parse_jobs(os.getenv("SCHEDULER_JOBS", "scryfall_mtg:daily,fixture_local:manual")):
        if mode == "daily":
            scheduler.add_job(lambda n=name: run_ingest(n), "interval", hours=24, id=f"{name}_daily", replace_existing=True)
    scheduler.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
