"""Извлечение одного адреса для SMTP (не цитата из тела письма)."""

from __future__ import annotations

import re

# Один адрес; не жадный — не съедаем хвост цитаты.
_ADDR_RE = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}"
)


def extract_email_address(raw: str) -> str:
    """
    Вернуть первый email из строки.
    Пусто, если в тексте нет адреса или это явно не адрес (многострочная цитата).
    """
    s = (raw or "").strip()
    if not s:
        return ""

    if "\n" in s or "\r" in s or len(s) > 120:
        m = _ADDR_RE.search(s)
        return (m.group(0) if m else "").strip().lower()

    bracket = re.search(r"<([^>@\s]+@[^>]+)>", s)
    if bracket:
        s = bracket.group(1).strip()

    s = s.strip().strip("<>").lower()
    if _ADDR_RE.fullmatch(s):
        return s

    m = _ADDR_RE.search(s)
    return (m.group(0) if m else "").strip().lower()


def is_valid_smtp_recipient(email: str) -> bool:
    em = extract_email_address(email)
    return bool(em) and "@" in em and "\n" not in em and "\r" not in em and len(em) <= 120
