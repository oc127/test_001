"""Top-level scheduler — APScheduler cron that owns the weekly and monthly jobs.

Run with:
    python -m inventory_bot.scheduler
or inside a systemd/Docker service.

Jobs
----
- weekly_alert: every Monday 09:00 (Asia/Shanghai), runs workflows.weekly_alert
- monthly_review: 1st of each month 09:00, runs workflows.monthly_review
- price_scan: Thursday 10:00, runs the 1688 price monitor

All cron hours are in `SETTINGS.timezone`. Override via env vars in `.env`.

Single-shot mode: `python -m inventory_bot.scheduler --once weekly` runs the
job immediately and exits — used from CI, from command line, or from the
user saying "跑一下" in 飞书.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import SETTINGS
from .clients.bitable import InventoryBotRepository
from .clients.feishu import FeishuBot
from .clients.supplier_1688 import SupplierPriceMonitor
from .clients.feishu import build_price_alert_card
from .workflows.monthly_review import run_monthly_review
from .workflows.weekly_alert import run_weekly_alert


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(level=SETTINGS.log_level, format=LOG_FORMAT)
logger = logging.getLogger("inventory_bot.scheduler")


# ===== Job wrappers =====

def job_weekly_alert() -> None:
    logger.info("=== weekly_alert job starting ===")
    report = run_weekly_alert()
    logger.info("weekly_alert finished: %s", report.to_dict())


def job_monthly_review() -> None:
    logger.info("=== monthly_review job starting ===")
    metrics = run_monthly_review()
    logger.info(
        "monthly_review finished: spend=¥%s, stockouts=%d",
        metrics.total_ordered_value_rmb,
        len(metrics.stockout_events),
    )


def job_price_scan() -> None:
    logger.info("=== price_scan job starting ===")
    repo = InventoryBotRepository()
    bot = FeishuBot()
    monitor = SupplierPriceMonitor()
    try:
        products = repo.fetch_products()
    except Exception as exc:
        logger.exception("Failed to fetch products for price scan: %s", exc)
        return
    alerts = monitor.scan_all(products)
    logger.info("price_scan: %d alerts", len(alerts))
    if alerts:
        card = build_price_alert_card(
            [
                {
                    "sku": a.sku,
                    "supplier": a.supplier,
                    "old_price_rmb": float(a.old_price_rmb),
                    "new_price_rmb": float(a.new_price_rmb),
                    "change_pct": a.change_pct,
                    "change_direction": a.change_direction,
                    "url": a.url,
                }
                for a in alerts
            ]
        )
        bot.send_card(card)


# ===== Scheduler setup =====

_WEEKDAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6
}


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=SETTINGS.timezone)

    # Weekly — Monday (or configured day) at configured hour
    weekday = SETTINGS.weekly_alert_day.lower()
    day_of_week = _WEEKDAY_MAP.get(weekday, 0)
    scheduler.add_job(
        job_weekly_alert,
        CronTrigger(day_of_week=day_of_week, hour=SETTINGS.weekly_alert_hour, minute=0),
        id="weekly_alert",
        replace_existing=True,
        misfire_grace_time=3600,  # 1 hour late is ok
    )

    # Monthly — 1st of each month
    scheduler.add_job(
        job_monthly_review,
        CronTrigger(
            day=SETTINGS.monthly_review_day,
            hour=SETTINGS.monthly_review_hour,
            minute=0,
        ),
        id="monthly_review",
        replace_existing=True,
        misfire_grace_time=3600 * 6,  # 6 hours late is ok
    )

    # Price scan — Thursday 10:00 (hardcoded; not critical enough to configure)
    scheduler.add_job(
        job_price_scan,
        CronTrigger(day_of_week=3, hour=10, minute=0),
        id="price_scan",
        replace_existing=True,
        misfire_grace_time=3600 * 12,
    )

    return scheduler


def _shutdown(scheduler: BlockingScheduler, sig, frame):
    logger.info("Received signal %s — shutting down scheduler", sig)
    try:
        scheduler.shutdown(wait=False)
    finally:
        sys.exit(0)


def run_once(job_name: str) -> None:
    jobs = {
        "weekly": job_weekly_alert,
        "monthly": job_monthly_review,
        "price": job_price_scan,
    }
    if job_name not in jobs:
        print(f"Unknown job: {job_name}. Choose from: {list(jobs)}")
        sys.exit(1)
    jobs[job_name]()


def main() -> None:
    parser = argparse.ArgumentParser(description="inventory_bot scheduler")
    parser.add_argument(
        "--once",
        choices=["weekly", "monthly", "price"],
        help="Run the given job immediately and exit",
    )
    args = parser.parse_args()

    if args.once:
        run_once(args.once)
        return

    scheduler = build_scheduler()
    signal.signal(signal.SIGINT, lambda s, f: _shutdown(scheduler, s, f))
    signal.signal(signal.SIGTERM, lambda s, f: _shutdown(scheduler, s, f))

    logger.info("Starting scheduler in %s…", SETTINGS.timezone)
    for job in scheduler.get_jobs():
        logger.info("  scheduled: %s @ %s", job.id, job.trigger)
    logger.info("Started at %s", datetime.now().isoformat())
    scheduler.start()


if __name__ == "__main__":
    main()
