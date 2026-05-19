from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import Session
from models import User
from config import config


async def get_or_create_user(session: Session, telegram_id: int) -> User:
    """Fetch a user by Telegram ID, creating one if missing.

    Safe for concurrent calls (prevents UNIQUE constraint crash).
    """
    tg_id = int(telegram_id)

    # 1) Try fetch first
    admin_ids = {int(x) for x in (getattr(config, "ADMIN_IDS", []) or [])}

    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    user = result.scalar_one_or_none()
    if user:
        if tg_id in admin_ids and (
            not bool(getattr(user, "is_admin", False))
            or not bool(getattr(user, "access_granted", False))
        ):
            user.is_admin = True
            user.access_granted = True
            user.is_banned = False
            await session.commit()
        return user

    is_admin = tg_id in admin_ids

    # 2) Try create
    user = User(
        telegram_id=tg_id,
        is_banned=False,
        access_granted=is_admin,
        is_admin=is_admin,
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError:
        # Another concurrent request created this user already
        await session.rollback()
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user2 = result.scalar_one_or_none()
        if user2:
            return user2
        raise

    await session.refresh(user)
    return user


async def sync_config_admins_to_db() -> int:
    """При старте: ADMIN_IDS из env → is_admin в Postgres (переживает redeploy)."""
    admin_ids = {int(x) for x in (getattr(config, "ADMIN_IDS", []) or [])}
    if not admin_ids:
        return 0
    updated = 0
    async with Session() as session:
        for tg_id in admin_ids:
            user = await get_or_create_user(session, tg_id)
            changed = False
            if not bool(getattr(user, "is_admin", False)):
                user.is_admin = True
                changed = True
            if not bool(getattr(user, "access_granted", False)):
                user.access_granted = True
                changed = True
            if bool(getattr(user, "is_banned", False)):
                user.is_banned = False
                changed = True
            if changed:
                updated += 1
        if updated:
            await session.commit()
    return updated
