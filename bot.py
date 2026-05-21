import asyncio
import importlib
import logging
import os
import pkgutil
import sys
from pathlib import Path
from typing import List, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent
from aiogram.exceptions import TelegramConflictError

from config import config
from database import init_db

logger = logging.getLogger(__name__)

_PID_FILE = Path(__file__).resolve().parent / ".finland_bot.pid"

_ROUTER_BOOT_ORDER: Tuple[str, ...] = (
    "handlers.start",
    "handlers.settings",
    "handlers.send",
    "handlers.stopsend",
)


def _discover_handler_modules(package_name: str = "handlers") -> List[str]:
    pkg = importlib.import_module(package_name)
    module_names: List[str] = [package_name]

    if hasattr(pkg, "__path__"):
        for m in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}."):
            module_names.append(m.name)

    module_names = list(dict.fromkeys(module_names))
    module_names_sorted = sorted(module_names)

    catchall = f"{package_name}.catchall_debug"
    if catchall in module_names_sorted:
        module_names_sorted = [x for x in module_names_sorted if x != catchall] + [catchall]

    return module_names_sorted


def _extract_routers(module, module_name: str) -> List[Router]:
    routers: List[Router] = []

    r = getattr(module, "router", None)
    if isinstance(r, Router):
        logger.info("Подключаю router из %s", module_name)
        routers.append(r)

    rs = getattr(module, "routers", None)
    if isinstance(rs, (list, tuple)):
        for x in rs:
            if isinstance(x, Router):
                logger.info("Подключаю router из %s (routers[])", module_name)
                routers.append(x)

    return routers


def _sort_routers(routers: List[Tuple[str, Router]]) -> List[Tuple[str, Router]]:
    priority = {name: i for i, name in enumerate(_ROUTER_BOOT_ORDER)}
    catchall = "handlers.catchall_debug"

    def key(item: Tuple[str, Router]) -> Tuple[int, str]:
        mod_name, _ = item
        if mod_name == catchall:
            return (10_000, mod_name)
        if mod_name in priority:
            return (priority[mod_name], mod_name)
        return (100, mod_name)

    return sorted(routers, key=key)


def _bind_priority_dispatcher_handlers(dp: Dispatcher) -> None:
    from aiogram.filters import Command
    from aiogram.types import Message

    from handlers.accounts import open_accounts_from_settings, quick_gmail_from_main_menu
    from handlers.api_keys import aqua_show_key, aqua_show_profile
    from handlers.first_sms import firstsms_open
    from handlers.proxies import open_proxies
    from handlers.send import send_cmd
    from handlers.settings import (
        _force_settings_menu,
        match_settings_menu_text,
        open_settings_menu,
        priority_menu,
        ref_hide,
        ref_toggle,
        settings_open_cb,
        settings_timings,
        spoof_name_menu,
    )
    from handlers.stopsend import cmd_stopsend
    from handlers.status import cmd_imap_diag, cmd_statussend
    from handlers.templates import presets_menu

    async def _dp_settings_message(message: Message, state: FSMContext) -> None:
        logger.info("⚙️ settings (dispatcher) tg=%s", message.from_user.id)
        await open_settings_menu(message, state)

    dp.message.register(
        _dp_settings_message,
        F.func(lambda m: match_settings_menu_text(getattr(m, "text", None))),
    )

    dp.message.register(send_cmd, Command("send"))
    dp.message.register(send_cmd, F.text == "▶️ Запустить рассылку")
    dp.message.register(cmd_stopsend, Command("stop", "stopsend"))
    dp.message.register(
        cmd_stopsend,
        F.text.in_({"⏹ Остановить рассылку", "/stop", "/stopsend"}),
    )
    dp.message.register(cmd_statussend, Command("stat", "status", "statussend"))
    dp.message.register(cmd_statussend, F.text == "📊 Статус рассылки")
    dp.message.register(cmd_imap_diag, Command("imap_diag"))
    dp.message.register(
        quick_gmail_from_main_menu,
        F.text.in_({"⚡ Быстрое добавление", "⚡ Быстрое добавление (Gmail)"}),
    )

    _deprecated = frozenset({"settings_menu", "goo:settings", "goo_settings", "settings_main"})
    bindings = (
        (settings_open_cb, F.data == "settings_open"),
        (priority_menu, F.data == "priority_menu"),
        (presets_menu, F.data == "presets_menu"),
        (firstsms_open, F.data == "firstsms_open"),
        (spoof_name_menu, F.data == "spoof_name_menu"),
        (open_accounts_from_settings, F.data == "settings_accounts"),
        (open_proxies, F.data == "settings_proxies"),
        (settings_timings, F.data == "settings_timings"),
        (aqua_show_key, F.data == "aqua_show:key"),
        (aqua_show_profile, F.data == "aqua_show:profile"),
        (ref_hide, F.data == "ref_hide"),
        (_force_settings_menu, F.data.in_(_deprecated)),
        (ref_toggle, F.data.startswith("ref_toggle:")),
    )
    for cb, flt in bindings:
        dp.callback_query.register(cb, flt)

    logger.info(
        "Привязано к Dispatcher: reply-меню + %d callback настроек (AQUA / FI)",
        len(bindings),
    )


def _load_all_routers(package_name: str = "handlers") -> List[Tuple[str, Router]]:
    routers: List[Tuple[str, Router]] = []

    for mod_name in _discover_handler_modules(package_name):
        mod = importlib.import_module(mod_name)
        for r in _extract_routers(mod, mod_name):
            routers.append((mod_name, r))

    unique: List[Tuple[str, Router]] = []
    seen: set[int] = set()
    for name, r in routers:
        if id(r) not in seen:
            unique.append((name, r))
            seen.add(id(r))

    return _sort_routers(unique)


def _acquire_single_instance_lock() -> None:
    try:
        import psutil
    except ImportError:
        logger.warning("psutil не установлен — защита от второго экземпляра отключена")
        return

    my_pid = os.getpid()
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if old_pid and old_pid != my_pid and psutil.pid_exists(old_pid):
            try:
                proc = psutil.Process(old_pid)
                cmd = " ".join(proc.cmdline())
                if "bot.py" in cmd:
                    logger.error(
                        "Уже запущен бот (PID %s). Остановите его или удалите %s",
                        old_pid,
                        _PID_FILE,
                    )
                    sys.exit(1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    _PID_FILE.write_text(str(my_pid), encoding="utf-8")


def _release_single_instance_lock() -> None:
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _log_mailing_env_once() -> None:
    from config import config

    legacy = []
    for name in (
        "MAILING_PLAIN_ONLY",
        "MAILING_MINIMAL_HEADERS",
        "MAILING_STRIP_LINK",
        "MAILING_FIXED_PRESET",
        "SMTP_EHLO_HOSTNAME",
        "MAILING_EHLO_NAME",
    ):
        if (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on"):
            legacy.append(name)
    subj = (getattr(config, "GLOBAL_SUBJECT_TEMPLATE", None) or "OFFER").strip()
    logger.info("Mailing subject template: %r (send path = happy88)", subj)
    if legacy:
        logger.warning(
            "⚠️ Устаревшие Railway Variables (игнорируются кодом, удалите): %s",
            ", ".join(legacy),
        )


async def _on_startup(bot: Bot) -> None:
    from services.bot_commands import register_bot_commands

    _log_mailing_env_once()

    try:
        await register_bot_commands(bot)
    except Exception:
        logger.exception("Не удалось зарегистрировать меню /start /send /stop /stat")

    wh = await bot.get_webhook_info()
    logger.info(
        "Telegram webhook: url=%r pending_updates=%s",
        wh.url or "",
        getattr(wh, "pending_update_count", "?"),
    )
    if wh.url:
        logger.warning("Активен webhook %s — удаляю, нужен polling", wh.url)
        await bot.delete_webhook(drop_pending_updates=False)

    if os.getenv("IMAP_DEDICATED_WORKER", "").strip() in {"1", "true", "yes", "on"}:
        logger.info(
            "IMAP на отдельном сервисе (imap_worker.py). На боте: IMAP_DEDICATED_WORKER=1, "
            "без ENABLE_INCOMING_MAIL"
        )
        return

    if os.getenv("ENABLE_INCOMING_MAIL", "").strip() not in {"1", "true", "yes", "on"}:
        logger.warning(
            "IMAP в bot.py выключен. Отдельный воркер: python imap_worker.py + ENABLE_INCOMING_MAIL=1"
        )
        return

    delay = int(os.getenv("INCOMING_MAIL_START_DELAY_SEC", "90"))
    poll_seconds = int(os.getenv("INCOMING_MAIL_POLL_SECONDS", "30"))

    async def _start_imap_delayed() -> None:
        if delay > 0:
            logger.info("IMAP worker стартует через %ss", delay)
            await asyncio.sleep(delay)
        from services.incoming_mail_worker import start_incoming_mail_worker

        start_incoming_mail_worker(bot, poll_seconds=poll_seconds)
        logger.info("✅ Incoming mail worker стартовал (poll=%ss)", poll_seconds)

    asyncio.create_task(_start_imap_delayed())


async def _polling_heartbeat(bot: Bot) -> None:
    n = 0
    while True:
        await asyncio.sleep(30)
        n += 1
        extra = ""
        try:
            await bot.get_me()
        except TelegramConflictError:
            extra = " | ⚠️ CONFLICT: второй процесс с тем же BOT_TOKEN!"
            logger.critical(extra)
        except Exception as e:
            extra = f" | getMe failed: {e}"
            logger.warning("heartbeat getMe: %s", e)
        logger.info("💓 polling alive #%d%s", n, extra)


async def _on_error(event: ErrorEvent) -> None:
    exc = event.exception
    if isinstance(exc, TelegramConflictError):
        logger.critical("⚠️ TELEGRAM CONFLICT: два процесса на одном BOT_TOKEN")
    logger.exception("Необработанная ошибка апдейта: %s", exc)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    _acquire_single_instance_lock()

    token = (config.BOT_TOKEN or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        logger.error(
            "BOT_TOKEN не задан. Railway → сервис бота → Variables → "
            "BOT_TOKEN = токен от @BotFather (формат 123456789:AA...)"
        )
        sys.exit(1)
    if ":" not in token or len(token) < 30:
        logger.error(
            "BOT_TOKEN невалидный (пустой, обрезанный или с лишними кавычками). "
            "Проверь Variables на Railway — без пробелов и без '...' в значении."
        )
        sys.exit(1)

    http_timeout = float(os.getenv("TELEGRAM_HTTP_TIMEOUT_SEC", "35"))
    session = AiohttpSession(timeout=http_timeout)

    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    from database import assert_persistent_database_or_exit, database_url_for_logs, is_persistent_database_url

    assert_persistent_database_or_exit()
    await init_db()
    from database import DATABASE_URL as _db_url, engine as _db_engine

    if is_persistent_database_url(_db_url):
        logger.info("✅ БД готова (PostgreSQL, persistent) · %s", database_url_for_logs(_db_url))
    else:
        logger.info("✅ БД готова (%s, локально) · Finland / AQUA", _db_engine.dialect.name)

    dp = Dispatcher()
    dp.startup.register(_on_startup)
    dp.errors.register(_on_error)

    from middlewares.bot_access import BotAccessMiddleware
    from middlewares.update_log import CallbackLogMiddleware, MessageLogMiddleware

    dp.message.middleware(MessageLogMiddleware())
    dp.callback_query.middleware(CallbackLogMiddleware())
    dp.message.middleware(BotAccessMiddleware())
    dp.callback_query.middleware(BotAccessMiddleware())

    _bind_priority_dispatcher_handlers(dp)

    for mod_name, r in _load_all_routers("handlers"):
        dp.include_router(r)

    allowed = sorted(
        {
            "message",
            "edited_message",
            "callback_query",
            "my_chat_member",
            "chat_member",
        }
    )

    me = await bot.get_me()
    logger.info("✅ Bot @%s (id=%s) · Finland / AQUA. Polling…", me.username, me.id)

    asyncio.create_task(_polling_heartbeat(bot))

    drop_pending = os.getenv("DROP_PENDING_UPDATES", "").strip() in {"1", "true", "yes"}

    try:
        await bot.delete_webhook(drop_pending_updates=drop_pending)
        wh = await bot.get_webhook_info()
        if wh.url:
            logger.warning("⚠️ Webhook всё ещё установлен: %s — polling может не получать апдейты", wh.url)
        await dp.start_polling(bot, allowed_updates=allowed, drop_pending_updates=drop_pending)
    finally:
        _release_single_instance_lock()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _release_single_instance_lock()
