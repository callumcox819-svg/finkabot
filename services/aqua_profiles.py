"""Профили команды AQUA (GOO NETWORK) — загрузка из API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiohttp

from config import config
from services.aqua_keys import normalize_aqua_api_key
from services.aqua_network import AquaError, _api_base, _auth_header


@dataclass(frozen=True)
class AquaProfile:
    profile_id: str
    title: str
    full_name: str
    address: str

    def button_label(self, max_len: int = 48) -> str:
        parts = [p for p in (self.title, self.full_name) if p]
        label = " · ".join(parts) if parts else self.profile_id
        if len(label) > max_len:
            return label[: max_len - 1] + "…"
        return label

    def display_short(self) -> str:
        if self.title:
            return f"{self.title} ({self.profile_id})"
        return self.profile_id


_DEFAULT_LIST_PATHS = (
    "/api/generate/single/profile/list",
    "/api/generate/single/profiles/list",
    "/api/generate/single/profile/all",
)


def _list_paths() -> tuple[str, ...]:
    custom = (getattr(config, "AQUA_PROFILES_LIST_PATH", None) or "").strip()
    if custom:
        return (custom,) + tuple(p for p in _DEFAULT_LIST_PATHS if p != custom)
    return _DEFAULT_LIST_PATHS


def _pick_str(data: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _parse_profile_item(raw: Any) -> AquaProfile | None:
    if not isinstance(raw, dict):
        return None
    pid = _pick_str(
        raw,
        "profileID",
        "profileId",
        "profile_id",
        "id",
        "_id",
        "token",
    )
    if not pid:
        return None
    title = _pick_str(raw, "name", "title", "label", "profileName", "profile_name")
    full_name = _pick_str(
        raw,
        "buyer_name",
        "buyerName",
        "fullName",
        "full_name",
        "fio",
        "buyer",
    )
    address = _pick_str(raw, "address", "addr", "buyer_address", "buyerAddress")
    return AquaProfile(
        profile_id=pid,
        title=title,
        full_name=full_name,
        address=address,
    )


def _extract_profile_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("data", "profiles", "items", "result", "list"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    msg = data.get("message")
    if isinstance(msg, list):
        return msg
    if isinstance(msg, dict):
        for key in ("profiles", "items", "data", "list"):
            val = msg.get(key)
            if isinstance(val, list):
                return val
    return []


def _parse_profiles_response(data: Any) -> list[AquaProfile]:
    if isinstance(data, dict) and data.get("status") is False:
        raise AquaError(str(data.get("message") or data)[:300])
    items = _extract_profile_list(data)
    out: list[AquaProfile] = []
    seen: set[str] = set()
    for item in items:
        prof = _parse_profile_item(item)
        if prof and prof.profile_id not in seen:
            seen.add(prof.profile_id)
            out.append(prof)
    return out


async def fetch_aqua_team_profiles(
    *,
    user_api_key: str,
    team_api_key: str,
    service: str | None = None,
    timeout_sec: float = 30.0,
) -> list[AquaProfile]:
    """Список готовых профилей команды из GOO (как в боте AQUA)."""
    user_key = normalize_aqua_api_key(user_api_key)
    team_key = normalize_aqua_api_key(team_api_key)
    if not user_key:
        raise AquaError("Не задан личный API key")
    if not team_key:
        raise AquaError("Не задан ключ команды AQUA (AQUA_TEAM_API_KEY)")

    body: dict[str, Any] = {}
    svc = (service or "").strip()
    if svc:
        body["service"] = svc

    headers = {
        "Authorization": _auth_header(user_key),
        "Host": "api.goo.network",
        "X-Team-Key": team_key,
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    last_err: str | None = None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for path in _list_paths():
            url = f"{_api_base()}{path}"
            async with session.post(url, json=body, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status == 404:
                    last_err = f"HTTP 404: {path}"
                    continue
                if resp.status != 200:
                    msg = ""
                    if isinstance(data, dict):
                        msg = str(data.get("message") or data.get("error") or "")
                    last_err = f"HTTP {resp.status} ({path}): {msg or text[:200]}"
                    if resp.status in (401, 403):
                        raise AquaError(last_err)
                    continue
                if not isinstance(data, dict):
                    last_err = f"Bad JSON ({path}): {text[:200]}"
                    continue
                profiles = _parse_profiles_response(data)
                if profiles:
                    return profiles
                last_err = f"Пустой список профилей ({path})"

    if last_err and "404" in last_err:
        raise AquaError(
            "API GOO не отдаёт список профилей (все пути 404). "
            "Введи Profile ID вручную: в боте AQUA → Мой профиль → Профили… "
            "(код вида 7Fm70U0QUMU). Проверь также личный API key и AQUA_TEAM_API_KEY на сервере."
        )
    raise AquaError(last_err or "Не удалось загрузить профили AQUA")


def profiles_from_env_json() -> list[AquaProfile]:
    """Опциональный fallback: AQUA_TEAM_PROFILES_JSON в Variables."""
    raw = (getattr(config, "AQUA_TEAM_PROFILES_JSON", None) or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AquaError(f"AQUA_TEAM_PROFILES_JSON: {e}") from e
    return _parse_profiles_response(data if isinstance(data, dict) else {"message": data})
