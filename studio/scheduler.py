"""Run the video pipeline repeatedly for unattended channel automation."""

from __future__ import annotations

import logging
import time

import config
from pipeline import run


logging.basicConfig(
    filename="automation.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main() -> None:
    if not config.AUTOMATION_ENABLED:
        raise RuntimeError("Set AUTOMATION_ENABLED=true in .env before starting the scheduler.")
    if config.AUTOMATION_INTERVAL_HOURS < 1:
        raise ValueError("AUTOMATION_INTERVAL_HOURS must be at least 1.")

    interval_seconds = config.AUTOMATION_INTERVAL_HOURS * 60 * 60
    while True:
        try:
            run()
            logging.info("Automation run completed successfully.")
        except Exception:
            logging.exception("Automation run failed; the next scheduled run will continue.")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()