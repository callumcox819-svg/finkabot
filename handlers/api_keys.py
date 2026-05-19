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
    AQUA_USER_API_KEY_SETTING,
    apply_aqua_profile_to_user,
    aqua_service_label,
    get_global_aqua_team_key,
    get_user_aqua_service,
    get_user_aqua_user_key_async,
    get_user_goo_profile_id,
    normalize_aqua_service,
)
from services.aqua_profiles import AquaProfile
from services.user_settings import set_user_setting
from utils.secrets import clean_secret

router = Router(name="api_keys")


class KeysState(StatesGroup):
    waiting_value = State()


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]
    )


def profile_screen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆔 Profile ID", callback_data="aqua_profile_id:menu")],
            [InlineKeyboardButton(text="🧭 Сервис", callback_data="aqua_service_menu")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            [InlineKeyboardButton(text="🟢 Скрыть", callback_data="aqua_hide")],
        ]
    )


def profile_id_view_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="aqua_set:profile_id")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aqua_show:profile")],
        ]
    )


def key_screen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Личный API key", callback_data="aqua_set:user_key")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            [InlineKeyboardButton(text="🟢 Скрыть", callback_data="aqua_hide")],
        ]
    )


def _show_full(key: str | None) -> str:
    return (key or "—").strip() or "—"


def _profile_id_display(user) -> str:
    pid = get_user_goo_profile_id(user)
    if pid:
        return f"<code>{pid}</code>"
    return "<i>не задан</i>"


async def _render_profile_screen(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        service_raw = await get_user_aqua_service(session, user)
        service = aqua_service_label(service_raw) if service_raw else "—"
        pid = get_user_goo_profile_id(user)
        pid_line = f"<code>{pid}</code>" if pid else "<i>не задан</i>"
        text = (
            "👤 <b>Профиль AQUA</b>\n\n"
            f"Сервис: <b>{service}</b>"
            + (f" (<code>{service_raw}</code>)" if service_raw else "")
            + f"\nProfile ID: {pid_line}\n\n"
            f"🇫🇮 {config.COUNTRY_LABEL} · команда <b>AQUA</b>"
        )
    try:
        await callback.message.edit_text(text, reply_markup=profile_screen_kb(), parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _render_profile_id_screen(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        text = (
            "🆔 <b>Profile ID</b> (бот AQUA / GOO)\n\n"
            f"Текущий ID: {_profile_id_display(user)}\n\n"
            "<i>Мой профиль → Профили…</i> в боте AQUA — скопируй код "
            "(например <code>7Fm70U0QUMU</code>)."
        )
    await callback.message.edit_text(
        text, reply_markup=profile_id_view_kb(), parse_mode="HTML"
    )


async def _render_key_screen(callback: CallbackQuery) -> None:
    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        user_key = await get_user_aqua_user_key_async(session, user)
    team_ok = bool(get_global_aqua_team_key())
    text = (
        "🔑 <b>Ключ AQUA</b> (api.goo.network)\n\n"
        f"Личный API key: {'✅' if user_key else '❌'}\n<code>{_show_full(user_key)}</code>\n\n"
        f"Ключ команды AQUA: {'✅' if team_ok else '❌'} "
        "<i>(общий для всех, задаётся на сервере)</i>\n\n"
        f"🇫🇮 {config.COUNTRY_LABEL} · команда <b>AQUA</b>"
    )
    await callback.message.edit_text(text, reply_markup=key_screen_kb(), parse_mode="HTML")


@router.callback_query(F.data == "aqua_hide")
async def aqua_hide(callback: CallbackQuery) -> None:
    await callback.message.edit_text("✅ Скрыто.")
    await callback.answer()


@router.callback_query(F.data == "aqua_show:profile")
async def aqua_show_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _render_profile_screen(callback)


@router.callback_query(F.data == "aqua_profile_id:menu")
async def aqua_profile_id_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _render_profile_id_screen(callback)


@router.callback_query(F.data == "aqua_show:key")
async def aqua_show_key(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_key_screen(callback)


@router.callback_query(F.data == "aqua_set:user_key")
async def aqua_set_user_key_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeysState.waiting_value)
    await state.update_data(field="aqua_user_key")
    await callback.message.edit_text(
        "✍️ Личный API key (заголовок <code>Authorization: Apikey …</code>)",
        reply_markup=_back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "aqua_set:profile_id")
async def aqua_set_profile_id_begin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeysState.waiting_value)
    await state.update_data(field="aqua_profile_id")
    await callback.message.edit_text(
        "✍️ Введи <b>Profile ID</b> из бота AQUA\n\n"
        "<i>Мой профиль → Профили…</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="aqua_profile_id:menu")]
            ]
        ),
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
        elif field == "aqua_profile_id":
            await apply_aqua_profile_to_user(
                session,
                user,
                AquaProfile(profile_id=value, title="", full_name="", address=""),
            )
        else:
            await message.answer("❌ Неизвестное поле.")
            return
        await session.commit()

    await state.clear()
    if field == "aqua_profile_id":
        await message.answer(
            f"✅ Profile ID сохранён\n\nТекущий ID: <code>{value}</code>",
            parse_mode="HTML",
            reply_markup=profile_id_view_kb(),
        )
    else:
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
        await session.commit()
    await callback.answer("✅ Сервис выбран")
    await aqua_service_menu(callback)
