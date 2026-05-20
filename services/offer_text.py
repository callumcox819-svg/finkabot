"""Подстановка названия товара (OFFER) в текст письма."""

from __future__ import annotations


def apply_offer_to_text(text: str, offer_title: str) -> str:
    """OFFER / {{OFFER}} / \"OFFER\" → название объявления."""
    txt = text or ""
    title = (offer_title or "").strip()
    if not title:
        return txt
    for needle in ('{{OFFER}}', '"OFFER"', "'OFFER'", "«OFFER»", "OFFER"):
        txt = txt.replace(needle, title)
    return txt


def trim_trailing_offer_title(body: str, offer_title: str) -> str:
    """Убрать дубль названия в конце тела, если оно уже в теме (OFFER в конце пресета)."""
    b = (body or "").rstrip()
    t = (offer_title or "").strip()
    if not b or not t or len(t) < 3:
        return body
    if b.endswith(t):
        b = b[: -len(t)].rstrip()
    return b
