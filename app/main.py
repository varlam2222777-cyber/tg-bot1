from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app.config import load_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.handlers.admin import router as admin_router
from app.handlers.flow import router as flow_router
from app.handlers.start import router as start_router
from app.handlers.tools import router as tools_router
from app.middlewares.db import DbSessionMiddleware
from app.middlewares.settings import SettingsMiddleware


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    settings = load_settings()
    if not settings.telegram_token:
        raise SystemExit("Укажите TELEGRAM_TOKEN в .env")

    engine = make_engine(settings)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    bot = Bot(
        token=settings.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(SettingsMiddleware(settings))
    dp.update.middleware(DbSessionMiddleware(session_factory))

    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(tools_router)
    dp.include_router(flow_router)

    @dp.errors()
    async def on_error(event: ErrorEvent, bot: Bot) -> bool:
        logging.exception("Необработанная ошибка: %s", event.exception)
        u = event.update
        if u.callback_query:
            try:
                await u.callback_query.answer("Ошибка. Попробуй /start", show_alert=True)
            except Exception:
                pass
        return True

    from app.webhook_server import start_server
    await start_server(session_factory, bot, settings)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
