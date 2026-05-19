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
    AQUA_PROFILE_ADDRESS_KEY,
    AQUA_PROFILE_NAME_KEY,
    AQUA_PROFILE_TITLE_KEY,
    AQUA_SERVICE_KEY,
    AQUA_USER_API_KEY_SETTING,
    apply_aqua_profile_to_user,
    aqua_service_label,
    get_global_aqua_team_key,
    get_user_aqua_api_keys_async,
    get_user_aqua_service,
    get_user_aqua_user_key_async,
    get_user_goo_profile_id,
    normalize_aqua_service,
)
from services.aqua_network import AquaError
from services.aqua_profiles import (
    AquaProfile,
    fetch_aqua_team_profiles,
    profiles_from_env_json,
)
from services.user_settings import get_user_setting, set_user_setting
from utils.secrets import clean_secret

router = Router(name="api_keys")

_PROFILES_PAGE_SIZE = 8


class KeysState(StatesGroup):
    waiting_value = State()


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")]]
    )


def profile_screen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Выбрать профиль AQUA", callback_data="aqua_profile_list:0")],
            [InlineKeyboardButton(text="✍️ Ввести Profile ID", callback_data="aqua_set:profile_id")],
            [InlineKeyboardButton(text="🔄 Обновить список", callback_data="aqua_profile_list:0")],
            [InlineKeyboardButton(text="🧭 Сервис", callback_data="aqua_service_menu")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
            [InlineKeyboardButton(text="🟢 Скрыть", callback_data="aqua_hide")],
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


def _profiles_list_kb(profiles: list[AquaProfile], page: int) -> InlineKeyboardMarkup:
    page = max(0, page)
    start = page * _PROFILES_PAGE_SIZE
    chunk = profiles[start : start + _PROFILES_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for p in chunk:
        rows.append(
            [
                InlineKeyboardButton(
                    text=p.button_label(),
                    callback_data=f"aqua_prof_sel:{p.profile_id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=f"aqua_profile_list:{page - 1}")
        )
    if start + _PROFILES_PAGE_SIZE < len(profiles):
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=f"aqua_profile_list:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="aqua_show:profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _load_team_profiles(session, user) -> list[AquaProfile]:
    user_key, team_key = await get_user_aqua_api_keys_async(session, user)
    service = await get_user_aqua_service(session, user)
    try:
        return await fetch_aqua_team_profiles(
            user_api_key=user_key,
            team_api_key=team_key,
            service=service or None,
        )
    except AquaError:
        fallback = profiles_from_env_json()
        if fallback:
            return fallback
        raise


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
            "Профили создаются в боте AQUA (GOO). Здесь выбираешь готовый — "
            "при генерации уйдёт его <code>profileID</code>.\n\n"
            f"Название: <code>{prof_title}</code>\n"
            f"ФИО: <code>{prof_name}</code>\n"
            f"Адрес: <code>{prof_addr}</code>\n"
            f"Profile ID: <code>{profile_id}</code>\n"
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
async def aqua_show_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_profile_screen(callback)


@router.callback_query(F.data == "aqua_show:key")
async def aqua_show_key(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_key_screen(callback)


@router.callback_query(F.data.startswith("aqua_profile_list:"))
async def aqua_profile_list(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int((callback.data or "").split(":", 1)[1])
    except Exception:
        page = 0

    await callback.answer("Загрузка…")
    try:
        async with Session() as session:
            user = await get_or_create_user(session, callback.from_user.id)
            user_key = await get_user_aqua_user_key_async(session, user)
            if not user_key:
                await callback.message.edit_text(
                    "❌ Сначала укажи личный API key: ⚙️ → 🔑 Ключ",
                    reply_markup=_back_kb(),
                )
                return
            if not get_global_aqua_team_key():
                await callback.message.edit_text(
                    "❌ На сервере не задан <code>AQUA_TEAM_API_KEY</code>.",
                    parse_mode="HTML",
                    reply_markup=_back_kb(),
                )
                return
            profiles = await _load_team_profiles(session, user)
    except AquaError as e:
        await callback.message.edit_text(
            f"❌ Не удалось загрузить профили:\n<code>{str(e)[:350]}</code>\n\n"
            "Список из API недоступен — нажми <b>✍️ Ввести Profile ID</b> и вставь код "
            "из бота AQUA (Мой профиль → Профили…).\n"
            "Либо проверь личный API key и <code>AQUA_TEAM_API_KEY</code> на Railway.",
            parse_mode="HTML",
            reply_markup=profile_screen_kb(),
        )
        return

    if not profiles:
        await callback.message.edit_text(
            "📭 Нет профилей в команде AQUA.\nСоздай их в боте GOO: Мой профиль → Профили…",
            reply_markup=profile_screen_kb(),
        )
        return

    await state.update_data(
        aqua_profiles_cache=[p.__dict__ for p in profiles],
    )
    total_pages = (len(profiles) + _PROFILES_PAGE_SIZE - 1) // _PROFILES_PAGE_SIZE
    page = min(max(0, page), max(0, total_pages - 1))
    text = (
        f"📋 <b>Профили AQUA</b> ({len(profiles)})\n\n"
        "Выбери профиль — данные (ФИО, адрес) подставятся при генерации."
    )
    if total_pages > 1:
        text += f"\n\nСтр. {page + 1}/{total_pages}"
    await callback.message.edit_text(
        text,
        reply_markup=_profiles_list_kb(profiles, page),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("aqua_prof_sel:"))
async def aqua_profile_select(callback: CallbackQuery, state: FSMContext) -> None:
    profile_id = (callback.data or "").split(":", 1)[1].strip()
    if not profile_id:
        return await callback.answer("Нет ID профиля", show_alert=True)

    data = await state.get_data()
    cached = data.get("aqua_profiles_cache") or []
    chosen: AquaProfile | None = None
    for raw in cached:
        if isinstance(raw, dict) and str(raw.get("profile_id") or "") == profile_id:
            chosen = AquaProfile(
                profile_id=profile_id,
                title=str(raw.get("title") or ""),
                full_name=str(raw.get("full_name") or ""),
                address=str(raw.get("address") or ""),
            )
            break

    if not chosen:
        try:
            async with Session() as session:
                user = await get_or_create_user(session, callback.from_user.id)
                for p in await _load_team_profiles(session, user):
                    if p.profile_id == profile_id:
                        chosen = p
                        break
        except AquaError as e:
            return await callback.answer(str(e)[:180], show_alert=True)

    if not chosen:
        chosen = AquaProfile(
            profile_id=profile_id,
            title="",
            full_name="",
            address="",
        )

    async with Session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        await apply_aqua_profile_to_user(session, user, chosen)
        await session.commit()

    await callback.answer("✅ Профиль выбран")
    await _render_profile_screen(callback)


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
        "✍️ <b>Profile ID</b> из бота AQUA (GOO)\n\n"
        "Путь: <b>Мой профиль → Профили…</b> — скопируй идентификатор "
        "(например <code>7Fm70U0QUMU</code>).\n\n"
        "Это не API key — только код выбранного профиля.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="aqua_show:profile")]
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
        await message.answer(f"✅ Profile ID сохранён: <code>{value}</code>", parse_mode="HTML")
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
    await aqua_service_menu(callback)
