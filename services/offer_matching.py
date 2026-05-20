"""Поиск Offer по email, названию, цене, ссылке и полному JSON."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_ as sa_or, select as sa_select

from models import ConversationLink, Offer, OfferEmail
from services.offer_storage import link_key, parse_offer_raw

_PRICE_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _ratio(a: str, b: str) -> float:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


_SUBJECT_EDGE_QUOTES_RE = re.compile(r'^["\'\s]+|["\'\s]+$', re.UNICODE)


def _strip_subject_edges(s: str) -> str:
    t = (s or "").strip()
    for _ in range(3):
        n = _SUBJECT_EDGE_QUOTES_RE.sub("", t).strip()
        if n == t:
            break
        t = n
    return t


def _norm_subject(subject: str) -> str:
    s = _strip_subject_edges((subject or "").strip())
    if not s:
        return ""
    return re.sub(r"^(re|aw|fw|fwd)\s*:\s*", "", s, flags=re.I).strip()


def product_title_from_subject(subject: str) -> str:
    """Название из темы (Re: Tuote …) — если оффер в БД привязан к другому лоту."""
    subj = _norm_subject(subject)
    if len(subj) > 140:
        subj = subj[:137] + "…"
    return subj


def offer_display_title(subject: str, offer: Offer | None) -> str:
    """Товар в карточке: тема письма, если оффер из БД не совпадает с Re:."""
    from services.offer_storage import offer_effective_title

    subj_t = product_title_from_subject(subject)
    if not offer:
        return subj_t or (subject or "").strip()
    ot = (offer_effective_title(offer) or "").strip()
    if subject_is_informative(subject):
        sm = subject_match_score(subject, offer)
        if sm >= _SUBJECT_EMAIL_AGREE_MIN_SCORE:
            return ot or subj_t
        return subj_t or ot
    return ot or subj_t


# Минимум совпадения темы с лотом из conversation_links (старый диалог)
_CONV_AD_URL_MIN_SUBJECT_SCORE = 40.0
# Тема Re: и email продавца — лот только при явном совпадении названия
_SUBJECT_EMAIL_AGREE_MIN_SCORE = 40.0


def _price_token(price: str) -> str:
    m = _PRICE_NUM_RE.search((price or "").replace(" ", ""))
    if not m:
        return ""
    return m.group(1).replace(",", ".")


def _canon_email(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.split("@", 1)
    local = local.strip()
    domain = domain.strip().lower()
    if "+" in local:
        local = local.split("+", 1)[0]
    if domain in ("googlemail.com", "gmail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def canon_seller_email(email: str) -> str:
    return _canon_email(email)


_SUBJECT_STOP = frozenset(
    {
        "re",
        "aw",
        "fw",
        "fwd",
        "the",
        "und",
        "der",
        "die",
        "das",
        "for",
        "von",
        "from",
    }
)


def _subject_tokens(subj: str) -> list[str]:
    parts = re.findall(r"[a-z0-9]{3,}", (subj or "").lower())
    return [p for p in parts if p not in _SUBJECT_STOP]


def score_offer(
    off: Offer,
    *,
    from_email: str = "",
    subject: str = "",
    from_name: str = "",
    body_text: str = "",
    email_hit: bool = False,
) -> float:
    score = 0.0
    subj = _norm_subject(subject)
    fn = (from_name or "").strip()
    body = (body_text or "").strip()
    body_l = body.lower()

    if email_hit:
        score += 120.0

    title = (off.title or "").strip()
    if subj and title:
        score += 90.0 * _ratio(subj, title)
        if subj.lower() in title.lower() or title.lower() in subj.lower():
            score += 15.0

    pname = (off.person_name or "").strip()
    if fn and pname:
        score += 45.0 * _ratio(fn, pname)
        if fn.lower() in pname.lower() or pname.lower() in fn.lower():
            score += 10.0

    price_tok = _price_token(off.price or "")
    if price_tok and price_tok in body.replace(" ", "").replace(",", "."):
        score += 35.0
    if price_tok and subj and price_tok in subj.replace(" ", ""):
        score += 20.0

    link = (off.link or "").strip()
    if link and link in body:
        score += 55.0
    lk = link_key(link)
    if lk and lk in body_l:
        score += 40.0

    raw = parse_offer_raw(getattr(off, "raw_json", None))
    if raw:
        loc = str(raw.get("location") or "").strip()
        if loc and len(loc) >= 3 and loc.lower() in body_l:
            score += 25.0
        raw_title = str(raw.get("item_title") or raw.get("title") or "").strip()
        if subj and raw_title:
            score += 30.0 * _ratio(subj, raw_title)
        raw_name = str(raw.get("item_person_name") or raw.get("person_name") or "").strip()
        if fn and raw_name:
            score += 25.0 * _ratio(fn, raw_name)
        score += _score_raw_json_fields(raw, subj=subj, fn=fn, body_l=body_l)

    return score


_RAW_SKIP_KEYS = frozenset(
    {"validated_emails", "offer_id", "item_photo", "photo", "image", "img", "email"}
)
_RAW_FIELD_WEIGHT: dict[str, float] = {
    "item_title": 28.0,
    "title": 28.0,
    "item_price": 22.0,
    "price": 22.0,
    "item_person_name": 20.0,
    "person_name": 20.0,
    "name": 18.0,
    "item_desc": 18.0,
    "location": 16.0,
    "item_link": 30.0,
    "link": 30.0,
    "person_link": 14.0,
    "phone": 25.0,
    "gender": 8.0,
}


def _score_raw_json_fields(
    raw: dict[str, Any],
    *,
    subj: str,
    fn: str,
    body_l: str,
) -> float:
    """Доп. баллы, если значения из парсера встречаются в письме."""
    hay = f"{subj} {fn} {body_l}".lower()
    extra = 0.0
    seen_vals: set[str] = set()
    for key, val in raw.items():
        if key in _RAW_SKIP_KEYS or val is None:
            continue
        if isinstance(val, (int, float)):
            s = str(val).strip()
        elif isinstance(val, str):
            s = val.strip()
        else:
            continue
        if len(s) < 3:
            continue
        sl = s.lower()
        if sl in seen_vals:
            continue
        seen_vals.add(sl)
        w = _RAW_FIELD_WEIGHT.get(str(key), 10.0)
        if sl in body_l or sl in hay:
            extra += w
            continue
        if key in ("item_link", "link", "person_link"):
            lk = link_key(s)
            if lk and lk in body_l:
                extra += w
    return extra


async def resolve_offer_for_incoming(
    session,
    *,
    user_id: int,
    from_email: str,
    subject: str,
    from_name: str,
    body_text: str = "",
) -> tuple[int | None, int | None]:
    """Найти Offer: сначала email, затем скоринг по всем полям."""
    fe_raw = (from_email or "").strip().lower()
    fe_can = _canon_email(fe_raw)

    email_pairs: list[tuple[OfferEmail, Offer]] = []
    q = (
        sa_select(OfferEmail, Offer)
        .join(Offer, Offer.id == OfferEmail.offer_id)
        .where(Offer.user_id == int(user_id))
    )
    conds = []
    if fe_raw:
        conds.append(func.lower(OfferEmail.email) == fe_raw)
    if fe_can and "@" in fe_can:
        local_can, domain_can = fe_can.split("@", 1)
        if domain_can in ("gmail.com", "googlemail.com"):
            conds.append(func.replace(func.lower(OfferEmail.email), ".", "") == fe_can.replace(".", ""))
        if local_can:
            conds.append(func.lower(OfferEmail.email).like(local_can + "@%"))
    if conds:
        email_pairs = (
            await session.execute(q.where(sa_or(*conds)).order_by(Offer.id.desc()).limit(80))
        ).all()

    if not email_pairs and fe_can:
        all_rows = (
            await session.execute(
                sa_select(OfferEmail, Offer)
                .join(Offer, Offer.id == OfferEmail.offer_id)
                .where(Offer.user_id == int(user_id))
                .order_by(Offer.id.desc())
                .limit(1200)
            )
        ).all()
        for oe, off in all_rows:
            if _canon_email((oe.email or "").strip().lower()) == fe_can:
                email_pairs.append((oe, off))
                break

    candidates: dict[int, tuple[Offer, OfferEmail | None, bool]] = {}
    for oe, off in email_pairs:
        candidates[int(off.id)] = (off, oe, True)

    # Всегда добавляем свежие офферы для матча по title/price/link/raw
    recent = (
        await session.execute(
            sa_select(Offer)
            .where(Offer.user_id == int(user_id))
            .order_by(Offer.id.desc())
            .limit(500)
        )
    ).scalars().all()
    for off in recent:
        oid = int(off.id)
        if oid not in candidates:
            candidates[oid] = (off, None, False)

    if not candidates:
        return None, None

    subj_strong = subject_is_informative(subject)

    # Re: … — email продавца не должен тянуть чужой лот (Ikea vs Baby milo).
    if subj_strong and email_pairs:
        from services.offer_storage import offer_effective_link

        best_sm = -1.0
        best_off: Offer | None = None
        best_oe: OfferEmail | None = None
        for oe, off in email_pairs:
            if not offer_effective_link(off):
                continue
            sm = subject_match_score(subject, off)
            if sm > best_sm:
                best_sm = sm
                best_off = off
                best_oe = oe
        if best_off and best_sm >= _SUBJECT_EMAIL_AGREE_MIN_SCORE:
            return int(best_off.id), int(best_oe.id) if best_oe else None

    best_offer_id: int | None = None
    best_email_id: int | None = None
    best_score = -1.0

    for off, oe, email_hit in candidates.values():
        sc = score_offer(
            off,
            from_email=from_email,
            subject=subject,
            from_name=from_name,
            body_text=body_text,
            email_hit=email_hit,
        )
        if subj_strong and email_hit:
            sm = subject_match_score(subject, off)
            if sm < _SUBJECT_EMAIL_AGREE_MIN_SCORE:
                sc -= 200.0
            elif sm >= 70.0:
                sc += 40.0
        if sc > best_score:
            best_score = sc
            best_offer_id = int(off.id)
            best_email_id = int(oe.id) if oe else None

    min_score = 55.0 if subj_strong else (45.0 if not email_pairs else 35.0)
    if best_offer_id is not None and best_score >= min_score:
        return best_offer_id, best_email_id
    return None, None


def subject_is_informative(subject: str) -> bool:
    subj = _norm_subject(subject)
    return len(subj) >= 8 or len(_subject_tokens(subj)) >= 2


def subject_token_hits(subject: str, off: Offer) -> int:
    from services.offer_storage import offer_effective_title

    title_l = offer_effective_title(off).lower()
    if not title_l:
        return 0
    return sum(1 for tok in _subject_tokens(subject) if tok in title_l)


def subject_match_score(subject: str, off: Offer) -> float:
    """Сильный матч темы письма к названию оффера (для продавцов с несколькими лотами)."""
    from services.offer_storage import offer_effective_title

    subj = _norm_subject(subject)
    if len(subj) < 6:
        return 0.0
    title = offer_effective_title(off).lower()
    if not title:
        return 0.0

    score = 75.0 * _ratio(subj, title)
    if subj.lower() in title or title in subj.lower():
        score += 55.0

    subj_l = subj.lower()
    title_l = title.lower()
    tok_hits = 0
    for tok in _subject_tokens(subj):
        if tok in title_l:
            tok_hits += 1
            score += 24.0
    if tok_hits >= 2:
        score += 25.0 + tok_hits * 12.0
    if tok_hits >= 3:
        score += 40.0

    wants_set = any(w in subj_l for w in ("komplette", "complet", "complete", "set "))
    if wants_set:
        if any(w in title_l for w in ("komplette", "complet", "complete", "set")):
            score += 45.0
        if any(w in title_l for w in ("sticker", "valverde", "extra sticker")) and not any(
            w in title_l for w in ("komplette", "complet", "set")
        ):
            score -= 50.0

    return score


async def finalize_aqua_listing_context(
    session,
    *,
    user_id: int,
    listing_url: str,
    offer: Offer | None,
    subject: str = "",
) -> tuple[Offer | None, str, str, str | None, str | None]:
    """url + title + price + photo из одного Offer; иначе тема письма."""
    from services.offer_storage import (
        find_offer_by_link,
        offer_effective_photo,
        offer_effective_price,
        offer_effective_title,
    )

    url = (listing_url or "").strip()
    off_url = (
        await find_offer_by_link(session, user_id=int(user_id), ad_url=url) if url else None
    )
    if off_url and offer and subject_is_informative(subject):
        if subject_match_score(subject, off_url) >= _CONV_AD_URL_MIN_SUBJECT_SCORE:
            offer = off_url
    elif off_url and offer is None:
        offer = off_url
    elif off_url and not subject_is_informative(subject):
        offer = off_url

    subj_t = product_title_from_subject(subject)
    title = ""
    price = image = None

    if offer:
        ot = (offer_effective_title(offer) or "").strip()
        if subject_is_informative(subject):
            sm = subject_match_score(subject, offer)
            if sm < _CONV_AD_URL_MIN_SUBJECT_SCORE and subj_t:
                title = subj_t
            else:
                title = ot or subj_t
        else:
            title = ot or subj_t
        price = offer_effective_price(offer) or None
        image = offer_effective_photo(offer) or None
    else:
        title = subj_t

    if not (title or "").strip():
        title = subj_t or (subject or "").strip() or "OFFER"

    return offer, url, title.strip(), price, image


_AQUA_SUBJECT_MIN_SCORE = 28.0
_AQUA_MULTI_SUBJECT_GAP = 12.0


def _aqua_offer_pair(
    subj: str,
    off: Offer | None,
    *,
    min_score: float | None,
) -> tuple[Offer, str] | None:
    from services.offer_storage import offer_effective_link

    if not off:
        return None
    link = offer_effective_link(off)
    if not link:
        return None
    if min_score is not None and subj and subject_is_informative(subj):
        if subject_match_score(subj, off) < min_score:
            return None
    return off, link


def _pick_best_linked_by_subject(
    offers: list[Offer],
    *,
    subject: str,
    min_score: float,
    min_gap: float = _AQUA_MULTI_SUBJECT_GAP,
) -> Offer | None:
    from services.offer_storage import offer_effective_link

    if not offers or not subject_is_informative(subject):
        return None

    ranked: list[tuple[Offer, float]] = []
    for off in offers:
        link = offer_effective_link(off)
        if not link:
            continue
        sc = subject_match_score(subject, off)
        if sc > 0:
            ranked.append((off, sc))
    if not ranked:
        return None

    ranked.sort(key=lambda x: x[1], reverse=True)
    best, best_sc = ranked[0]
    if best_sc < min_score:
        return None
    if len(ranked) == 1:
        return best
    _, second_sc = ranked[1]
    if best_sc - second_sc >= min_gap or best_sc >= min_score + 18:
        return best
    return None


async def _load_offer(
    session,
    *,
    user_id: int,
    offer_id: int,
) -> Offer | None:
    return (
        await session.execute(
            sa_select(Offer)
            .where(Offer.id == int(offer_id))
            .where(Offer.user_id == int(user_id))
            .limit(1)
        )
    ).scalars().first()


async def _load_conversation_link(
    session,
    *,
    user_id: int,
    inbox_email: str,
    contact_email: str,
) -> ConversationLink | None:
    inbox = _canon_email(inbox_email)
    contact = _canon_email(contact_email)
    if not inbox or not contact:
        return None
    return (
        await session.execute(
            sa_select(ConversationLink)
            .where(ConversationLink.user_id == int(user_id))
            .where(func.lower(ConversationLink.account_email) == inbox)
            .where(func.lower(ConversationLink.from_email) == contact)
            .limit(1)
        )
    ).scalars().first()


async def resolve_listing_for_incoming_mail(
    session,
    *,
    user_id: int,
    from_email: str,
    subject: str,
    from_name: str = "",
    body_text: str = "",
    resolved_offer_id: int | None = None,
    mail_ad_url: str | None = None,
    inbox_email: str | None = None,
    pinned_offer_id: int | None = None,
    conv_ad_url: str | None = None,
) -> tuple[Offer | None, str]:
    """
    Единый выбор лота для карточки, IMAP и «Создать ссылку».
    Приоритет: тема письма → привязка письма → закреплённый лот диалога → conv (с проверкой темы).
    """
    from services.offer_storage import ensure_offer_link_column, find_offer_by_link, offer_effective_link

    subj = _strip_subject_edges((subject or "").strip())
    fe = (from_email or "").strip()

    if not (pinned_offer_id or conv_ad_url) and (inbox_email or "").strip() and fe:
        conv = await _load_conversation_link(
            session,
            user_id=int(user_id),
            inbox_email=inbox_email or "",
            contact_email=fe,
        )
        if conv:
            if pinned_offer_id is None and getattr(conv, "pinned_offer_id", None):
                pinned_offer_id = int(conv.pinned_offer_id)
            if not conv_ad_url:
                conv_ad_url = (conv.ad_url or "").strip() or None

    def _ret(off: Offer | None) -> tuple[Offer | None, str]:
        if not off:
            return None, ""
        link = offer_effective_link(off)
        if not link:
            return None, ""
        ensure_offer_link_column(off, link)
        return off, link

    if subject_is_informative(subj):
        recent = (
            await session.execute(
                sa_select(Offer)
                .where(Offer.user_id == int(user_id))
                .order_by(Offer.id.desc())
                .limit(800)
            )
        ).scalars().all()
        off = _pick_best_linked_by_subject(
            list(recent),
            subject=subj,
            min_score=34.0,
            min_gap=16.0,
        )
        if off:
            return _ret(off)

        off = await resolve_best_offer_by_subject_global(
            session,
            user_id=int(user_id),
            subject=subj,
            from_name=from_name,
            body_text=body_text,
        )
        pair = _aqua_offer_pair(subj, off, min_score=_AQUA_SUBJECT_MIN_SCORE)
        if pair:
            return pair

        seller_offers = await list_offers_for_seller_email(
            session, user_id=int(user_id), from_email=fe
        )
        multi = len(seller_offers) > 1
        off = _pick_best_linked_by_subject(
            seller_offers,
            subject=subj,
            min_score=_SUBJECT_EMAIL_AGREE_MIN_SCORE,
            min_gap=14.0 if multi else 8.0,
        )
        if off:
            return _ret(off)

        off = _pick_best_offer_by_subject_scores(
            seller_offers,
            subject=subj,
            from_name=from_name,
            body_text=body_text,
            min_score=50.0 if multi else _SUBJECT_EMAIL_AGREE_MIN_SCORE,
        )
        pair = _aqua_offer_pair(subj, off, min_score=_SUBJECT_EMAIL_AGREE_MIN_SCORE)
        if pair:
            return pair

    murl = (mail_ad_url or "").strip()
    if murl:
        off = await find_offer_by_link(session, user_id=int(user_id), ad_url=murl)
        min_sc = _CONV_AD_URL_MIN_SUBJECT_SCORE if subject_is_informative(subj) else None
        pair = _aqua_offer_pair(subj, off, min_score=min_sc)
        if pair:
            return pair
        if off and not subject_is_informative(subj):
            link = offer_effective_link(off) or murl
            if link:
                ensure_offer_link_column(off, link)
                return off, link

    if resolved_offer_id:
        off = await _load_offer(session, user_id=int(user_id), offer_id=int(resolved_offer_id))
        min_sc = _SUBJECT_EMAIL_AGREE_MIN_SCORE if subject_is_informative(subj) else None
        pair = _aqua_offer_pair(subj, off, min_score=min_sc)
        if pair:
            return pair

    if pinned_offer_id:
        off = await _load_offer(session, user_id=int(user_id), offer_id=int(pinned_offer_id))
        min_sc = _SUBJECT_EMAIL_AGREE_MIN_SCORE if subject_is_informative(subj) else None
        pair = _aqua_offer_pair(subj, off, min_score=min_sc)
        if pair:
            return pair

    curl = (conv_ad_url or "").strip()
    if curl:
        off = await find_offer_by_link(session, user_id=int(user_id), ad_url=curl)
        if off:
            if not subject_is_informative(subj):
                return _ret(off)
            if subject_match_score(subj, off) >= _CONV_AD_URL_MIN_SUBJECT_SCORE:
                return _ret(off)

    oid, _ = await resolve_offer_for_incoming(
        session,
        user_id=int(user_id),
        from_email=fe,
        subject=subj,
        from_name=from_name,
        body_text=body_text,
    )
    if oid:
        off = await _load_offer(session, user_id=int(user_id), offer_id=int(oid))
        pair = _aqua_offer_pair(
            subj,
            off,
            min_score=_AQUA_SUBJECT_MIN_SCORE if subject_is_informative(subj) else None,
        )
        if pair:
            return pair

    if not subject_is_informative(subj):
        seller_offers = await list_offers_for_seller_email(
            session, user_id=int(user_id), from_email=fe
        )
        if len(seller_offers) == 1:
            return _ret(seller_offers[0])

    return None, ""


async def resolve_offer_for_aqua_link(
    session,
    *,
    user_id: int,
    from_email: str,
    subject: str,
    from_name: str = "",
    body_text: str = "",
    resolved_offer_id: int | None = None,
    mail_ad_url: str | None = None,
    inbox_email: str | None = None,
) -> tuple[Offer | None, str]:
    """Кнопка «Создать ссылку» — тот же лот, что карточка письма."""
    return await resolve_listing_for_incoming_mail(
        session,
        user_id=int(user_id),
        from_email=from_email,
        subject=subject,
        from_name=from_name,
        body_text=body_text,
        resolved_offer_id=resolved_offer_id,
        mail_ad_url=mail_ad_url,
        inbox_email=inbox_email,
    )


async def list_offers_for_seller_email(
    session,
    *,
    user_id: int,
    from_email: str,
) -> list[Offer]:
    fe_raw = (from_email or "").strip().lower()
    fe_can = _canon_email(fe_raw)
    if not fe_can:
        return []

    conds = []
    if fe_raw:
        conds.append(func.lower(OfferEmail.email) == fe_raw)
    if fe_can and "@" in fe_can:
        conds.append(func.lower(OfferEmail.email) == fe_can)
        local_can, domain_can = fe_can.split("@", 1)
        if domain_can in ("gmail.com", "googlemail.com"):
            conds.append(
                func.replace(func.lower(OfferEmail.email), ".", "") == fe_can.replace(".", "")
            )

    if not conds:
        return []

    rows = (
        await session.execute(
            sa_select(Offer)
            .join(OfferEmail, OfferEmail.offer_id == Offer.id)
            .where(Offer.user_id == int(user_id))
            .where(sa_or(*conds))
            .order_by(Offer.id.desc())
        )
    ).scalars().all()

    seen: set[int] = set()
    out: list[Offer] = []
    for off in rows:
        oid = int(off.id)
        if oid in seen:
            continue
        seen.add(oid)
        out.append(off)
    return out


def _pick_best_offer_by_subject_scores(
    offers: list[Offer],
    *,
    subject: str,
    from_name: str = "",
    body_text: str = "",
    min_score: float,
) -> Offer | None:
    if not offers or not subject_is_informative(subject):
        return None

    best: Offer | None = None
    best_sc = -1.0
    for off in offers:
        sc = subject_match_score(subject, off)
        sc += (
            score_offer(
                off,
                subject=subject,
                from_name=from_name,
                body_text=body_text,
                email_hit=False,
            )
            * 0.3
        )
        if sc > best_sc:
            best_sc = sc
            best = off

    if best is None:
        return None
    if best_sc >= min_score:
        return best
    if subject_token_hits(subject, best) >= 3 and best_sc >= 48.0:
        return best
    return None


async def resolve_best_offer_by_subject(
    session,
    *,
    user_id: int,
    from_email: str,
    subject: str,
    from_name: str = "",
    body_text: str = "",
) -> Offer | None:
    offers = await list_offers_for_seller_email(session, user_id=int(user_id), from_email=from_email)
    if not offers:
        return None

    multi = len(offers) > 1
    return _pick_best_offer_by_subject_scores(
        offers,
        subject=subject,
        from_name=from_name,
        body_text=body_text,
        min_score=58.0 if multi else 42.0,
    )


async def resolve_best_offer_by_subject_global(
    session,
    *,
    user_id: int,
    subject: str,
    from_name: str = "",
    body_text: str = "",
) -> Offer | None:
    """Если email привязан к другому лоту — ищем оффер по теме среди всех объявлений пользователя."""
    if not subject_is_informative(subject):
        return None

    recent = (
        await session.execute(
            sa_select(Offer)
            .where(Offer.user_id == int(user_id))
            .order_by(Offer.id.desc())
            .limit(800)
        )
    ).scalars().all()
    return _pick_best_offer_by_subject_scores(
        list(recent),
        subject=subject,
        from_name=from_name,
        body_text=body_text,
        min_score=62.0,
    )
