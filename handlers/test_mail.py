"""Тестовая рассылка: тема как в /send, до 4 сохранённых получателей."""

from __future__ import annotations

import asyncio
import json
import pathlib
import random
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import async_session
from handlers.first_sms import pick_random_first_sms
from handlers.templates import pick_random_smart_preset
from models import EmailAccount, Offer, OfferEmail, User
from services.aqua_keys import AQUA_PROFILE_ADDRESS_KEY, AQUA_PROFILE_NAME_KEY
from services.offer_storage import offer_effective_title
from services.placeholders import apply_placeholders
from services.smtp_block_control import is_smtp_account_block_error, mark_account_smtp_blocked
from services.smtp_delivery_verify import verify_message_in_sent
from services.smtp_proxy_send import send_email_via_account_with_proxy
from services.subject_offer import subject_for_offer
from services.user_settings import get_user_setting, set_user_setting
from sqlalchemy import func, select
from utils.bg_jobs import is_running as bg_is_running
from utils.bg_jobs import start as bg_start

router = Router()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
TEST_MAIL_RECIPIENTS_KEY = "test_mail_recipients"
MAX_TEST_RECIPIENTS = 4
TEST_SEND_DELAY_SEC = 2.0

class TestMailStates(StatesGroup):
    waiting_recipients = State()


def _canon_email(addr: str) -> str:
    return (addr or "").strip().lower()


def _parse_emails(text: str, *, max_n: int = MAX_TEST_RECIPIENTS) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[\s,;]+", (text or "").strip()):
        e = _canon_email(chunk)
        if e and EMAIL_RE.match(e) and e not in out:
            out.append(e)
        if len(out) >= max_n:
            break
    return out


async def _load_saved_recipients(session, user: User) -> list[str]:
    raw = (await get_user_setting(session, user, TEST_MAIL_RECIPIENTS_KEY) or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [
                e
                for x in data
                if (e := _canon_email(str(x))) and EMAIL_RE.match(e)
            ][:MAX_TEST_RECIPIENTS]
    except json.JSONDecodeError:
        pass
    return _parse_emails(raw)


async def _save_recipients(session, user: User, emails: list[str]) -> None:
    clean = _parse_emails(" ".join(emails), max_n=MAX_TEST_RECIPIENTS)
    await set_user_setting(
        session,
        user,
        TEST_MAIL_RECIPIENTS_KEY,
        json.dumps(clean, ensure_ascii=False),
    )


def _menu_kb(*, has_recipients: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_recipients:
        rows.append(
            [InlineKeyboardButton(text="▶️ Отправить на сохранённые", callback_data="test_mail:send")]
        )
    rows.append(
        [InlineKeyboardButton(text="✏️ Указать получателей (до 4)", callback_data="test_mail:edit")]
    )
    if has_recipients:
        rows.append(
            [InlineKeyboardButton(text="🗑 Очистить список", callback_data="test_mail:clear")]
        )
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="test_mail:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _menu_text(session, user: User) -> str:
    saved = await _load_saved_recipients(session, user)
    lines = [
        "<b>🧪 Тест маил</b>",
        "",
        "Тема и текст — как в рассылке: <b>случайный оффер</b> из БД, тема = название товара.",
        f"Получателей в списке: <b>{len(saved)}/{MAX_TEST_RECIPIENTS}</b>",
    ]
    if saved:
        lines.append("")
        for i, em in enumerate(saved, 1):
            lines.append(f"{i}. <code>{escape(em)}</code>")
    else:
        lines.append("")
        lines.append(
            "<i>Сначала «Указать получателей» — через запятую или с новой строки, "
            "например:</i>\n<code>you@gmail.com, friend@hotmail.com</code>"
        )
    lines.append("")
    lines.append("<i>Проверяйте Inbox и Спам у получателя.</i>")
    return "\n".join(lines)


async def _show_menu(message: Message, *, edit: bool = False) -> None:
    from services.bot_roles import user_is_admin

    if not await user_is_admin(message.from_user.id):
        return
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == int(message.from_user.id)))
        ).scalars().first()
        if not user:
            return await message.answer("❌ Сначала /start")
        text = await _menu_text(session, user)
        saved = await _load_saved_recipients(session, user)
        kb = _menu_kb(has_recipients=bool(saved))
    if edit and getattr(message, "edit_text", None):
        try:
            return await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "🧪 Тест маил")
async def test_mail_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(message)


@router.callback_query(F.data == "test_mail:close")
async def test_mail_close(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "test_mail:clear")
async def test_mail_clear(call: CallbackQuery, state: FSMContext) -> None:
    from services.bot_roles import user_is_admin

    if not await user_is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == int(call.from_user.id)))
        ).scalars().first()
        if user:
            await _save_recipients(session, user, [])
            await session.commit()
    await call.answer("Список очищен")
    await _show_menu(call.message, edit=True)


@router.callback_query(F.data == "test_mail:edit")
async def test_mail_edit_cb(call: CallbackQuery, state: FSMContext) -> None:
    from services.bot_roles import user_is_admin

    if not await user_is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(TestMailStates.waiting_recipients)
    await call.answer()
    await call.message.answer(
        f"✏️ Введите до <b>{MAX_TEST_RECIPIENTS}</b> email получателей.\n"
        "Через запятую, пробел или с новой строки.\n\n"
        "Пример:\n"
        "<code>test1@gmail.com, test2@hotmail.com</code>\n\n"
        "Отмена: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(TestMailStates.waiting_recipients)
async def test_mail_save_recipients(message: Message, state: FSMContext) -> None:
    from services.bot_roles import user_is_admin

    if not await user_is_admin(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if text in ("-", "cancel") or text.lower() == "отмена":
        await state.clear()
        await message.answer("❌ Отменено.")
        await _show_menu(message)
        return

    emails = _parse_emails(text)
    if not emails:
        return await message.answer(
            "❌ Не найдено ни одного email. Попробуйте снова или отправьте <code>-</code>.",
            parse_mode="HTML",
        )

    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == int(message.from_user.id)))
        ).scalars().first()
        if not user:
            await state.clear()
            return await message.answer("❌ Сначала /start")
        await _save_recipients(session, user, emails)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Сохранено <b>{len(emails)}</b> адрес(ов).\n" + ", ".join(f"<code>{escape(e)}</code>" for e in emails),
        parse_mode="HTML",
    )
    await _show_menu(message)


@router.callback_query(F.data == "test_mail:send")
async def test_mail_send_cb(call: CallbackQuery, state: FSMContext) -> None:
    from services.bot_roles import user_is_admin

    if not await user_is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await call.answer()
    await _run_mass_test(call.message, int(call.from_user.id))


async def _pick_random_offer(session, user_id: int) -> Offer | None:
    return (
        await session.execute(
            select(Offer)
            .where(Offer.user_id == int(user_id))
            .where(Offer.title.is_not(None))
            .where(Offer.title != "")
            .order_by(func.random())
            .limit(1)
        )
    ).scalars().first()


async def _build_test_message(
    session,
    *,
    tg_id: int,
    user: User,
    offer: Offer | None,
) -> tuple[str, str, str]:
    """subject, body, item_title"""
    item_title = offer_effective_title(offer) if offer else ""
    if not item_title:
        row = (
            await session.execute(
                select(Offer.title)
                .where(Offer.user_id == int(user.id))
                .where(Offer.title.is_not(None))
                .order_by(func.random())
                .limit(1)
            )
        ).first()
        item_title = (row[0] if row else "") or "OFFER"

    price = (getattr(offer, "price", "") or "").strip() if offer else ""
    link = (getattr(offer, "link", "") or "").strip() if offer else ""
    image_url = (getattr(offer, "photo", "") or "").strip() if offer else ""

    buyer_name = ((await get_user_setting(session, user, AQUA_PROFILE_NAME_KEY)) or "").strip()
    address = ((await get_user_setting(session, user, AQUA_PROFILE_ADDRESS_KEY)) or "").strip()

    ctx = {
        "ITEM_TITLE": item_title,
        "PRICE": price,
        "BUYER_NAME": buyer_name,
        "ADDRESS": address,
        "IMAGE_URL": image_url,
    }

    base_text = await pick_random_smart_preset(tg_id, item_title)
    if not (base_text or "").strip():
        base_text = await pick_random_first_sms(tg_id, item_title)
    if not (base_text or "").strip():
        base_text = f"Hei! Onko tuote vielä myynnissä? {item_title}".strip()

    body = apply_placeholders(base_text, link=link, ctx=ctx)
    subject = subject_for_offer(item_title)
    return subject, body, item_title


async def _run_mass_test(message: Message, tg_id: int) -> None:
    if bg_is_running(tg_id, "test_mail"):
        return await message.answer("⏳ Тест уже отправляется…")

    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == tg_id))
        ).scalars().first()
        if not user:
            return await message.answer("❌ Сначала /start")
        recipients = await _load_saved_recipients(session, user)
        if not recipients:
            return await message.answer(
                "❌ Список получателей пуст. «🧪 Тест маил» → «Указать получателей»."
            )
        user_id = int(user.id)
        accs = [
            a
            for a in (
                await session.execute(select(EmailAccount).where(EmailAccount.user_id == user_id))
            ).scalars().all()
            if getattr(a, "status", "") == "active"
        ]
        if not accs:
            return await message.answer("❌ Нет активных аккаунтов в «📮 Аккаунты».")

        sender_emails = {_canon_email(a.email) for a in accs}
        targets = [r for r in recipients if r not in sender_emails]
        if not targets:
            return await message.answer(
                "❌ Все получатели совпадают с вашими ящиками отправителя. Укажите внешние email."
            )
        eligible_acc_ids = [int(a.id) for a in accs]

    status = await message.answer(
        f"⏳ Тест на <b>{len(targets)}</b> адрес(ов)…",
        parse_mode="HTML",
    )

    async def _job() -> None:
        ok_n = 0
        fail_lines: list[str] = []
        details: list[str] = []
        acc_ids = list(eligible_acc_ids)

        try:
            for i, to_email in enumerate(targets):
                if i > 0:
                    await asyncio.sleep(TEST_SEND_DELAY_SEC)

                if not acc_ids:
                    fail_lines.append("нет активных ящиков")
                    break

                async with async_session() as session:
                    user = (
                        await session.execute(select(User).where(User.id == user_id))
                    ).scalars().first()
                    if not user:
                        fail_lines.append("пользователь не найден")
                        break
                    acc_row = await session.get(EmailAccount, random.choice(acc_ids))
                    if not acc_row or getattr(acc_row, "status", "") != "active":
                        fail_lines.append("ящик недоступен")
                        break
                    account = acc_row
                    offer = await _pick_random_offer(session, user_id)
                    subject, body, item_title = await _build_test_message(
                        session, tg_id=tg_id, user=user, offer=offer
                    )
                    ok, err, msgid = await send_email_via_account_with_proxy(
                        session,
                        user_id,
                        account,
                        to_email,
                        subject,
                        body,
                    )

                acc_email = account.email
                subj_short = (subject or "")[:50]
                if ok:
                    ok_n += 1
                    details.append(
                        f"✅ <code>{escape(to_email)}</code>\n"
                        f"   от <code>{escape(acc_email)}</code>\n"
                        f"   тема: {escape(subj_short)}"
                    )
                    if i == 0:
                        try:
                            await asyncio.sleep(3)
                            verified, _ = await verify_message_in_sent(
                                account.email,
                                account.password or "",
                                subject=subject,
                                to_email=to_email,
                                message_id=msgid,
                            )
                            if verified:
                                details.append("   <i>Копия в «Отправленных» отправителя: да</i>")
                        except Exception:
                            pass
                    async with async_session() as session2:
                        raw_link = await pick_random_raw_link(session2)
                        if raw_link:
                            test_offer = Offer(
                                user_id=user_id,
                                title=item_title[:200] or "TEST",
                                link=raw_link,
                                price=(getattr(offer, "price", None) or "1") if offer else "1",
                                photo=getattr(offer, "photo", None) if offer else None,
                                person_name="TEST",
                            )
                            session2.add(test_offer)
                            await session2.flush()
                            session2.add(OfferEmail(offer_id=test_offer.id, email=to_email))
                            await session2.commit()
                else:
                    err_s = err or "unknown"
                    fail_lines.append(f"<code>{escape(to_email)}</code>: {escape(err_s[:120])}")
                    if is_smtp_account_block_error(err_s):
                        async with async_session() as session_blk:
                            await mark_account_smtp_blocked(
                                session_blk,
                                account,
                                err_s,
                                db_user_id=user_id,
                                bot=message.bot,
                                chat_id=int(message.chat.id),
                            )
                        acc_ids = [aid for aid in acc_ids if aid != int(account.id)]

            summary = (
                f"<b>🧪 Тест завершён</b>\n\n"
                f"Успешно: <b>{ok_n}/{len(targets)}</b>\n\n"
                + "\n".join(details[:8])
            )
            if fail_lines:
                summary += "\n\n<b>Ошибки:</b>\n" + "\n".join(fail_lines[:6])
            summary += (
                "\n\n<i>Смотрите Inbox и Спам. Тема без «Test message» — как в рассылке.</i>"
            )
            await status.edit_text(summary, parse_mode="HTML")
        except Exception as e:
            await status.edit_text(f"❌ Ошибка теста: {escape(str(e))}", parse_mode="HTML")

    if not bg_start(tg_id, "test_mail", _job()):
        await message.answer("⏳ Тест уже отправляется…")


def _is_valid_ad_link(url: str) -> bool:
    if not url:
        return False
    u = url.lower().strip()
    if not u.startswith(("http://", "https://")):
        return False
    if "tori.fi" in u or "posti.fi" in u:
        return True
    if "tutti.ch" in u:
        return True
    if "kleinanzeigen.de" in u:
        return True
    if "ebay." in u and ".de" in u:
        return True
    return False


async def pick_random_raw_link(session):
    row = (
        await session.execute(
            select(Offer.link).where(Offer.link.is_not(None)).order_by(func.random()).limit(50)
        )
    ).all()
    for (candidate,) in row:
        if _is_valid_ad_link(candidate):
            return candidate

    p = pathlib.Path("data/test_links.txt")
    if not p.exists():
        return None
    links = [
        x.strip()
        for x in p.read_text(encoding="utf-8").splitlines()
        if x.strip() and _is_valid_ad_link(x.strip())
    ]
    return random.choice(links) if links else None


@router.message(Command("preview_imap"))
async def preview_imap_card(message: Message) -> None:
    """Демо-карточка входящего письма (тот же UI, что у IMAP)."""
    from services.incoming_mail_worker import build_kb, render_mail_text_chunks

    async with async_session() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_id == int(message.from_user.id)).limit(1)
            )
        ).scalars().first()
        if not user:
            return await message.answer("Сначала /start")

        acc = (
            await session.execute(
                select(EmailAccount)
                .where(EmailAccount.user_id == int(user.id))
                .where(EmailAccount.status.in_(["active", "enabled"]))
                .limit(1)
            )
        ).scalars().first()
        inbox = (getattr(user, "sender_name", None) or "").strip() or "Demo User"
        account_email = (getattr(acc, "email", None) or "demo@gmail.com") if acc else "demo@gmail.com"

    demo_body = (
        "Sie haben wohl eine falsche Mail adresse, ich habe nichts zum Verkauf ausgeschrieben\n\n"
        "--------\n"
        "Gesendet: Mittwoch, 25. März 2026 um 14:45\n"
        "Von: Maria Johansen <demo@gmail.com>\n"
    )
    chunks = render_mail_text_chunks(
        account_email=account_email,
        inbox_label=inbox,
        from_name="lara.wolf",
        from_email="lara.wolf@gmx.ch",
        subject="Aw: Johann Jakob Couchtisch, Messing-Glas",
        body=demo_body,
        offer_id=12345,
        link_id="210743034",
        service_label="tori.fi",
        product_title="Johann Jakob Couchtisch, Messing-Glas",
    )
    kb = build_kb(0, "preview", mail_id=None)
    await message.answer(
        "ℹ️ <b>Демо-карточка</b> (не реальное IMAP-письмо).",
        parse_mode="HTML",
    )
    await message.answer(chunks[0], reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
