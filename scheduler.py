"""Планировщик рассылки маршрута через день в заданное время (Europe/Moscow)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import storage

logger = logging.getLogger(__name__)

TZ = ZoneInfo(config.TIMEZONE_NAME)


def should_send_today(today: date, last_sent: date | None) -> bool:
    if last_sent is None:
        return True
    return (today - last_sent).days >= config.SEND_INTERVAL_DAYS


def setup_scheduler(send_callback: Callable[[], Awaitable[None]]) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)

    async def job() -> None:
        today = date.today()
        last_sent = storage.get_last_broadcast_date()
        if not should_send_today(today, last_sent):
            logger.info(
                "Пропуск рассылки: последняя была %s, интервал %s дн.",
                last_sent,
                config.SEND_INTERVAL_DAYS,
            )
            return
        await send_callback()
        storage.set_last_broadcast_date(today)

    scheduler.add_job(
        job,
        trigger=CronTrigger(hour=config.SEND_HOUR, minute=config.SEND_MINUTE, timezone=TZ),
        id="morning_route_broadcast",
        replace_existing=True,
    )
    return scheduler
