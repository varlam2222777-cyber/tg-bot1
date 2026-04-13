from __future__ import annotations

import logging

from aiogram import Bot

from app.config import Settings

logger = logging.getLogger(__name__)

_trend_debug_enabled = False


def trend_debug_enabled() -> bool:
    return _trend_debug_enabled


def set_trend_debug(enabled: bool) -> None:
    global _trend_debug_enabled
    _trend_debug_enabled = enabled


async def notify_trend_debug_if_enabled(
    bot: Bot,
    settings: Settings,
    *,
    user_id: int,
    username: str | None,
    trend_id: int,
    trend_url: str,
) -> None:
    if not _trend_debug_enabled:
        return
    who = f"{user_id}" + (f" @{username}" if username else "")
    text = (
        "🔎 debug trend (выбор пользователя, до оплаты и генерации)\n\n"
        f"Пользователь: {who}\n"
        f"trend_id: {trend_id}\n"
        f"TREND_URL:\n{trend_url}"
    )
    for aid in settings.admin_ids:
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            logger.warning("notify_trend_debug admin %s: %s", aid, e)
