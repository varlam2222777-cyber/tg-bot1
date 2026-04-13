from __future__ import annotations

import json
import logging

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Generation, Order
from app.trends import trend_url_by_index
from app.worker import spawn_generation

logger = logging.getLogger(__name__)


async def enqueue_paid_order(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    order_id: int,
    chat_id: int,
) -> None:
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "paid":
            return
        already = await session.scalar(
            select(func.count()).select_from(Generation).where(Generation.order_id == order_id)
        )
        if int(already or 0) > 0:
            logger.info(
                "enqueue_paid_order: заказ %s уже имеет %s generation(s), повтор не создаём",
                order_id,
                already,
            )
            return
        try:
            photos: list[str] = json.loads(order.photo_file_ids_json)
            trends: list[int] = json.loads(order.trend_indices_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.exception("enqueue_paid_order: битый JSON заказа %s: %s", order_id, e)
            return
        if len(photos) != len(trends):
            logger.error(
                "enqueue_paid_order: order %s len(photos)=%s len(trends)=%s",
                order_id,
                len(photos),
                len(trends),
            )
            return
        urls: list[str] | None = None
        if order.trend_urls_json:
            urls = json.loads(order.trend_urls_json)
        gen_ids: list[int] = []
        for i, (photo_id, trend_idx) in enumerate(zip(photos, trends)):
            idx = int(trend_idx)
            ref_url: str | None = None
            if urls is not None and i < len(urls):
                ref_url = urls[i]
            if not ref_url:
                ref_url = trend_url_by_index(settings, idx)
            g = Generation(
                user_id=order.user_id,
                order_id=order.id,
                photo_file_id=photo_id,
                trend_index=idx,
                reference_video_url=ref_url,
                chat_id=chat_id,
                status="pending",
            )
            session.add(g)
            await session.flush()
            gen_ids.append(g.id)
        await session.commit()

    for gid in gen_ids:
        spawn_generation(bot, settings, session_factory, gid)
