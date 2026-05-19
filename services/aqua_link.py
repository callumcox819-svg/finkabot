"""Генерация ссылок AQUA для оффера / входящих."""

from __future__ import annotations

from models import Offer, User
from services.aqua_keys import (
    aqua_service_for_api,
    get_user_aqua_api_keys_async,
    get_user_aqua_service,
    get_user_goo_profile_id,
    is_valid_aqua_service,
)
from services.aqua_network import AquaError, generate_aqua_link
from services.offer_storage import offer_effective_photo, offer_effective_price, offer_effective_title


async def aqua_generate_for_offer(
    session,
    user: User,
    offer: Offer | None,
    *,
    listing_url: str | None = None,
    price: str | None = None,
) -> str:
    user_key, team_key = await get_user_aqua_api_keys_async(session, user)
    if not user_key:
        raise AquaError("Не задан личный API key. ⚙️ → 🔑 Ключ")
    if not team_key:
        raise AquaError("Ключ команды AQUA не задан на сервере (AQUA_TEAM_API_KEY).")

    profile_id = get_user_goo_profile_id(user)
    if not profile_id:
        raise AquaError("Не выбран профиль AQUA. ⚙️ → 🧾 Профиль → Выбрать профиль")

    service = await get_user_aqua_service(session, user)
    if not is_valid_aqua_service(service):
        raise AquaError("Не выбран сервис. ⚙️ → 🧾 Профиль → Tori.fi / Posti.fi")

    title = offer_effective_title(offer)
    if not title:
        raise AquaError("Нет названия объявления")

    p = (price or "").strip() or offer_effective_price(offer)
    if not p:
        raise AquaError("Нет цены")

    image = offer_effective_photo(offer) or None
    api_service = aqua_service_for_api(service)

    return await generate_aqua_link(
        user_api_key=user_key,
        team_api_key=team_key,
        service=api_service,
        profile_id=profile_id,
        listing_url=listing_url,
        name=title,
        price=p,
        image=image,
    )
