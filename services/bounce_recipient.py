"""Отбои доставки получателю (mailer-daemon) — не ответы продавцов."""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Offer, OfferEmail

_RECIPIENT_FAIL_PHRASES = (
    "address not found",
    "wasn't delivered",
    "was not delivered",
    "unable to receive mail",
    "couldn't be found",
    "could not be found",
    "mailbox unavailable",
    "user unknown",
    "recipient address rejected",
    "no such user",
    "does not exist",
    "undeliverable",
    "550 5.5.0",
    "550 5.1.1",
    "5.5.0 requested action not taken",
    "requested action not taken: mailbox unavailable",
)

_BOUNCE_EMAIL_PATTERNS = (
    re.compile(
        r"wasn['']?t delivered to\s+\*?\*?\s*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
        re.I,
    ),
    re.compile(
        r"delivery to the following recipient failed[^\n]*\n[^\n]*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
        re.I,
    ),
    re.compile(r"final-recipient:\s*rfc822;\s*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?", re.I),
    re.compile(r"to:\s*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?", re.I),
)


def is_recipient_delivery_failure_bounce(subject: str, body: str) -> bool:
    """DSN: адрес получателя не существует / ящик недоступен (не block отправителя)."""
    blob = f"{subject or ''}\n{body or ''}".lower()
    return any(p in blob for p in _RECIPIENT_FAIL_PHRASES)


def extract_bounce_recipient_email(subject: str, body: str) -> Optional[str]:
    blob = f"{subject or ''}\n{body or ''}"
    for pat in _BOUNCE_EMAIL_PATTERNS:
        m = pat.search(blob)
        if m:
            addr = (m.group(1) or "").strip().lower()
            if "@" in addr and "mailer-daemon" not in addr and "postmaster" not in addr:
                return addr
    m2 = re.search(
        r"\b([a-zA-Z0-9._%+\-]+@(?!mail\.|postmaster)[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b",
        blob,
        re.I,
    )
    if m2:
        return (m2.group(1) or "").strip().lower()
    return None


async def purge_offer_emails_for_recipient(
    session: AsyncSession,
    *,
    user_id: int,
    recipient_email: str,
) -> int:
    """Убрать мёртвый email из очереди рассылки (OfferEmail)."""
    fe = (recipient_email or "").strip().lower()
    if not fe or "@" not in fe:
        return 0
    offer_ids = sa_select(Offer.id).where(Offer.user_id == int(user_id))
    res = await session.execute(
        delete(OfferEmail).where(
            OfferEmail.offer_id.in_(offer_ids),
            func.lower(OfferEmail.email) == fe,
        )
    )
    await session.commit()
    return int(res.rowcount or 0)


async def purge_from_dsn_body(
    session: AsyncSession,
    *,
    user_id: int,
    subject: str,
    body: str,
) -> tuple[int, str | None]:
    addr = extract_bounce_recipient_email(subject, body)
    if not addr:
        return 0, None
    n = await purge_offer_emails_for_recipient(session, user_id=user_id, recipient_email=addr)
    return n, addr
