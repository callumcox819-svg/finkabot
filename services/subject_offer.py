"""Глобальная тема письма с подстановкой OFFER (название товара)."""

from __future__ import annotations

import re

from config import config


def sanitize_email_subject(text: str) -> str:
    """Тема письма — одна строка без \\n (иначе SMTP: HeaderWriteError)."""
    s = (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


SUBJECT_TEMPLATE_SETTING = "subject_template"

# Готовые шаблоны (⚙️ → Темы): OFFER = название объявления (фин. текст в теме)
MAILING_SUBJECT_PRESETS: tuple[tuple[str, str], ...] = (
    ("osto_tuote", "Re: Tuotteen ostaminen OFFER"),
    ("kysymys", "Kysymys: OFFER"),
    ("tuote", "Tuote: OFFER"),
    ("osto", "Osto: OFFER"),
    ("viesti", "Viesti – OFFER"),
    ("re_offer", "Re: OFFER"),
    ("plain", "OFFER"),
)


def global_subject_template() -> str:
    tpl = (getattr(config, "GLOBAL_SUBJECT_TEMPLATE", None) or "Kysymys: OFFER").strip()
    return tpl or "Kysymys: OFFER"


async def resolve_mailing_subject_template(session, user) -> str:
    """Шаблон темы для /send: сначала ⚙️ Темы, иначе GLOBAL_SUBJECT_TEMPLATE (Railway)."""
    from services.user_settings import get_user_setting

    custom = (await get_user_setting(session, user, SUBJECT_TEMPLATE_SETTING) or "").strip()
    return custom or global_subject_template()


def render_subject_with_offer(subject_template: str, offer_title: str) -> str:
    tpl = sanitize_email_subject((subject_template or "").strip() or global_subject_template())
    offer_value = sanitize_email_subject((offer_title or "").strip() or "OFFER")
    out = tpl.replace("{{OFFER}}", offer_value).replace("OFFER", offer_value).strip()
    if not out:
        out = offer_value
    out = sanitize_email_subject(out)
    if len(out) > 140:
        out = out[:137] + "…"
    return out


def subject_for_offer(offer_title: str, *, template: str | None = None) -> str:
    tpl = (template or "").strip() or global_subject_template()
    return render_subject_with_offer(tpl, offer_title)
