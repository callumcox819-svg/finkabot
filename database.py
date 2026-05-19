# database.py
from __future__ import annotations

import os
import sys
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from models import Base

log = logging.getLogger(__name__)

_LOCAL_SQLITE_FALLBACK = "sqlite+aiosqlite:///./bot.db"


def is_railway_runtime() -> bool:
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_SERVICE_NAME")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_DEPLOYMENT_ID")
    )


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def is_ephemeral_database_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return True
    if "${{" in u or u.startswith("${"):
        return True
    return u.startswith("sqlite")


def is_persistent_database_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("postgresql") or u.startswith("postgres://")


def normalize_database_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def resolve_database_url() -> str:
    """Источник правды для URL БД (config.py дублировать не нужно)."""
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if raw:
        return normalize_database_url(raw)
    if is_railway_runtime():
        return ""
    return _LOCAL_SQLITE_FALLBACK


def database_url_for_logs(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return "<empty>"
    if u.startswith("sqlite"):
        return u
    try:
        p = urlparse(u.replace("postgresql+asyncpg://", "postgresql://", 1))
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        db = (p.path or "").lstrip("/") or "?"
        return f"postgresql://***@{host}{port}/{db}"
    except Exception:
        return "postgresql://***"


def _railway_variables_hint() -> str:
    if (os.getenv("APP_ROLE") or "").strip() == "imap_worker":
        name = (os.getenv("RAILWAY_SERVICE_NAME") or "").strip()
        return f"Сервис IMAP ({name or 'imap_worker / unique-solace'})"
    name = (os.getenv("RAILWAY_SERVICE_NAME") or "").strip()
    if name:
        return f"Сервис «{name}»"
    return "Сервис бота (finkabot)"


def assert_persistent_database_or_exit(url: str | None = None) -> None:
    """На Railway SQLite/пустой DATABASE_URL — данные пропадают при redeploy."""
    db_url = normalize_database_url(url or resolve_database_url())
    if not is_railway_runtime():
        return
    if _truthy_env("ALLOW_EPHEMERAL_DB"):
        log.warning("ALLOW_EPHEMERAL_DB=1 — SQLite на Railway разрешён (данные НЕ сохраняются)")
        return
    if is_persistent_database_url(db_url):
        return

    log.critical("=" * 60)
    log.critical("Railway: нужен PostgreSQL, иначе всё сбросится при redeploy!")
    log.critical("DATABASE_URL сейчас: %s", database_url_for_logs(db_url))
    log.critical("")
    log.critical("1) В проекте Railway: + New → Database → PostgreSQL")
    svc = _railway_variables_hint()
    log.critical("2) %s → Variables → удали ПУСТУЮ DATABASE_URL (если есть)", svc)
    log.critical("3) + New Variable → Variable Reference → Postgres → DATABASE_URL")
    log.critical("4) Redeploy. В логах: «БД: PostgreSQL»")
    log.critical("Подробно: RAILWAY_DATABASE.txt")
    log.critical("=" * 60)
    sys.exit(1)


DATABASE_URL = resolve_database_url()
assert_persistent_database_or_exit(DATABASE_URL)

if not DATABASE_URL:
    DATABASE_URL = _LOCAL_SQLITE_FALLBACK

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"timeout": 60}
elif DATABASE_URL.startswith("postgresql"):
    _engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "15"))
    _engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "25"))
    _engine_kwargs["pool_timeout"] = int(os.getenv("DB_POOL_TIMEOUT", "30"))

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

# ✅ основная фабрика сессий (как ждут handlers/send.py и др.)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# ✅ алиас для старого кода (как ждёт middlewares/access.py)
Session = async_session


@asynccontextmanager
async def db_session():
    """Postgres/SQLite сессия со сбросом PySocks-патча (иначе пул зависает под SMTP)."""
    from proxy_manager import database_socket_guard

    async with database_socket_guard():
        async with Session() as session:
            yield session


async def _ensure_users_telegram_bigint() -> None:
    """
    Автомиграция: users.telegram_id INTEGER -> BIGINT
    Чтобы Telegram ID типа 7416000184 не ломал запросы.
    """
    # Эти миграции написаны под Postgres (information_schema, ::bigint).
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        res = await conn.execute(
            text("""
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='telegram_id'
                LIMIT 1
            """)
        )
        row = res.first()
        if not row:
            return

        data_type = (row[0] or "").lower()
        if data_type in ("integer", "int4"):
            log.warning("Migrating users.telegram_id from INTEGER to BIGINT ...")
            await conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT USING telegram_id::bigint"))
            log.warning("Migrated users.telegram_id to BIGINT ✅")


async def _ensure_incoming_mail_telegram_message_id_column() -> None:
    """Не дублировать карточку входящего в TG при повторном IMAP-опросе."""
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE incoming_mails ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT")
        )


async def _ensure_incoming_mail_link_columns() -> None:
    """Автомиграция: добавляем incoming_mails.ad_url и incoming_mails.generated_link если их нет.

    В проекте нет alembic-миграций, поэтому добавляем колонки безопасно через information_schema.
    """
    # Postgres-only (ALTER TABLE ... IF NOT EXISTS)
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        # Railway/Postgres поддерживает IF NOT EXISTS для ADD COLUMN
        await conn.execute(text("ALTER TABLE incoming_mails ADD COLUMN IF NOT EXISTS ad_url TEXT"))
        await conn.execute(text("ALTER TABLE incoming_mails ADD COLUMN IF NOT EXISTS generated_link TEXT"))


async def _ensure_conversation_links_generated_link_column() -> None:
    """Автомиграция: добавляем conversation_links.generated_link если её нет.

    Нужна для кнопки "Создать ссылку" (генерация ссылки сохраняется в ConversationLink).
    """
    # Postgres-only (ALTER TABLE ... IF NOT EXISTS)
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE conversation_links ADD COLUMN IF NOT EXISTS generated_link TEXT"))


async def _ensure_offers_raw_json_column() -> None:
    """Полный JSON объявления из парсера (все поля для генерации ссылок)."""
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE offers ADD COLUMN IF NOT EXISTS raw_json TEXT"))


async def _migrate_seller_blacklist_names() -> None:
    """ЧС продавцов по имени из JSON (не по email)."""
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE seller_blacklist ADD COLUMN IF NOT EXISTS seller_name_key TEXT"))
        await conn.execute(text("ALTER TABLE seller_blacklist ADD COLUMN IF NOT EXISTS seller_name_display TEXT"))
        await conn.execute(text("ALTER TABLE seller_blacklist DROP COLUMN IF EXISTS seller_email"))
        await conn.execute(text("ALTER TABLE seller_blacklist DROP COLUMN IF EXISTS note"))


async def _ensure_conversation_links_pinned_offer_id_column() -> None:
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE conversation_links ADD COLUMN IF NOT EXISTS pinned_offer_id INTEGER")
        )


async def _ensure_conversation_links_tg_message_id_column() -> None:
    """Автомиграция: добавляем conversation_links.tg_message_id если её нет.

    Нужна для трединга в Telegram: повторные сообщения от продавца должны отправляться
    reply_to первому сообщению.
    """
    if engine.dialect.name != "postgresql":
        return

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE conversation_links ADD COLUMN IF NOT EXISTS tg_message_id BIGINT"))


async def init_db() -> None:
    dialect = engine.dialect.name
    if dialect == "postgresql":
        log.info(
            "БД: PostgreSQL · %s (аккаунты, офферы, входящие, настройки — сохраняются при redeploy)",
            database_url_for_logs(DATABASE_URL),
        )
    else:
        log.warning(
            "БД: %s · %s (только локально; на Railway нужен Postgres)",
            dialect,
            database_url_for_logs(DATABASE_URL),
        )

    # создаём таблицы если нет
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # затем — безопасная миграция типов
    try:
        await _ensure_users_telegram_bigint()
    except Exception as e:
        log.error("Failed users.telegram_id BIGINT migration: %s", e)

    # затем — безопасное добавление колонок в incoming_mails (для "Инфо" и генерации ссылок)
    try:
        await _ensure_incoming_mail_link_columns()
    except Exception as e:
        log.error("Failed incoming_mails link columns migration: %s", e)

    try:
        await _ensure_incoming_mail_telegram_message_id_column()
    except Exception as e:
        log.error("Failed incoming_mails.telegram_message_id migration: %s", e)

    # затем — безопасное добавление колонки в conversation_links (для "Создать ссылку")
    try:
        await _ensure_conversation_links_generated_link_column()
    except Exception as e:
        log.error("Failed conversation_links.generated_link migration: %s", e)

    # затем — tg_message_id для трединга входящих сообщений
    try:
        await _ensure_conversation_links_tg_message_id_column()
    except Exception as e:
        log.error("Failed conversation_links.tg_message_id migration: %s", e)

    try:
        await _ensure_offers_raw_json_column()
    except Exception as e:
        log.error("Failed offers.raw_json migration: %s", e)

    try:
        await _ensure_conversation_links_pinned_offer_id_column()
    except Exception as e:
        log.error("Failed conversation_links.pinned_offer_id migration: %s", e)

    try:
        await _migrate_seller_blacklist_names()
    except Exception as e:
        log.error("Failed seller_blacklist name migration: %s", e)

    if dialect == "postgresql":
        log.info(
            "Хранилище: user_settings, email_accounts, offers, incoming_mails, "
            "user_json_blobs (шаблоны) — в Postgres"
        )
