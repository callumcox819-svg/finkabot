# handlers/mail_templates.py
from __future__ import annotations

import logging
from typing import List

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

from database import Session
from models import EmailAccount, User

from services.incoming_mail_worker import FULL_META
from services.smtp_proxy_send import send_email_via_account_with_proxy
from handlers.templates import load_templates, TemplateItem
from handlers.incoming_mail import _bg_incoming_smtp, _reply_notify_build_async
from services.users import get_or_create_user
from models import IncomingMail

router = Router()
logger = logging.getLogger(__name__)


def _templates_kb(
    items: List[TemplateItem],
    acc_id: int,
    uid: str,
    *,
    mail_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    for i, t in enumerate(items[:30]):
        label = (t.title or f"Пресет #{i + 1}").strip()[:40]
        if mail_id:
            cb = f"mail_tmpl_send:{i}:m{int(mail_id)}"
        else:
            cb = f"mail_tmpl_send:{i}:{acc_id}:{uid}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    close_cb = f"mail_tmpl_close:m{int(mail_id)}" if mail_id else f"mail_tmpl_close:{acc_id}:{uid}"
    rows.append([InlineKeyboardButton(text="Скрыть", callback_data=close_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _safe_re_subject(subject: str) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re: message"
    sl = s.lower()
    if sl.startswith("re:"):
        return s
    return f"Re: {s}"


def _render_subject_with_offer(subject_template: str, offer_title: str) -> str:
    from services.subject_offer import render_subject_with_offer

    return render_subject_with_offer(
        (subject_template or "").strip() or "Re: OFFER",
        (offer_title or "").strip() or "OFFER",
    )


def _parse_uid(uid: str) -> int | None:
    """IncomingMail stores imap_uid as int. UID in callbacks can be like '123' or 'S:123'."""
    try:
        u = (uid or "").strip()
        if u.startswith("S:"):
            u = u.split(":", 1)[1]
        return int(u)
    except Exception:
        return None


def _meta_dict_from_mail(m: IncomingMail) -> dict:
    from services.email_address import extract_email_address

    return {
        "from_email": extract_email_address(m.from_email or ""),
        "from_name": (m.from_name or "").strip(),
        "subject": m.subject or "",
        "account_email": extract_email_address(m.account_email or ""),
        "date_str": m.date_str or "",
        "_acc_id": int(m.account_id),
        "_uid": str(m.imap_uid),
        "_mail_id": int(m.id),
    }


async def _load_meta_by_mail_id(mail_id: int) -> dict | None:
    try:
        mid = int(mail_id)
    except (TypeError, ValueError):
        return None
    async with Session() as session:
        m = (
            await session.execute(
                select(IncomingMail).where(IncomingMail.id == mid).limit(1)
            )
        ).scalars().first()
    if not m:
        return None
    return _meta_dict_from_mail(m)


async def _load_meta_from_db(acc_id: int, uid: str) -> dict | None:
    """Fallback after redeploy: FULL_META в RAM очищается, письмо ищем в Postgres."""
    uid_num = _parse_uid(uid)
    if uid_num is None:
        return None

    async with Session() as session:
        m = (
            await session.execute(
                select(IncomingMail)
                .where(IncomingMail.account_id == int(acc_id))
                .where(IncomingMail.imap_uid == int(uid_num))
                .order_by(IncomingMail.id.desc())
                .limit(1)
            )
        ).scalars().first()

    if not m:
        return None

    return _meta_dict_from_mail(m)


async def _resolve_mail_meta(
    *,
    acc_id: int | None = None,
    uid: str | None = None,
    mail_id: int | None = None,
    state_data: dict | None = None,
    tg_message_id: int | None = None,
) -> dict | None:
    """Письмо для пресетов: RAM → Postgres по mail_id → acc+uid → id карточки TG."""
    state_data = state_data or {}
    mid = mail_id or state_data.get("mail_id")
    if mid:
        meta = await _load_meta_by_mail_id(int(mid))
        if meta:
            return meta

    acc = int(acc_id or state_data.get("acc_id") or 0)
    uid_s = str(uid or state_data.get("uid") or "").strip()
    if acc and uid_s:
        meta = FULL_META.get((acc, uid_s)) or await _load_meta_from_db(acc, uid_s)
        if meta:
            return meta

    if tg_message_id:
        async with Session() as session:
            m = (
                await session.execute(
                    select(IncomingMail)
                    .where(IncomingMail.telegram_message_id == int(tg_message_id))
                    .order_by(IncomingMail.id.desc())
                    .limit(1)
                )
            ).scalars().first()
        if m:
            return _meta_dict_from_mail(m)

    return None


_STALE_MAIL_MSG = (
    "Письмо не найдено в базе (не из‑за возраста). "
    "Откройте карточку снова через «Написать ещё» или дождитесь нового входящего."
)


 


@router.callback_query(F.data.startswith("mail_tmpl_open:"))
async def mail_tmpl_open(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    acc_id: int | None = None
    uid: str | None = None
    mail_id: int | None = None
    try:
        parts = (callback.data or "").split(":")
        if len(parts) == 2 and parts[1].startswith("m"):
            mail_id = int(parts[1][1:])
        elif len(parts) >= 3:
            acc_id = int(parts[1])
            uid = ":".join(parts[2:])
    except Exception:
        return await callback.answer("Неверные данные", show_alert=True)

    card_mid = int(callback.message.message_id) if callback.message else None
    meta = await _resolve_mail_meta(
        acc_id=acc_id,
        uid=uid,
        mail_id=mail_id,
        state_data=data,
        tg_message_id=card_mid,
    )
    if not meta:
        return await callback.answer(_STALE_MAIL_MSG, show_alert=True)

    acc_id = int(meta.get("_acc_id") or acc_id or 0)
    uid = str(meta.get("_uid") or uid or "")
    mail_id = int(meta.get("_mail_id") or mail_id or 0) or None

    items = await load_templates(callback.from_user.id)
    if not items:
        return await callback.answer("Нет шаблонов. Добавь их в ⚡ Шаблоны", show_alert=True)

    text = "Нажмите на пресет для отправки"
    await callback.message.answer(
        text,
        reply_markup=_templates_kb(items, acc_id, uid, mail_id=mail_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mail_tmpl_close:"))
async def mail_tmpl_close(callback: CallbackQuery):
    await callback.answer("Ок", show_alert=False)


@router.callback_query(F.data.startswith("mail_tmpl_send:"))
async def mail_tmpl_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    acc_id: int | None = None
    uid: str | None = None
    mail_id: int | None = None
    try:
        parts = (callback.data or "").split(":")
        idx = int(parts[1])
        if len(parts) == 3 and parts[2].startswith("m"):
            mail_id = int(parts[2][1:])
        elif len(parts) >= 4:
            acc_id = int(parts[2])
            uid = ":".join(parts[3:])
        else:
            raise ValueError("bad callback")
    except Exception:
        return await callback.answer("Неверный формат", show_alert=True)

    card_mid = int(callback.message.message_id) if callback.message else None
    meta = await _resolve_mail_meta(
        acc_id=acc_id,
        uid=uid,
        mail_id=mail_id,
        state_data=data,
        tg_message_id=card_mid,
    )
    if not meta:
        return await callback.answer(_STALE_MAIL_MSG, show_alert=True)

    acc_id = int(meta.get("_acc_id") or acc_id or 0)
    uid = str(meta.get("_uid") or uid or "")

    from services.email_address import extract_email_address, is_valid_smtp_recipient

    to_email = extract_email_address(meta.get("from_email") or "")
    if not is_valid_smtp_recipient(to_email):
        return await callback.answer("Не найден email получателя", show_alert=True)

    items = await load_templates(callback.from_user.id)
    if not items:
        return await callback.answer("Нет шаблонов. Добавь их в ⚡ Шаблоны", show_alert=True)

    if idx < 0 or idx >= len(items):
        return await callback.answer("Шаблон не найден", show_alert=True)

    tpl = items[idx]
    body = (tpl.text or "").strip()
    if not body:
        return await callback.answer("Пустой шаблон", show_alert=True)

    subject_orig = (meta.get("subject") or "").strip()
    subject = _safe_re_subject(subject_orig)
    tg_id = callback.from_user.id
    body_copy = body

    async def _send() -> tuple[bool, str | None]:
        async with Session() as session:
            acc = (await session.execute(select(EmailAccount).where(EmailAccount.id == acc_id))).scalars().first()
            if not acc:
                return False, "SMTP аккаунт не найден"
            user = (
                await session.execute(select(User).where(User.telegram_id == int(tg_id)))
            ).scalars().first()
            if not user:
                return False, "Пользователь не найден"
            out_subject = subject
            try:
                from services.user_settings import get_user_setting

                subj_insert = str(await get_user_setting(session, user, "subj_insert") or "").strip().lower() in {
                    "1", "true", "yes", "on",
                }
                if subj_insert:
                    uid_num = _parse_uid(uid)
                    offer_title = ""
                    if uid_num is not None:
                        mrow = (
                            await session.execute(
                                select(IncomingMail)
                                .where(IncomingMail.account_id == int(acc_id))
                                .where(IncomingMail.imap_uid == int(uid_num))
                                .limit(1)
                            )
                        ).scalars().first()
                        if mrow and getattr(mrow, "resolved_offer_id", None):
                            from models import Offer

                            off = (
                                await session.execute(
                                    select(Offer).where(Offer.id == int(mrow.resolved_offer_id)).limit(1)
                                )
                            ).scalars().first()
                            if off:
                                offer_title = (off.title or "").strip()
                    tpl = str(await get_user_setting(session, user, "subject_template") or "Re: OFFER")
                    out_subject = _render_subject_with_offer(tpl, offer_title)
            except Exception:
                pass
            return await send_email_via_account_with_proxy(
                session,
                int(user.id),
                acc,
                to_email,
                out_subject,
                body_copy,
                sender_name=getattr(user, "sender_name", None),
            )

    data = await state.get_data()
    db_user_id: int | None = None
    try:
        async with Session() as session:
            u = await get_or_create_user(session, int(tg_id))
            db_user_id = int(u.id)
    except Exception:
        pass
    notify = await _reply_notify_build_async(
        acc_id=acc_id,
        uid=str(uid),
        meta=meta or {},
        state_data=data,
        body_text=body,
        is_preset=True,
        extra_cleanup=[callback.message.message_id],
        user_id=db_user_id,
    )
    await state.clear()
    if not await _bg_incoming_smtp(callback, tg_id, _send, notify=notify):
        return
    logger.info("MAIL_TEMPLATE queued to=%s acc_id=%s uid=%s idx=%s", to_email, acc_id, uid, idx)
