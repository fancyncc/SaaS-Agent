from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import get_settings
from backend.models import MailDelivery


async def send_account_link(email: str, purpose: str, url: str, *, session: AsyncSession | None = None) -> None:
    if get_settings().mail_debug:
        return
    if session is not None:
        session.add(MailDelivery(email=email, purpose=purpose, body=url))
        return
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as own_session:
            own_session.add(MailDelivery(email=email, purpose=purpose, body=url))
            await own_session.commit()
    finally:
        await engine.dispose()


def smtp_send(item: MailDelivery) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = item.email
    message["Subject"] = f"SaaS Implementation: {item.purpose}"
    message["Message-ID"] = f"<{item.id}@implementation.local>"
    message.set_content(f"请使用以下一次性链接完成操作：\n\n{item.body}\n\n如果不是本人请求，请忽略。")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def flush_mail() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session:
            items = (await session.scalars(select(MailDelivery).where(MailDelivery.status == "queued").limit(30).with_for_update(skip_locked=True))).all()
            for item in items:
                try:
                    await asyncio.to_thread(smtp_send, item)
                    item.status, item.body = "sent", ""
                except Exception as exc:
                    item.attempts += 1
                    item.last_error = type(exc).__name__
                    if item.attempts >= 3:
                        item.status, item.body = "failed", ""
            await session.commit()
    finally:
        await engine.dispose()
