import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _parse_admin_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


class Config:
    BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

    _admins_env = os.getenv("ADMIN_IDS", "").strip()
    ADMIN_IDS = _parse_admin_ids(_admins_env) if _admins_env else []

    # URL БД: см. database.py (там же проверка Postgres на Railway)
    DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

    VALIDEMAIL_URL = os.getenv("VALIDEMAIL_URL", "https://validemail.co/api/v1/validate").strip()
    VALIDEMAIL_API_KEY_1 = os.getenv("VALIDEMAIL_API_KEY_1", "").strip()
    VALIDEMAIL_API_KEY_2 = os.getenv("VALIDEMAIL_API_KEY_2", "").strip()
    _keys_env = os.getenv("VALIDEMAIL_API_KEYS", "").strip()
    if _keys_env:
        VALIDEMAIL_API_KEYS = [x.strip() for x in _keys_env.split(",") if x.strip()]
    else:
        VALIDEMAIL_API_KEYS = [k for k in (VALIDEMAIL_API_KEY_1, VALIDEMAIL_API_KEY_2) if k]
    VALIDEMAIL_CONCURRENCY = int(os.getenv("VALIDEMAIL_CONCURRENCY", "12"))

    GLOBAL_SUBJECT_TEMPLATE = os.getenv("GLOBAL_SUBJECT_TEMPLATE", "OFFER").strip() or "OFFER"

    # AQUA / GOO NETWORK (Финляндия)
    GOO_API_BASE = os.getenv("GOO_API_BASE", "https://api.goo.network").strip().rstrip("/")
    # Общий ключ команды AQUA (X-Team-Key) — один на всех пользователей бота
    AQUA_TEAM_API_KEY = (os.getenv("AQUA_TEAM_API_KEY") or "").strip()
    AQUA_PROFILES_LIST_PATH = (
        os.getenv("AQUA_PROFILES_LIST_PATH", "/api/generate/single/profile/list") or ""
    ).strip()
    AQUA_TEAM_PROFILES_JSON = (os.getenv("AQUA_TEAM_PROFILES_JSON") or "").strip()
    COUNTRY_CODE = "FI"
    COUNTRY_LABEL = "Финляндия"

    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
    DEEPSEEK_API_BASE = (os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com") or "").strip().rstrip("/")
    DEEPSEEK_MODEL = (os.getenv("DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat").strip()
    TRANSLATE_PROVIDER = (os.getenv("TRANSLATE_PROVIDER", "auto") or "auto").strip().lower()


config = Config()
