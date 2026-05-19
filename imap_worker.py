"""
Отдельный процесс только для входящей почты (IMAP).

На Railway: второй сервис из того же репозитория:
  Start command: python imap_worker.py

Общее с ботом: DATABASE_URL (Postgres), BOT_TOKEN (только send_message, без polling).
На сервисе бота: IMAP_DEDICATED_WORKER=1 — IMAP в bot.py не запускается.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import config
from database import init_db

logger = logging.getLogger(__name__)


def _truthy(name: str, default: str = "") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_imap_worker_defaults() -> None:
    """Дефолты для отдельного IMAP-сервиса (можно переопределить в Railway Variables)."""
    defaults = {
        "MAX_IMAP_CONCURRENT": "20",
        "INCOMING_MAIL_POLL_SECONDS": "120",
        "IMAP_PER_ACCOUNT_INTERVAL_SEC": "120",
        "IMAP_CYCLE_SLEEP_SEC": "10",
        "IMAP_ACCOUNT_TIMEOUT_SEC": "45",
        "IMAP_CONNECT_TIMEOUT_SEC": "25",
        "IMAP_MAILING_PAUSE": "per_user",
        "IMAP_ACCOUNTS_CACHE_SEC": "30",
        "DB_POOL_SIZE": "15",
        "DB_MAX_OVERFLOW": "25",
    }
    for key, val in defaults.items():
        os.environ.setdefault(key, val)


async def _worker_heartbeat() -> None:
    n = 0
    while True:
        await asyncio.sleep(60)
        n += 1
        try:
            from services.incoming_mail_worker import incoming_mail_diag_snapshot

            diag = incoming_mail_diag_snapshot()
        except Exception:
            diag = {}
        logger.info(
            "💓 IMAP worker #%s · mailboxes=%s · due~%s · backoff=%s · max_conc=%s · interval=%ss",
            n,
            diag.get("tracked_mailboxes", "?"),
            diag.get("due_for_poll_approx", "?"),
            len(diag.get("backoff_sec_by_account") or {}),
            diag.get("max_concurrent", "?"),
            diag.get("per_account_interval_sec", "?"),
        )


async def _scheduler_watchdog() -> None:
    """Если цикл планировщика завис — видно в логах."""
    while True:
        await asyncio.sleep(180)
        try:
            from services.incoming_mail_worker import incoming_mail_diag_snapshot

            diag = incoming_mail_diag_snapshot()
            ago = diag.get("scheduler_last_tick_ago_sec")
            if ago is not None and int(ago) > 300:
                logger.error(
                    "IMAP scheduler не тикает %ss — проверь Postgres/прокси или перезапусти imap-worker",
                    ago,
                )
        except Exception:
            logger.exception("IMAP watchdog error")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    _apply_imap_worker_defaults()

    if not _truthy("ENABLE_INCOMING_MAIL"):
        logger.error(
            "ENABLE_INCOMING_MAIL не задан. На сервисе imap-worker в Variables: ENABLE_INCOMING_MAIL=1"
        )
        sys.exit(1)

    if not (config.BOT_TOKEN or "").strip():
        logger.error("BOT_TOKEN пустой — нужен для пересылки писем в Telegram")
        sys.exit(1)

    from database import assert_persistent_database_or_exit, database_url_for_logs, is_persistent_database_url

    assert_persistent_database_or_exit()
    await init_db()
    from database import DATABASE_URL as _db_url, engine as db_engine

    if is_persistent_database_url(_db_url):
        logger.info(
            "IMAP worker: PostgreSQL %s · Telegram polling ВЫКЛ",
            database_url_for_logs(_db_url),
        )
    else:
        logger.warning(
            "IMAP worker: БД %s — для Railway нужен Postgres!",
            db_engine.dialect.name,
        )

    http_timeout = float(os.getenv("TELEGRAM_HTTP_TIMEOUT_SEC", "35"))
    session = AiohttpSession(timeout=http_timeout)
    bot = Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    me = await bot.get_me()
    logger.info("IMAP worker: Bot @%s (id=%s) — только уведомления о письмах", me.username, me.id)

    poll_seconds = int(os.getenv("INCOMING_MAIL_POLL_SECONDS", "120"))
    delay = int(os.getenv("INCOMING_MAIL_START_DELAY_SEC", "10"))
    if delay > 0:
        logger.info("Старт опроса ящиков через %ss", delay)
        await asyncio.sleep(delay)

    from services.incoming_mail_worker import start_incoming_mail_worker

    start_incoming_mail_worker(bot, poll_seconds=poll_seconds)
    asyncio.create_task(_worker_heartbeat())
    asyncio.create_task(_scheduler_watchdog())

    logger.info(
        "IMAP worker running · ~%ss на ящик · MAX_IMAP_CONCURRENT=%s",
        poll_seconds,
        os.getenv("MAX_IMAP_CONCURRENT", "20"),
    )

    try:
        await asyncio.Event().wait()
    finally:
        await bot.session.close()
        logger.info("IMAP worker stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
