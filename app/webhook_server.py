"""
HTTP-сервер для приёма вебхуков от ЮКасса.
Запускается параллельно с Telegram-поллингом на PORT (Railway задаёт автоматически).
"""
from __future__ import annotations

import json
import logging
import os

from aiohttp import web
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.db.models import Order, Payment, User
from app.db.repo import set_funnel

logger = logging.getLogger(__name__)


async def _handle_yookassa_webhook(
    request: web.Request,
    session_factory: async_sessionmaker[AsyncSession],
    bot,
    settings,
) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    event_type = body.get("event", "")
    obj = body.get("object", {})
    logger.info("webhook: event=%s pay_id=%s", event_type, obj.get("id"))

    if event_type != "payment.succeeded":
        # Другие события игнорируем, но отвечаем 200
        return web.Response(text="ok")

    pay_id = obj.get("id")
    metadata = obj.get("metadata") or {}
    order_id_str = metadata.get("order_id")
    amount_str = metadata.get("amount")

    if not pay_id or not order_id_str:
        logger.warning("webhook: нет pay_id или order_id в metadata: %s", body)
        return web.Response(status=400, text="missing fields")

    try:
        order_id = int(order_id_str)
        amount = int(amount_str or "0")
    except ValueError:
        return web.Response(status=400, text="bad metadata")

    async with session_factory() as session:
        # Атомарно переводим заказ pending_payment → paid
        claim = await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "pending_payment")
            .values(status="paid")
            .returning(Order.id, Order.user_id, Order.package_type)
        )
        row = claim.fetchone()
        if row is None:
            # Уже обработан (дублирующий вебхук) — отвечаем 200
            await session.commit()
            return web.Response(text="ok")

        uid = row[1]
        pkg = row[2]
        credits = 1 if amount == 149 else (3 if pkg == 3 else 1)

        r = await session.execute(select(User).where(User.user_id == uid))
        u = r.scalar_one_or_none()
        if u:
            u.balance += credits
            await set_funnel(session, uid, funnel_paid=True)

        rp = await session.execute(select(Payment).where(Payment.yookassa_payment_id == pay_id))
        pay = rp.scalar_one_or_none()
        if pay:
            pay.status = "succeeded"

        await session.commit()

    logger.info("webhook: заказ %s оплачен, user=%s credits=%s", order_id, uid, credits)

    # Запускаем генерацию
    try:
        from app.worker_queue import enqueue_paid_order

        # chat_id = user_id (в личке chat_id == user_id)
        await enqueue_paid_order(bot, settings, session_factory, order_id, uid)
        await bot.send_message(
            uid,
            "Оплата прошла ✅ Запускаем генерацию ⏳ 7–15 минут. Можно закрыть бот — пришлём уведомление."
        )
    except Exception:
        logger.exception("webhook: enqueue_paid_order order_id=%s", order_id)

    return web.Response(text="ok")


def make_app(session_factory: async_sessionmaker[AsyncSession], bot, settings) -> web.Application:
    app = web.Application()

    async def yookassa_handler(request: web.Request) -> web.Response:
        return await _handle_yookassa_webhook(request, session_factory, bot, settings)

    async def health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/yookassa/webhook", yookassa_handler)

    return app


async def start_server(session_factory: async_sessionmaker[AsyncSession], bot, settings) -> None:
    port = int(os.getenv("PORT", "8080"))
    app = make_app(session_factory, bot, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Webhook-сервер запущен на порту %s", port)
