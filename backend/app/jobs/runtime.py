from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.ingest.registry import is_connector_write_quarantined
from app.ingest.run import run_ingest


logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
DEFAULT_SCHEDULER_JOBS = "fixture_local:manual"


def start_scheduler_if_enabled() -> None:
    global _scheduler
    if os.getenv("ENABLE_SCHEDULER", "false").lower() != "true":
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    jobs = os.getenv("SCHEDULER_JOBS", DEFAULT_SCHEDULER_JOBS)
    for item in jobs.split(","):
        if ":" not in item:
            continue
        name, mode = [part.strip() for part in item.split(":", 1)]
        if is_connector_write_quarantined(name):
            logger.warning("runtime scheduler skipping quarantined write connector=%s mode=%s", name, mode)
            continue
        if mode == "daily":
            _scheduler.add_job(lambda n=name: run_ingest(n), "interval", hours=24, id=f"{name}_daily", replace_existing=True)
    _scheduler.start()
