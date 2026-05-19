"""AQUA (GOO NETWORK) — ключи и профиль · Финляндия."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest

from database import Session
from config import config
from services.users import get_or_create_user
from services.aqua_keys import (
    AQUA_SERVICE_KEY,
    AQUA_TEAM_API_KEY_SETTING,
    AQUA_USER_API_KEY_SETTING,
    aqua_service_label,
    get_user_aqua_api_keys_async,
    get_user_aqua_service,
    get_user_goo_profile_id,
    normalize_aqua_service,
)
from services.user_settings import get_user_setting, set_user_setting
from utils.secrets import clean_secret

router = Router(name="api_keys")

AQUA_PROFILE_TITLE_KEY = "aqua_profile_title"
AQUA_PROFILE_NAME_KEY = "aqua_profile_name"
AQUA_PROFILE_ADDRESS_KEY = "aqua_profile_address"


class KeysState(StatesGroup):
    waiting_value = State()


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]
    )


def profile_screen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Заметки профиля", callback_data="aqua_profile_create")],
            [InlineKeyboardButton(text="🆔 Profile ID (GOO)", callback_data="aqua_set:profile_id")],
            [InlineKeyboardButton(text="🧭 Сервис", callback_data="aqua_service_menu")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            [InlineKeyboardButton(text="🟢 Скрыть", callback_data="aqua_hide")],
        ]
    )


def key_screen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 User API key", callback_data="aqua_set:user_key")],
            [InlineKeyboardButton(text="🛠 Team API key", callback_data="aqua_set:team_key")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            [InlineKeyboardButton(text="🟢 Скрыть", callback_data="aqua_hide")],
        ]
    )


def _show_full(key: str | None) -> str:
    return (key or "—").strip() or "—"


async def _render_profile_screen(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        prof_title = (await get_user_setting(session, user, AQUA_PROFILE_TITLE_KEY) or "—").strip() or "—"
        prof_name = (await get_user_setting(session, user, AQUA_PROFILE_NAME_KEY) or "—").strip() or "—"
        prof_addr = (await get_user_setting(session, user, AQUA_PROFILE_ADDRESS_KEY) or "—").strip() or "—"
        service_raw = await get_user_aqua_service(session, user)
        service = aqua_service_label(service_raw) if service_raw else "—"
        profile_id = get_user_goo_profile_id(user) or "—"
        text = (
            "👤 <b>Профиль AQUA</b>\n\n"
            f"Заметка — название: <code>{prof_title}</code>\n"
            f"Заметка — имя: <code>{prof_name}</code>\n"
            f"Заметка — адрес: <code>{prof_addr}</code>\n"
            f"Profile ID (GOO): <code>{profile_id}</code>\n"
            f"Сервис: <b>{service}</b>"
            + (f" (<code>{service_raw}</code>)" if service_raw else "")
            + f"\n\n🇫🇮 {config.COUNTRY_LABEL} · команда <b>AQUA</b>\n"
        )
    try:
        await callback.message.edit_text(text, reply_markup=profile_screen_kb(), parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _render_key_screen(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        user_key, team_key = await get_user_aqua_api_keys_async(session, user)
    text = (
        "🔑 <b>Ключи AQUA</b> (api.goo.network)\n\n"
        f"User API key: {'✅' if user_key else '❌'}\n<code>{_show_full(user_key)}</code>\n\n"
        f"Team API key: {'✅' if team_key else '❌'}\n<code>{_show_full(team_key)}</code>\n\n"
        f"🇫🇮 {config.COUNTRY_LABEL} · команда <b>AQUA</b>"
    )
    await callback.message.edit_text(text, reply_markup=key_screen_kb(), parse_mode="HTML")


@router.callback_query(F.data == "aqua_hide")
async def aqua_hide(callback: CallbackQuery) -> None:
    await callback.message.edit_text("✅ Скрыто.")
    await callback.answer()


@router.callback_query(F.data == "aqua_show:profile")
async def aqua_show_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_profile_screen(callback)


@router.callback_query(F.data == "aqua_show:key")
async def aqua_show_key(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_key_screen(callback)


@router.callback_query(F.data == "aqua_set:user_key")
async def aqua_set_user_key_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeysState.waiting_value)
    await state.update_data(field="aqua_user_key")
    await callback.message.edit_text(
        "✍️ User API key (заголовок <code>Authorization: Apikey …</code>)",
        reply_markup=_back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "aqua_set:team_key")
async def aqua_set_team_key_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeysState.waiting_value)
    await state.update_data(field="aqua_team_key")
    await callback.message.edit_text(
        "✍️ Team API key (заголовок <code>X-Team-Key</code>)",
        reply_markup=_back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "aqua_set:profile_id")
async def aqua_set_profile_id_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeysState.waiting_value)
    await state.update_data(field="goo_profile_id")
    await callback.message.edit_text(
        "✍️ <b>profileID</b> из GOO (Мой профиль → Профили…)",
        reply_markup=_back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(KeysState.waiting_value)
async def keys_set_finish(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("field")
    value = clean_secret((message.text or "").strip())
    if not value:
        await message.answer("❌ Пустое значение.")
        return

    async with Session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        if field == "aqua_user_key":
            user.goo_user_api_key_aqua = value
            await set_user_setting(session, user, AQUA_USER_API_KEY_SETTING, value)
        elif field == "aqua_team_key":
            user.goo_team_api_key_aqua = value
            await set_user_setting(session, user, AQUA_TEAM_API_KEY_SETTING, value)
        elif field == "goo_profile_id":
            user.goo_profile_id = value
        await session.commit()

    await state.clear()
    await message.answer("✅ Сохранено.")


class AquaProfileState(StatesGroup):
    title = State()
    name = State()
    address = State()


@router.callback_query(F.data == "aqua_profile_create")
async def aqua_profile_create(callback: CallbackQuery, state: FSMContext) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        cur_title = (await get_user_setting(session, user, AQUA_PROFILE_TITLE_KEY) or "—").strip() or "—"
        cur_name = (await get_user_setting(session, user, AQUA_PROFILE_NAME_KEY) or "—").strip() or "—"
        cur_addr = (await get_user_setting(session, user, AQUA_PROFILE_ADDRESS_KEY) or "—").strip() or "—"

    await state.clear()
    await state.set_state(AquaProfileState.title)
    await callback.message.edit_text(
        "➕ <b>Заметки профиля</b> (для себя, не уходит в API)\n\n"
        f"Сейчас: {cur_title} / {cur_name} / {cur_addr}",
        parse_mode="HTML",
        reply_markup=_back_kb(),
    )
    await callback.answer()


@router.message(AquaProfileState.title)
async def aqua_profile_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ Пусто.")
        return
    await state.update_data(title=title)
    await state.set_state(AquaProfileState.name)
    await message.answer("Имя (заметка):")


@router.message(AquaProfileState.name)
async def aqua_profile_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Пусто.")
        return
    await state.update_data(name=name)
    await state.set_state(AquaProfileState.address)
    await message.answer("Адрес (заметка):")


@router.message(AquaProfileState.address)
async def aqua_profile_address(message: Message, state: FSMContext) -> None:
    addr = (message.text or "").strip()
    if not addr:
        await message.answer("❌ Пусто.")
        return
    data = await state.get_data()
    async with Session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        await set_user_setting(session, user, AQUA_PROFILE_TITLE_KEY, (data.get("title") or "").strip())
        await set_user_setting(session, user, AQUA_PROFILE_NAME_KEY, (data.get("name") or "").strip())
        await set_user_setting(session, user, AQUA_PROFILE_ADDRESS_KEY, addr)
    await state.clear()
    await message.answer("✅ Сохранено.")


@router.callback_query(F.data == "aqua_service_menu")
async def aqua_service_menu(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        cur = await get_user_aqua_service(session, user)

    from services.aqua_keys import aqua_service_matches

    def mark(service: str, label: str) -> str:
        return ("🟩 " if aqua_service_matches(cur, service) else "⬜️ ") + label

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=mark("tori_fi", "Tori.fi"), callback_data="aqua_service_set:tori_fi")],
            [InlineKeyboardButton(text=mark("posti_fi", "Posti.fi"), callback_data="aqua_service_set:posti_fi")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aqua_show:profile")],
        ]
    )
    await callback.message.edit_text(
        "🧭 <b>Сервис AQUA</b> (Финляндия)\n\n"
        "<code>tori_fi</code> · <code>posti_fi</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aqua_service_set:"))
async def aqua_service_set(callback: CallbackQuery) -> None:
    try:
        _, service = (callback.data or "").split(":", 1)
    except Exception:
        return await callback.answer("Неверные данные", show_alert=True)
    canonical = normalize_aqua_service(service)
    if not canonical:
        return await callback.answer("Неизвестный сервис", show_alert=True)
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        await set_user_setting(session, user, AQUA_SERVICE_KEY, canonical)
    await aqua_service_menu(callback)
