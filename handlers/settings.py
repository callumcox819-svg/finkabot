# handlers/settings.py
from __future__ import annotations

import asyncio
import json
import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest

from database import Session, db_session
from services.users import get_or_create_user
from services.user_settings import get_user_setting, set_user_setting
from keyboards.main_menu import main_menu_kb
from utils.callback_safe import callback_answer_safe
from services.aqua_keys import (
    AQUA_SERVICE_KEY,
    aqua_service_for_html_dir,
    aqua_service_label,
    get_user_aqua_service,
)
from config import config

class SpoofNameState(StatesGroup):
    waiting_name = State()

router = Router()

# =========================
# Утилиты
# =========================

async def _safe_send(target, *args, **kwargs):
    """Safely await a coroutine OR call an async function with args/kwargs."""
    try:
        coro = target(*args, **kwargs) if callable(target) else target
        return await coro
    except TelegramBadRequest:
        return None
    except Exception:
        logger.exception("_safe_send")
        return None


async def _cq_edit_text(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> None:
    """Правка inline-меню: через bot.edit_message_text (не message.edit_text через _safe_send)."""
    msg = callback.message
    if msg is None:
        return
    try:
        await callback.bot.edit_message_text(
            text,
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest:
        pass


logger = logging.getLogger(__name__)

SETTINGS_MENU_TEXT = "Настройки"


def match_settings_menu_text(text: str | None) -> bool:
    """Кнопка «⚙️ Настройки» с главной клавиатуры (устойчиво к вариантам emoji)."""
    t = (text or "").strip().casefold().replace("\ufe0f", "")
    if not t:
        return False
    if "настройки" in t:
        return True
    return t in {"settings", "setting", "⚙️ настройки"}


async def open_settings_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    tg_id = int(message.from_user.id)
    logger.info("open_settings_menu tg=%s text=%r", tg_id, message.text)
    try:
        await message.answer("⏳ Открываю настройки…")
    except Exception:
        pass
    try:
        kb = await asyncio.wait_for(
            _settings_menu_kb_for_user(tg_id),
            timeout=float(__import__("os").getenv("SETTINGS_MENU_DB_TIMEOUT_SEC", "12")),
        )
        await message.answer(
            SETTINGS_MENU_TEXT,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except asyncio.TimeoutError:
        logger.error("open_settings_menu DB timeout tg=%s", tg_id)
        await message.answer(
            SETTINGS_MENU_TEXT,
            reply_markup=settings_menu_kb({}),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("open_settings_menu failed tg=%s", tg_id)
        await message.answer(
            "❌ Не удалось открыть настройки (ошибка БД). Попробуйте через 5 сек или /start.",
            parse_mode="HTML",
        )


# =========================
# HTML Nick
# =========================

SUBJECT_TEMPLATE_KEY = "subject_template"
HTML_THEME_KEY = "html_theme"
PROXY_ROTATION_KEY = "proxy_rotation"

HTMLNICK_KEY = "html_nick"
COUNTRY_KEY = "country"
TEAM_KEY = "team"

async def load_html_nick(session: Session, tg_user_id: int) -> str | None:
    user = await get_or_create_user(session, tg_user_id)
    val = await get_user_setting(session, user, HTMLNICK_KEY)
    return (val or "").strip() or None

async def save_html_nick(session: Session, tg_user_id: int, value: str | None) -> None:
    user = await get_or_create_user(session, tg_user_id)
    v = (value or "").strip() or None
    await set_user_setting(session, user, HTMLNICK_KEY, v)


# =========================
# FSM for simple inputs (nick, timings)
# =========================


class _SettingsInput(StatesGroup):
    html_nick = State()
    subject_template = State()
    priority = State()
    html_theme = State()
    timings = State()


def settings_menu_kb(flags: dict[str, bool]) -> InlineKeyboardMarkup:
    """Главное меню настроек: Финляндия + AQUA (без goo_*, тем, fast, html_mailer)."""
    def dot(on: bool, label: str) -> str:
        return ("🟢 " if on else "🔴 ") + label

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Приоритет\nотправки", callback_data="priority_menu"),
                InlineKeyboardButton(text="🧾 Пресеты", callback_data="presets_menu"),
            ],
            [
                InlineKeyboardButton(text=dot(flags.get("smart_mode", False), "Умный режим"), callback_data="ref_toggle:smart_mode"),
                InlineKeyboardButton(text="📄 Умные пресеты", callback_data="smart_presets_menu"),
            ],
            [
                InlineKeyboardButton(text=dot(flags.get("spoofing", False), "Спуфинг"), callback_data="ref_toggle:spoofing"),
                InlineKeyboardButton(text="👤 Имя для\nспуфинга", callback_data="spoof_name_menu"),
            ],
            [
                InlineKeyboardButton(text=dot(flags.get("block_control", False), "Контроль\nблокировок"), callback_data="ref_toggle:block_control"),
            ],
            [
                InlineKeyboardButton(text="📧 E-mail", callback_data="settings_accounts"),
                InlineKeyboardButton(text="🌐 Прокси", callback_data="settings_proxies"),
            ],
            [
                InlineKeyboardButton(text="🧮 Интервал", callback_data="settings_timings"),
            ],
            [
                InlineKeyboardButton(text=dot(flags.get("proxy_rotation", False), "Ротация"), callback_data="ref_toggle:proxy_rotation"),
                InlineKeyboardButton(text="🔑 Ключ", callback_data="aqua_show:key"),
            ],
            [
                InlineKeyboardButton(text="🧾 Профиль", callback_data="aqua_show:profile"),
                InlineKeyboardButton(text="🍀 Скрыть", callback_data="ref_hide"),
            ],
        ]
    )


async def _settings_menu_kb_for_user(tg_user_id: int) -> InlineKeyboardMarkup:
    """Return settings menu keyboard with current toggle states (per-user)."""
    async with db_session() as session:
        user = await get_or_create_user(session, tg_user_id)

        async def _b(key: str, default: bool = False) -> bool:
            v = await get_user_setting(session, user, key)
            if v is None:
                return default
            s = str(v).strip().lower()
            return s in {"1", "true", "yes", "on", "y"}

        flags = {
            "smart_mode": await _b("smart_mode", False),
            "spoofing": await _b("spoofing", False),
            "block_control": await _b("block_control", False),
            "proxy_rotation": await _b("proxy_rotation", True),
        }

    return settings_menu_kb(flags)


def _is_settings_menu_message(message: Message) -> bool:
    return match_settings_menu_text(message.text)


@router.message(F.func(_is_settings_menu_message))
async def settings_open(message: Message, state: FSMContext) -> None:
    await open_settings_menu(message, state)


async def _spoof_name_menu_payload(tg_user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Текст и клавиатура меню HTML-имени. None — сервис не выбран."""
    async with db_session() as session:
        user = await get_or_create_user(session, tg_user_id)
        service = await get_user_aqua_service(session, user)
        if not service:
            return None
        key = _html_nick_key_for_service(service)
        cur = (await get_user_setting(session, user, key) or "").strip()
        html_subj = (await get_user_setting(session, user, HTML_THEME_KEY) or "").strip() or "— не задано —"

    label = _service_label(service)
    cur_line = cur if cur else "— не задано —"
    text = (
        f"👤 <b>HTML: имя и тема</b>\n"
        f"Сервис: <b>{label}</b>\n"
        f"Имя отправителя (при 🟢 Спуфинг): <b>{cur_line}</b>\n\n"
        f"Используется только при отправке <b>HTML</b>.\n"
        f"Рассылка — отдельно: имя из «📧 E-mail», тема <code>OFFER</code>.\n\n"
        f"📌 <b>Тема для HTML:</b> <code>{html_subj}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Установить имя ({label})", callback_data="spoof_name_set")],
            [InlineKeyboardButton(text="📌 Тема для HTML", callback_data="html_theme_menu")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
        ]
    )
    return text, kb


async def _show_spoof_name_menu_message(message: Message, *, prompt_chat_id: int | None, prompt_msg_id: int | None) -> None:
    payload = await _spoof_name_menu_payload(message.from_user.id)
    if not payload:
        await message.answer("Сначала выберите сервис в 👤 Профиль → 🧭 Выбор сервиса.")
        return
    text, kb = payload
    if prompt_chat_id and prompt_msg_id:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=prompt_chat_id,
                message_id=prompt_msg_id,
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "spoof_name_menu")
async def spoof_name_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Меню установки имени (смены ника) для HTML, привязанное к выбранному сервису профиля."""
    await state.clear()
    payload = await _spoof_name_menu_payload(callback.from_user.id)
    if not payload:
        return await callback.answer("Сначала выберите сервис в профиле", show_alert=True)
    text, kb = payload
    await _cq_edit_text(callback, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "spoof_name_set")
async def spoof_name_set(callback: CallbackQuery, state: FSMContext) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        service = (await get_user_setting(session, user, AQUA_SERVICE_KEY) or "").strip()
        if not service:
            return await callback.answer("Сначала выберите сервис в профиле", show_alert=True)
    await state.set_state(SpoofNameState.waiting_name)
    await state.update_data(
        service=service,
        spoof_prompt_chat_id=callback.message.chat.id if callback.message else None,
        spoof_prompt_msg_id=callback.message.message_id if callback.message else None,
    )
    await _cq_edit_text(
        callback,
        "Введите имя для спуфинга (смены ника) для HTML:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚫 Отмена", callback_data="spoof_name_menu")]]
        ),
    )
    await callback.answer()


@router.message(SpoofNameState.waiting_name)
async def spoof_name_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service = (data.get("service") or "").strip()
    name = (message.text or "").strip()
    if not name:
        return await message.answer("Введите имя текстом.")
    async with db_session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        key = _html_nick_key_for_service(service)
        await set_user_setting(session, user, key, name)

    prompt_chat_id = data.get("spoof_prompt_chat_id")
    prompt_msg_id = data.get("spoof_prompt_msg_id")
    await state.clear()

    await message.answer("✅ Имя добавлено")
    await _show_spoof_name_menu_message(
        message,
        prompt_chat_id=int(prompt_chat_id) if prompt_chat_id else None,
        prompt_msg_id=int(prompt_msg_id) if prompt_msg_id else None,
    )


@router.callback_query(F.data == "settings_open")
async def settings_open_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_answer_safe(callback)
    try:
        kb = await asyncio.wait_for(
            _settings_menu_kb_for_user(callback.from_user.id),
            timeout=float(__import__("os").getenv("SETTINGS_MENU_DB_TIMEOUT_SEC", "12")),
        )
    except asyncio.TimeoutError:
        logger.error("settings_open_cb DB timeout tg=%s", callback.from_user.id)
        kb = settings_menu_kb({})
    except Exception:
        logger.exception("settings_open_cb failed tg=%s", callback.from_user.id)
        kb = settings_menu_kb({})
    await _cq_edit_text(callback, "Настройки", reply_markup=kb)


# =========================
# Missing callbacks from settings menu (Domains / Sender name / Templates / Timings / HTML nick)
# =========================


@router.callback_query(F.data == "settings_domains")
async def settings_domains(callback: CallbackQuery) -> None:
    """Open domains menu. We don't touch domains logic, only show its existing inline menu."""
    from handlers.domains import domains_menu_kb

    await callback.message.edit_text(
        "🌐 <b>Управление доменами</b>\n\nВыбери действие:",
        reply_markup=domains_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sender_name_menu")
async def sender_name_menu(callback: CallbackQuery) -> None:
    """Show current sender name and provide existing 'sender_name_set' action."""
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        current = (getattr(user, "sender_name", None) or "—").strip() or "—"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Установить", callback_data="sender_name_set")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
        ]
    )
    await callback.message.edit_text(
        "📝 <b>Имя отправителя</b>\n\n"
        f"Текущее имя: <code>{current}</code>\n\n"
        "Нажми «Установить», чтобы задать другое.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_templates")
async def settings_templates(callback: CallbackQuery, state: FSMContext) -> None:
    """Open presets list (same UI as умные пресеты)."""
    from handlers.templates import presets_menu

    await presets_menu(callback, state)


@router.callback_query(F.data == "html_nick_menu")
async def html_nick_menu(callback: CallbackQuery, state: FSMContext) -> None:
    async with Session() as session:
        cur = await load_html_nick(session, callback.from_user.id)

    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Установить", callback_data="html_nick_set")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
        ]
    )

    await callback.message.edit_text(
        "📝 <b>Смена ника</b>\n\n"
        f"Текущий ник: <code>{cur or '—'}</code>\n\n"
        "Нажми «Установить», чтобы задать другой.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "html_nick_set")
async def html_nick_set_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Ask user to send a new nick (HTML nick) after pressing 'Установить'."""
    await state.clear()
    await state.set_state(_SettingsInput.html_nick)

    await callback.message.edit_text(
        "📝 <b>Смена ника</b>\n\n"
        "Отправь новый ник одним сообщением (или «-», чтобы очистить).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(_SettingsInput.html_nick)
async def html_nick_set(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Пустое значение. Отправь ещё раз.")
        return
    if value == "-":
        value = ""
    async with Session() as session:
        await save_html_nick(session, message.from_user.id, value)
    await state.clear()
    await message.answer("✅ Сохранено.")


# =========================
# Timings (ONLY UI change: menu + "Изменить тайминг" button)
# =========================

@router.callback_query(F.data == "settings_timings")
async def settings_timings(callback: CallbackQuery, state: FSMContext) -> None:
    """Show timings menu (no immediate input)."""
    from services.settings import load_timing

    await state.clear()

    async with db_session() as session:
        timing = await load_timing(session, callback.from_user.id)

    await callback.message.edit_text(
        "⏱ <b>Тайминги рассылки</b>\n\n"
        "Текущий диапазон:\n"
        f"MIN: <code>{timing.get('min')}</code> сек\n"
        f"MAX: <code>{timing.get('max')}</code> сек\n\n"
        " ",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Изменить тайминг", callback_data="timings_edit")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            ]
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "timings_edit")
async def timings_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Ask user to input MIN MAX after pressing 'Изменить тайминг'."""
    await state.clear()
    await state.set_state(_SettingsInput.timings)

    await callback.message.edit_text(
        "⏱ <b>Тайминги рассылки</b>\n\n"
        "Отправь двумя числами: <code>MIN MAX</code> (пример: <code>1 5</code>).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_timings")],
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()




@router.message(_SettingsInput.timings)
async def timings_set(message: Message, state: FSMContext) -> None:
    from services.settings import load_timing, save_timing

    text = (message.text or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$", text)
    if not m:
        await message.answer("❌ Формат: MIN MAX (например: 1 5)")
        return
    mn = float(m.group(1))
    mx = float(m.group(2))
    if mn <= 0 or mx <= 0 or mx < mn:
        await message.answer("❌ Неверные значения. Нужно: 0 < MIN <= MAX")
        return

    async with Session() as session:
        cur = await load_timing(session, message.from_user.id)
        cur["min"] = int(mn) if mn.is_integer() else mn
        cur["max"] = int(mx) if mx.is_integer() else mx
        cur["min_delay"] = mn
        cur["max_delay"] = mx
        await save_timing(session, message.from_user.id, cur)

    await state.clear()
    await message.answer("✅ Сохранено.")


@router.callback_query(F.data == "settings_back")
async def settings_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text(
            "Настройки",
            reply_markup=await _settings_menu_kb_for_user(callback.from_user.id),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


def _service_label(code: str) -> str:
    return aqua_service_label(code)


def _html_nick_key_for_service(service: str) -> str:
    sub = aqua_service_for_html_dir((service or "").strip() or None)
    return f"html_nick_{sub}" if sub else HTMLNICK_KEY


# =========================
# Reference menu toggles / stubs (1v1 UI)
# =========================

_REF_TOGGLE_KEYS = {
    "check_send": "check_send",
    "subj_insert": "subj_insert",
    "smart_mode": "smart_mode",
    "spoofing": "spoofing",
    "html_mailer": "html_mailer",
    "saver": "saver",
    "card": "card",
    "block_control": "block_control",
    "proxy_rotation": "proxy_rotation",
}


def _simple_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]])


@router.callback_query(F.data.startswith("ref_toggle:"))
async def ref_toggle(callback: CallbackQuery):
    key = (callback.data or "").split(":", 1)[1].strip()
    db_key = _REF_TOGGLE_KEYS.get(key)
    if not db_key:
        return await callback.answer()

    async with db_session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        cur = await get_user_setting(session, user, db_key)
        cur_s = str(cur or "").strip().lower()
        cur_b = cur_s in {"1", "true", "yes", "on", "y"}
        new_b = not cur_b
        await set_user_setting(session, user, db_key, "1" if new_b else "0")

    kb = await _settings_menu_kb_for_user(callback.from_user.id)
    await _cq_edit_text(callback, "Настройки", reply_markup=kb)


@router.callback_query(F.data == "ref_hide")
async def ref_hide(callback: CallbackQuery):
    # Try delete message, else just remove buttons
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("ref_open:"))
async def ref_open(callback: CallbackQuery, state: FSMContext):
    """Helper screens for reference menu items that are not full modules in this repo."""
    screen = (callback.data or "").split(":", 1)[1].strip()
    if screen == "commands":
        await state.clear()
        msg = (
            "⌨️ <b>Команды</b>\n\n"
            "Страна: <b>Финляндия (FI)</b>\n"
            "Команда: <b>AQUA</b>\n\n"
            "/send — запустить рассылку\n"
            "/stop — остановить рассылку\n"
            "/stat — статус рассылки"
        )
        await callback.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return
    if screen in {"themes", "themes_html"}:
        await state.clear()
        title = "📌 <b>Темы</b>" if screen == "themes" else "🏷 <b>Тема для HTML</b>"
        text = (
            f"{title}\n\n"
            "В этом проекте темы/шаблоны управляются через «🧾 Пресеты».\n"
            "Если нужно — добавлю отдельный менеджер тем 1в1 (лист/добавить/удалить/выбрать)."
        )
        await callback.message.edit_text(text, reply_markup=_simple_back_kb(), parse_mode="HTML")
        await callback.answer()
        return

    if screen == "smart_presets":
        await callback.answer()
        from handlers.templates import smart_presets_menu

        await smart_presets_menu(callback, state)
        return

    if screen in {"cases", "scenario_name", "rotation"}:
        await state.clear()
        labels = {
            "cases": "🟢 <b>Сценарии</b>",
            "scenario_name": "🧾 <b>Имя для сценариев</b>",
            "rotation": "🔄 <b>Ротация</b>",
        }
        text = (
            f"{labels.get(screen, 'ℹ️')}\n\n"
            "Этот раздел в твоём проекте пока не был реализован как отдельный экран.\n"
            "Если хочешь 1в1 — напиши, какие именно действия там должны быть (по видео), и я добавлю."
        )
        await callback.message.edit_text(text, reply_markup=_simple_back_kb(), parse_mode="HTML")
        await callback.answer()
        return

    # unknown
    await callback.answer("OK")


# =========================
# Команды (как на видео) — просто экран с командами + Назад
# =========================

@router.callback_query(F.data == "ref_open:commands")
async def ref_open_commands(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "⌨️ <b>Команды</b>\n\n"
        "/send — запустить рассылку\n"
        "/stop — остановить рассылку\n"
        "/stat — статус рассылки\n\n"
        "Также: просто пришли JSON/TXT с объявлениями — бот провалидирует и сохранит в БД."
    )
    await _safe_send(callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]),
        parse_mode="HTML",
    ))
    await callback.answer()

# =========================
# Темы (OFFER)
# =========================

@router.callback_query(F.data == "themes_menu")
async def themes_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        cur = (await get_user_setting(session, user, SUBJECT_TEMPLATE_KEY) or "").strip()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="themes_edit")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="themes_clear")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
    ])
    cur_show = cur if cur else "—"
    txt = (
        "📌 <b>Темы</b>\n\n"
        "Шаблон темы письма (поддерживает <code>OFFER</code>):\n"
        f"<code>{cur_show}</code>\n\n"
        "Пример: <code>OFFER | Antwort</code>"
    )
    await _safe_send(callback.message.edit_text(txt, reply_markup=kb, parse_mode="HTML"))
    await callback.answer()

@router.callback_query(F.data == "themes_edit")
async def themes_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(_SettingsInput.subject_template)
    await _safe_send(callback.message.edit_text(
        "📌 <b>Темы</b>\n\n"
        "Отправь шаблон темы. Используй <code>OFFER</code>.\n"
        "Чтобы удалить — отправь <code>-</code>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="themes_menu")]]),
        parse_mode="HTML",
    ))
    await callback.answer()

@router.message(_SettingsInput.subject_template)
async def themes_set(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    if val == "-":
        val = ""
    async with Session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        await set_user_setting(session, user, SUBJECT_TEMPLATE_KEY, val)
    await state.clear()

    # Показываем экран "Темы" сразу после сохранения, чтобы было видно — установлено или нет.
    cur_show = val if val else "—"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="themes_edit")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="themes_clear")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
    ])
    txt = (
        "📌 <b>Темы</b>\n\n"
        "Шаблон темы письма (поддерживает <code>OFFER</code>):\n"
        f"<code>{cur_show}</code>\n\n"
        "Пример: <code>OFFER | Antwort</code>"
    )
    await message.answer(txt, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "themes_clear")
async def themes_clear(callback: CallbackQuery, state: FSMContext):
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        await set_user_setting(session, user, SUBJECT_TEMPLATE_KEY, "")
    await callback.answer("Очищено ✅")
    await themes_menu(callback, state)

# =========================
# Тема для HTML (реально сохраняем html_theme)
# =========================

@router.callback_query(F.data == "html_theme_menu")
async def html_theme_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        cur = (await get_user_setting(session, user, HTML_THEME_KEY) or "").strip()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="html_theme_edit")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="html_theme_clear")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="spoof_name_menu")],
    ])
    cur_show = cur if cur else "—"
    txt = (
        "📌 <b>Тема для HTML</b>\n\n"
        "Используется только при отправке <b>HTML</b> (не при массовой рассылке).\n"
        "Рассылка использует глобальный <code>OFFER</code> → название товара.\n\n"
        f"Текущее значение:\n<code>{cur_show}</code>"
    )
    await _safe_send(callback.message.edit_text(txt, reply_markup=kb, parse_mode="HTML"))
    await callback.answer()

@router.callback_query(F.data == "html_theme_edit")
async def html_theme_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(_SettingsInput.html_theme)
    await _safe_send(callback.message.edit_text(
        "🧾 <b>Тема для HTML</b>\n\nОтправь тему одной строкой.\n"
        "Чтобы удалить — отправь <code>-</code>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="spoof_name_menu")]]),
        parse_mode="HTML",
    ))
    await callback.answer()

@router.message(_SettingsInput.html_theme)
async def html_theme_set(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    if val == "-":
        val = ""
    async with Session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        await set_user_setting(session, user, HTML_THEME_KEY, val)
    await state.clear()
    await message.answer(
        "✅ Тема для HTML сохранена.\nПример: <code>Your item sold</code>",
        reply_markup=await _settings_menu_kb_for_user(message.from_user.id),
    )

@router.callback_query(F.data == "html_theme_clear")
async def html_theme_clear(callback: CallbackQuery, state: FSMContext):
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        await set_user_setting(session, user, HTML_THEME_KEY, "")
    await callback.answer("Очищено ✅")
    await html_theme_menu(callback, state)

# =========================
# Ротация прокси — экран + переключатель proxy_rotation (без "0 действий")
# =========================

@router.callback_query(F.data == "proxy_rotation_menu")
async def proxy_rotation_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        v = await get_user_setting(session, user, PROXY_ROTATION_KEY)
        cur = str(v or "").strip().lower() in {"1","true","yes","on"}
    status = "✅" if cur else "❌"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔄 Ротация: {status}", callback_data="ref_toggle:proxy_rotation")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
    ])
    await _safe_send(callback.message.edit_text(
        "🔄 <b>Ротация</b>\n\n"
        "ВКЛ — прокси меняются между отправками.\n"
        "ВЫКЛ — один прокси.",
        reply_markup=kb,
        parse_mode="HTML",
    ))
    await callback.answer()

# =========================
# Приоритет доменов — сохраняем список доменов по порядку
# =========================

DOMAIN_PRIORITY_KEY = "domain_priority"

@router.callback_query(F.data == "priority_menu")
async def priority_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with db_session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        raw = await get_user_setting(session, user, DOMAIN_PRIORITY_KEY)
        try:
            items = json.loads(raw) if raw else []
        except Exception:
            items = []
    if not isinstance(items, list):
        items = []

    if items:
        lst = "\n".join([f"{i+1}. <code>{d}</code>" for i, d in enumerate(items)])
    else:
        lst = "—"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить приоритет", callback_data="priority_edit")],
        [InlineKeyboardButton(text="🗑 Сбросить приоритет", callback_data="priority_reset")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
    ])
    await _safe_send(callback.message.edit_text(
        "📊 <b>Приоритет отправки</b>\n\n"
        "Домен №1 валидируется первым, потом №2 и т.д.\n\n"
        f"<b>Текущий приоритет:</b>\n{lst}",
        reply_markup=kb,
        parse_mode="HTML",
    ))

@router.callback_query(F.data == "priority_edit")
async def priority_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(_SettingsInput.priority)
    await _safe_send(callback.message.edit_text(
        "📊 <b>Приоритет отправки</b>\n\n"
        "Отправь домены списком (каждый с новой строки).\n"
        "Пример:\n<code>gmx.de\ngmail.com\n...</code>\n\n"
        "Чтобы очистить — отправь <code>-</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="priority_menu")]]),
        parse_mode="HTML",
    ))
    await callback.answer()

@router.message(_SettingsInput.priority)
async def priority_set(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt == "-":
        items = []
    else:
        items = [re.sub(r"^https?://", "", x.strip().lower()) for x in txt.splitlines() if x.strip()]
    async with Session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        await set_user_setting(session, user, DOMAIN_PRIORITY_KEY, json.dumps(items))
    await state.clear()
    await message.answer("✅ Сохранено.", reply_markup=await _settings_menu_kb_for_user(message.from_user.id))

@router.callback_query(F.data == "priority_reset")
async def priority_reset(callback: CallbackQuery, state: FSMContext):
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        await set_user_setting(session, user, DOMAIN_PRIORITY_KEY, json.dumps([]))
    await callback.answer("Сброшено ✅")
    await priority_menu(callback, state)

# =========================
# Ловим любые старые "назад" из старого меню, чтобы оно больше не всплывало
# =========================

@router.callback_query(F.data.in_({"settings_menu", "goo:settings", "goo_settings", "settings_main"}))
async def _force_settings_menu(callback: CallbackQuery, state: FSMContext):
    await settings_open_cb(callback, state)
