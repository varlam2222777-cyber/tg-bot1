from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ErrorLog, Generation, Order, Payment, User


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    r = await session.execute(select(User).where(User.user_id == user_id))
    return r.scalar_one_or_none()


async def upsert_user(session: AsyncSession, user_id: int, username: str | None) -> User:
    u = await get_user(session, user_id)
    if u is None:
        u = User(user_id=user_id, tg_username=username)
        session.add(u)
        await session.flush()
    elif username and u.tg_username != username:
        u.tg_username = username
    return u


async def set_funnel(session: AsyncSession, user_id: int, **kwargs) -> None:
    await session.execute(update(User).where(User.user_id == user_id).values(**kwargs))


async def add_balance(session: AsyncSession, user_id: int, delta: int) -> None:
    u = await get_user(session, user_id)
    if u:
        u.balance += delta


async def create_order(
    session: AsyncSession,
    user_id: int,
    package_type: int,
    photo_file_ids: list[str],
    trend_indices: list[int],
    *,
    trend_urls: list[str] | None = None,
) -> Order:
    o = Order(
        user_id=user_id,
        status="pending_payment",
        package_type=package_type,
        photo_file_ids_json=json.dumps(photo_file_ids, ensure_ascii=False),
        trend_indices_json=json.dumps(trend_indices, ensure_ascii=False),
        trend_urls_json=json.dumps(trend_urls, ensure_ascii=False) if trend_urls else None,
    )
    session.add(o)
    await session.flush()
    return o


async def create_payment_row(
    session: AsyncSession,
    user_id: int,
    amount_rub: int,
    order_id: int | None,
    yookassa_id: str | None,
    status: str,
) -> Payment:
    p = Payment(
        user_id=user_id,
        amount_rub=amount_rub,
        order_id=order_id,
        yookassa_payment_id=yookassa_id,
        status=status,
    )
    session.add(p)
    await session.flush()
    return p


async def log_error(session: AsyncSession, source: str, message: str, user_id: int | None = None, detail: str | None = None) -> None:
    session.add(ErrorLog(source=source, message=message, user_id=user_id, detail=detail))


async def count_users_today(session: AsyncSession) -> int:
    today = dt.datetime.now(dt.UTC).date()
    r = await session.execute(select(func.count()).select_from(User).where(func.date(User.created_at) == today))
    return int(r.scalar() or 0)


async def count_funnel_today(session: AsyncSession, field: str) -> int:
    today = dt.datetime.now(dt.UTC).date()
    col = getattr(User, field)
    r = await session.execute(select(func.count()).select_from(User).where(func.date(User.created_at) == today, col.is_(True)))
    return int(r.scalar() or 0)


async def revenue_sum_today(session: AsyncSession) -> dict[int, int]:
    today = dt.datetime.now(dt.UTC).date()
    r = await session.execute(
        select(Payment.amount_rub, func.count())
        .where(func.date(Payment.created_at) == today, Payment.status == "succeeded")
        .group_by(Payment.amount_rub)
    )
    out: dict[int, int] = {}
    for amount, cnt in r.all():
        out[int(amount)] = int(cnt)
    return out


async def generations_stats_today(session: AsyncSession) -> tuple[int, int]:
    today = dt.datetime.now(dt.UTC).date()
    ok = await session.execute(
        select(func.count()).select_from(Generation).where(func.date(Generation.created_at) == today, Generation.status == "completed")
    )
    fail = await session.execute(
        select(func.count()).select_from(Generation).where(func.date(Generation.created_at) == today, Generation.status == "failed")
    )
    return int(ok.scalar() or 0), int(fail.scalar() or 0)
