"""AQUA (GOO NETWORK) — Финляндия: tori.fi, posti.fi."""

from __future__ import annotations

from config import config
from models import User
from services.user_settings import get_user_setting, set_user_setting

AQUA_SERVICE_KEY = "aqua_service"

AQUA_USER_API_KEY_SETTING = "aqua_user_api_key"

AQUA_PROFILE_TITLE_KEY = "aqua_profile_title"
AQUA_PROFILE_NAME_KEY = "aqua_profile_name"
AQUA_PROFILE_ADDRESS_KEY = "aqua_profile_address"

AQUA_SERVICE_CHOICES = ("tori_fi", "posti_fi")

_SERVICE_ALIASES: dict[str, str] = {
    "tori_fi": "tori_fi",
    "tori.fi": "tori_fi",
    "tori": "tori_fi",
    "posti_fi": "posti_fi",
    "posti.fi": "posti_fi",
    "posti": "posti_fi",
}


def normalize_aqua_service(code: str | None) -> str | None:
    s = (code or "").strip().lower()
    if not s:
        return None
    return _SERVICE_ALIASES.get(s)


def is_valid_aqua_service(code: str | None) -> bool:
    return normalize_aqua_service(code) is not None


def aqua_service_for_api(code: str | None) -> str:
    n = normalize_aqua_service(code)
    if not n:
        raise ValueError(f"Unknown AQUA service: {code!r}")
    return n


def aqua_service_for_html_dir(code: str | None) -> str:
    return normalize_aqua_service(code) or ""


def aqua_service_matches(cur: str | None, choice: str) -> bool:
    a = normalize_aqua_service(cur)
    b = normalize_aqua_service(choice)
    return bool(a and b and a == b)


def aqua_service_label(code: str | None) -> str:
    n = normalize_aqua_service(code) or (code or "").strip()
    return {
        "tori_fi": "Tori.fi",
        "posti_fi": "Posti.fi",
    }.get(n, n or "—")


async def get_user_aqua_service(session, user: User) -> str:
    raw = (await get_user_setting(session, user, AQUA_SERVICE_KEY) or "").strip()
    return normalize_aqua_service(raw) or ""


def get_global_aqua_team_key() -> str:
    """Ключ команды AQUA — из переменной окружения AQUA_TEAM_API_KEY."""
    return (getattr(config, "AQUA_TEAM_API_KEY", None) or "").strip()


def get_user_aqua_user_key(user: User) -> str:
    return (getattr(user, "goo_user_api_key_aqua", None) or "").strip()


async def get_user_aqua_user_key_async(session, user: User) -> str:
    user_key = get_user_aqua_user_key(user)
    if not user_key:
        user_key = (
            await get_user_setting(session, user, AQUA_USER_API_KEY_SETTING) or ""
        ).strip()
    return user_key


async def get_user_aqua_api_keys_async(session, user: User) -> tuple[str, str]:
    """(user_api_key, team_api_key) — team всегда глобальный."""
    user_key = await get_user_aqua_user_key_async(session, user)
    return user_key, get_global_aqua_team_key()


def get_user_aqua_api_keys(user: User) -> tuple[str, str]:
    return get_user_aqua_user_key(user), get_global_aqua_team_key()


def get_user_goo_profile_id(user: User) -> str:
    return (getattr(user, "goo_profile_id", None) or "").strip()


async def get_user_aqua_profile_display(session, user: User) -> str:
    """Подпись профиля для карточек (название + profileID)."""
    title = (
        await get_user_setting(session, user, AQUA_PROFILE_TITLE_KEY) or ""
    ).strip()
    pid = get_user_goo_profile_id(user)
    if title and pid:
        return f"{title} ({pid})"
    if title:
        return title
    return pid


async def apply_aqua_profile_to_user(session, user: User, profile) -> None:
    from services.aqua_profiles import AquaProfile

    if not isinstance(profile, AquaProfile):
        raise TypeError("profile must be AquaProfile")
    user.goo_profile_id = profile.profile_id
    await set_user_setting(session, user, AQUA_PROFILE_TITLE_KEY, profile.title)
    await set_user_setting(session, user, AQUA_PROFILE_NAME_KEY, profile.full_name)
    await set_user_setting(session, user, AQUA_PROFILE_ADDRESS_KEY, profile.address)
