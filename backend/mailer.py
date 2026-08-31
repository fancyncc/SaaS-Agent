from __future__ import annotations

import logging

from backend.config import get_settings

logger = logging.getLogger("saas_agent.mail")


async def send_account_link(email: str, purpose: str, url: str) -> None:
    """Development adapter. Replace this function with SMTP/provider delivery in production."""
    if get_settings().mail_debug:
        logger.warning("MAIL_DEBUG %s link for %s: %s", purpose, email, url)
        return
    raise RuntimeError("Production mail adapter is not configured")
