#!/usr/bin/env python3
"""Проверка оффера в БД (Railway Postgres или локальный .env).

  python scripts/check_offer_in_db.py
  python scripts/check_offer_in_db.py --email jafettesfazghi@gmail.com --title "Baby milo"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import func, or_, select

from database import DATABASE_URL, database_url_for_logs
from models import Offer, OfferEmail
from services.offer_storage import offer_effective_link, offer_effective_title, parse_offer_raw


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="jafettesfazghi@gmail.com")
    ap.add_argument("--title", default="Baby milo")
    args = ap.parse_args()

    email_q = (args.email or "").strip().lower()
    title_q = (args.title or "").strip().lower()

    print("DATABASE:", database_url_for_logs(DATABASE_URL))
    if not DATABASE_URL or "sqlite" in DATABASE_URL.lower():
        print(
            "\n[!] Ne Postgres Railway — lokalny sqlite ili pusto.\n"
            "    Skopiruj DATABASE_URL iz Railway -> Postgres -> Connect\n"
            "    i zapusti:\n"
            '    $env:DATABASE_URL="postgresql://..."; python scripts/check_offer_in_db.py\n'
        )

    from database import Session
    from sqlalchemy import text

    async with Session() as session:
        try:
            await session.execute(text("SELECT 1 FROM offers LIMIT 1"))
        except Exception as e:
            print(f"\nDB error (empty DB or no migrations): {e}")
            return
        # По email продавца
        if email_q:
            rows = (
                await session.execute(
                    select(Offer, OfferEmail.email)
                    .join(OfferEmail, OfferEmail.offer_id == Offer.id)
                    .where(func.lower(OfferEmail.email) == email_q)
                    .order_by(Offer.id.desc())
                    .limit(50)
                )
            ).all()
            print(f"\n=== Офферы с email {email_q!r}: {len(rows)} ===")
            for off, em in rows:
                _print_offer(off, em)

        # По названию (частичное)
        if title_q:
            pat = f"%{title_q}%"
            rows2 = (
                await session.execute(
                    select(Offer)
                    .where(
                        or_(
                            func.lower(Offer.title).like(pat),
                            func.lower(Offer.raw_json).like(pat),
                        )
                    )
                    .order_by(Offer.id.desc())
                    .limit(30)
                )
            ).scalars().all()
            print(f"\n=== Офферы с названием ~{title_q!r}: {len(rows2)} ===")
            for off in rows2:
                emails = (
                    await session.execute(
                        select(OfferEmail.email).where(OfferEmail.offer_id == off.id)
                    )
                ).scalars().all()
                _print_offer(off, ", ".join(emails) if emails else "—")

        # Incoming mail hint
        try:
            from models import IncomingMail

            mails = (
                await session.execute(
                    select(IncomingMail)
                    .where(func.lower(IncomingMail.from_email) == email_q)
                    .order_by(IncomingMail.id.desc())
                    .limit(5)
                )
            ).scalars().all()
            if mails:
                print(f"\n=== Последние письма от {email_q}: {len(mails)} ===")
                for m in mails:
                    print(
                        f"  mail id={m.id} subj={m.subject!r} "
                        f"resolved_offer_id={getattr(m, 'resolved_offer_id', None)} "
                        f"ad_url={(getattr(m, 'ad_url', '') or '')[:80]}"
                    )
        except Exception as e:
            print(f"\n(incoming_mail: {e})")


def _print_offer(off: Offer, emails: str) -> None:
    title = offer_effective_title(off)
    link = offer_effective_link(off)
    raw = parse_offer_raw(getattr(off, "raw_json", None))
    item_link = (raw.get("item_link") or raw.get("link") or "") if raw else ""
    print(f"\n  offer id={off.id} user_id={off.user_id}")
    print(f"  title: {title!r}")
    print(f"  Offer.link: {(off.link or '')[:100]!r}")
    print(f"  raw item_link: {str(item_link)[:100]!r}")
    print(f"  effective_link: {link[:100]!r}" if link else "  effective_link: (нет)")
    print(f"  emails: {emails}")
    ok = bool(link) and ("tori.fi" in link.lower() or "posti" in link.lower())
    print(f"  OK for AQUA: {'yes' if ok else 'NO — no tori/posti link'}")


if __name__ == "__main__":
    asyncio.run(main())
