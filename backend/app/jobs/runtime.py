from __future__ import annotations

import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.ingest.run import run_ingest

_scheduler: BackgroundScheduler | None = None


def start_scheduler_if_enabled() -> None:
    global _scheduler
    if os.getenv("ENABLE_SCHEDULER", "false").lower() != "true":
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    jobs = os.getenv("SCHEDULER_JOBS", "scryfall_mtg:daily,fixture_local:manual")
    for item in jobs.split(","):
        if ":" not in item:
            continue
        name, mode = [part.strip() for part in item.split(":", 1)]
        if mode == "daily":
            _scheduler.add_job(lambda n=name: run_ingest(n), "interval", hours=24, id=f"{name}_daily", replace_existing=True)
    _scheduler.start()
