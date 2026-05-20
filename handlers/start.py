import asyncio
import logging
import os

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import CommandStart

from keyboards.main_menu import main_menu_kb
from database import db_session
from services.users import get_or_create_user
from services.bot_roles import config_admin_ids
from services.bot_access import deny_access_message

router = Router()
logger = logging.getLogger(__name__)

_START_DB_TIMEOUT_SEC = float(os.getenv("START_DB_TIMEOUT_SEC", "12"))

_WELCOME = (
    "👋 Привет! Бот для рассылки (Финляндия, AQUA / Tori / Posti).\n\n"
    "Основные команды:\n"
    "/send — запустить рассылку\n"
    "/stop — остановить рассылку\n"
    "/stat — статус рассылки\n"
    "/imap_diag — входящая почта / IMAP\n\n"
    "Чтобы начать валидацию email — пришли JSON-файл с объявлениями.\n\n"
    "⚙️ Настройки — аккаунты, прокси, API-ключ AQUA, шаблоны."
)


async def _start_load_user(tg_id: int) -> tuple[bool, bool, bool]:
    """(is_banned, is_admin, has_access) — один round-trip к БД."""
    async with db_session() as session:
        user = await get_or_create_user(session, tg_id)
        if getattr(user, "is_banned", False):
            return True, False, False
        is_admin = bool(getattr(user, "is_admin", False))
        if is_admin and not bool(getattr(user, "access_granted", False)):
            user.access_granted = True
            await session.commit()
        has_access = is_admin or bool(getattr(user, "access_granted", False))
        return False, is_admin, has_access


@router.message(CommandStart())
@router.message(F.text.in_({"/ping", "/health"}))
async def cmd_start(message: Message) -> None:
    tg_id = int(message.from_user.id)
    text = (message.text or "").strip().lower()
    if text in ("/ping", "/health"):
        await message.answer("🏓 pong — бот на связи.")
        return

    logger.info("▶ HANDLER /start tg=%s", tg_id)

    try:
        await message.answer("⏳ Загружаю меню…")
    except Exception:
        logger.exception("/start: не удалось отправить первый ответ tg=%s", tg_id)
        return

    if tg_id in config_admin_ids():
        await message.answer(_WELCOME, reply_markup=main_menu_kb(tg_id, show_admin=True))
        return

    try:
        is_banned, is_admin, has_access = await asyncio.wait_for(
            _start_load_user(tg_id),
            timeout=_START_DB_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error("/start DB timeout tg=%s", tg_id)
        await message.answer(
            "⏳ База данных не отвечает. Подожди 15 сек и снова /start.\n"
            "<i>Если повторяется — проверь Postgres на Railway.</i>",
            parse_mode="HTML",
        )
        return
    except Exception:
        logger.exception("/start failed tg=%s", tg_id)
        await message.answer("❌ Ошибка БД. Попробуй /start через 10 сек.")
        return

    if is_banned:
        await message.answer(
            "⛔ Вы заблокированы администратором.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not has_access:
        await deny_access_message(message)
        return

    await message.answer(_WELCOME, reply_markup=main_menu_kb(tg_id, show_admin=is_admin))
