"""GOO NETWORK / AQUA — генерация ссылок (Финляндия)."""

from __future__ import annotations

import re
from typing import Any

import aiohttp

from config import config


class AquaError(Exception):
    pass


def _api_base() -> str:
    return (getattr(config, "GOO_API_BASE", None) or "https://api.goo.network").strip().rstrip("/")


def _auth_header(user_api_key: str) -> str:
    key = (user_api_key or "").strip()
    if key.lower().startswith("apikey "):
        return key
    return f"Apikey {key}"


def price_to_api_number(price: str | float | int | None) -> float:
    """Число для поля price в no-parse (валюта по service)."""
    if price is None:
        raise AquaError("Нет цены")
    if isinstance(price, (int, float)):
        n = float(price)
        if n < 0:
            raise AquaError("Некорректная цена")
        return n
    raw = str(price).strip().replace(",", ".")
    m = re.search(r"([\d.]+)", raw)
    if not m:
        raise AquaError(f"Не удалось разобрать цену: {price!r}")
    n = float(m.group(1))
    if n < 0:
        raise AquaError("Некорректная цена")
    return n


async def _post_generate(
    path: str,
    *,
    user_api_key: str,
    team_api_key: str,
    body: dict[str, Any],
    timeout_sec: float = 30.0,
) -> str:
    user_key = (user_api_key or "").strip()
    team_key = (team_api_key or "").strip()
    if not user_key:
        raise AquaError("Не задан User API key (AQUA)")
    if not team_key:
        raise AquaError("Не задан Team API key (AQUA)")

    url = f"{_api_base()}{path}"
    headers = {
        "Authorization": _auth_header(user_key),
        "Host": "api.goo.network",
        "X-Team-Key": team_key,
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            text = await resp.text()
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None
            if resp.status != 200:
                msg = ""
                if isinstance(data, dict):
                    msg = str(data.get("message") or data.get("error") or "")
                raise AquaError(f"HTTP {resp.status}: {msg or text[:300]}")
            if not isinstance(data, dict):
                raise AquaError(f"Bad JSON: {text[:300]}")
            if not data.get("status"):
                raise AquaError(str(data.get("message") or data)[:300])
            link = data.get("message") or data.get("url")
            if not link:
                raise AquaError(f"No link in response: {str(data)[:300]}")
            return str(link).strip()


async def generate_aqua_link_parse(
    *,
    user_api_key: str,
    team_api_key: str,
    service: str,
    listing_url: str,
    profile_id: str,
    balance_checker: bool = False,
    timeout_sec: float = 30.0,
) -> str:
    """POST /api/generate/single/parse — ссылка из URL объявления."""
    pid = (profile_id or "").strip()
    if not pid:
        raise AquaError("Не задан profileID (профиль GOO)")
    listing = (listing_url or "").strip()
    if not listing:
        raise AquaError("Нет URL объявления")
    body = {
        "service": service,
        "url": listing,
        "isNeedBalanceChecker": bool(balance_checker),
        "profileID": pid,
    }
    return await _post_generate(
        "/api/generate/single/parse",
        user_api_key=user_api_key,
        team_api_key=team_api_key,
        body=body,
        timeout_sec=timeout_sec,
    )


async def generate_aqua_link_no_parse(
    *,
    user_api_key: str,
    team_api_key: str,
    service: str,
    name: str,
    price: str | float | int,
    profile_id: str,
    image: str | None = None,
    balance_checker: bool = False,
    timeout_sec: float = 30.0,
) -> str:
    """POST /api/generate/single/no-parse — ссылка по названию/цене/фото."""
    pid = (profile_id or "").strip()
    if not pid:
        raise AquaError("Не задан profileID (профиль GOO)")
    title = (name or "").strip()
    if not title:
        raise AquaError("Нет названия товара")
    body: dict[str, Any] = {
        "service": service,
        "name": title,
        "price": price_to_api_number(price),
        "isNeedBalanceChecker": bool(balance_checker),
        "profileID": pid,
    }
    img = (image or "").strip()
    if img:
        body["image"] = img
    return await _post_generate(
        "/api/generate/single/no-parse",
        user_api_key=user_api_key,
        team_api_key=team_api_key,
        body=body,
        timeout_sec=timeout_sec,
    )


async def generate_aqua_link(
    *,
    user_api_key: str,
    team_api_key: str,
    service: str,
    profile_id: str,
    listing_url: str | None = None,
    name: str | None = None,
    price: str | float | int | None = None,
    image: str | None = None,
    balance_checker: bool = False,
    prefer_parse: bool = True,
    timeout_sec: float = 30.0,
) -> str:
    """С парсером, если есть URL объявления; иначе no-parse."""
    if prefer_parse and (listing_url or "").strip():
        try:
            return await generate_aqua_link_parse(
                user_api_key=user_api_key,
                team_api_key=team_api_key,
                service=service,
                listing_url=str(listing_url),
                profile_id=profile_id,
                balance_checker=balance_checker,
                timeout_sec=timeout_sec,
            )
        except AquaError:
            if not (name or "").strip() or price is None:
                raise
    return await generate_aqua_link_no_parse(
        user_api_key=user_api_key,
        team_api_key=team_api_key,
        service=service,
        name=str(name or ""),
        price=price if price is not None else "0",
        profile_id=profile_id,
        image=image,
        balance_checker=balance_checker,
        timeout_sec=timeout_sec,
    )
