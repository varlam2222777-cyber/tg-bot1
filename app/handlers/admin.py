from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select

from app.config import Settings
from app.debug_flags import set_trend_debug, trend_debug_enabled
from app.db.models import Generation, Payment, User
from app.db.repo import add_balance, get_user, upsert_user

router = Router(name="admin")


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("admin"))
async def cmd_admin(message: Message, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    await message.answer(
        "🔐 АДМИН-ПАНЕЛЬ\n\n"
        "Выберите раздел:\n\n"
        "📊 /stats_today — Статистика за сегодня\n"
        "👥 /users — Пользователи\n"
        "💰 /revenue — Выручка\n"
        "❌ /errors — Логи ошибок (заглушка)\n"
        "➕ /addbalance — тест: пополнить баланс генераций пользователю\n"
        "🔎 /debugtrend — уведомления о trend_id и URL при выборе тренда (без KIE)",
    )


@router.message(Command("addbalance"))
async def cmd_addbalance(message: Message, command: CommandObject, settings: Settings, session) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    if not command.args:
        await message.answer(
            "Тестовый режим: начислить генерации на баланс без оплаты.\n\n"
            "Формат:\n"
            "/addbalance <telegram_user_id> <количество_генераций>\n\n"
            "Пример:\n"
            "/addbalance 123456789 3"
        )
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Нужно ровно два аргумента: числовой user_id и количество генераций.")
        return
    try:
        target_id = int(parts[0])
        n = int(parts[1])
    except ValueError:
        await message.answer("user_id и количество должны быть целыми числами.")
        return
    if n < 1 or n > 10_000:
        await message.answer("Количество: от 1 до 10000.")
        return

    await upsert_user(session, target_id, None)
    await add_balance(session, target_id, n)
    await session.commit()
    u = await get_user(session, target_id)
    bal = u.balance if u else n
    await message.answer(
        f"✅ Пользователю {target_id} начислено +{n} генераций.\n"
        f"Текущий баланс: {bal}."
    )


@router.message(Command("stats_today"))
async def stats_today(message: Message, settings: Settings, session) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    u_started = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= start))
    u_up = await session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= start, User.funnel_uploaded_photo.is_(True))
    )
    u_paid = await session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= start, User.funnel_paid.is_(True))
    )

    rev = await session.execute(
        select(Payment.amount_rub, func.count())
        .where(Payment.created_at >= start, Payment.status == "succeeded")
        .group_by(Payment.amount_rub)
    )
    lines = []
    total = 0
    for amount, cnt in rev.all():
        s = int(amount) * int(cnt)
        total += s
        lines.append(f"│ За {amount}₽: {cnt} × {amount}₽ = {s:,}₽".replace(",", " "))

    ok = await session.scalar(
        select(func.count()).select_from(Generation).where(Generation.created_at >= start, Generation.status == "completed")
    )
    fail = await session.scalar(
        select(func.count()).select_from(Generation).where(Generation.created_at >= start, Generation.status == "failed")
    )

    text = (
        f"📊 Статистика за {start.date()}\n\n"
        f"┌─ ПОЛЬЗОВАТЕЛИ ─┐\n"
        f"│ Запустили бота: {u_started or 0} чел\n"
        f"│ Загрузили фото: {u_up or 0} чел\n"
        f"│ Оплатили: {u_paid or 0} чел\n"
        f"└─────────────────┘\n\n"
        f"┌─ ВЫРУЧКА ─┐\n"
        + ("\n".join(lines) if lines else "│ (нет данных)\n")
        + f"\n│ ИТОГО: {total:,}₽\n".replace(",", " ")
        + "└───────────┘\n\n"
        f"┌─ ГЕНЕРАЦИИ ─┐\n"
        f"│ Успешные: {ok or 0}\n"
        f"│ Ошибки: {fail or 0}\n"
        f"└─────────────┘"
    )
    await message.answer(text)


@router.message(Command("users"))
async def users_cmd(message: Message, settings: Settings, session) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    total = await session.scalar(select(func.count()).select_from(User))
    await message.answer(
        f"👥 ПОЛЬЗОВАТЕЛИ\n\n"
        f"┌─ ВСЕГО ─┐\n"
        f"│ Всего: {total or 0} чел\n"
        f"└─────────┘\n\n"
        f"(Топ-10 и активность — расширь запросы в БД при необходимости.)"
    )


@router.message(Command("revenue"))
async def revenue_cmd(message: Message, settings: Settings, session) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(
            Payment.created_at >= start, Payment.status == "succeeded"
        )
    )
    await message.answer(
        "💰 ВЫРУЧКА\n\n"
        f"┌─ СЕГОДНЯ ─┐\n"
        f"│ {int(today or 0):,}₽\n".replace(",", " ")
        + "└───────────┘\n\n"
        + "(Неделя/месяц — добавь агрегацию по датам.)"
    )


@router.message(Command("debugtrend"))
async def cmd_debugtrend(message: Message, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    new_val = not trend_debug_enabled()
    set_trend_debug(new_val)
    if new_val:
        await message.answer(
            "🔎 Уведомления о выборе тренда: ВКЛ\n\n"
            "При каждом выборе тренда пользователем всем админам "
            "придёт trend_id и TREND_URL из .env (до оплаты и генерации).\n\n"
            "Повтори /debugtrend чтобы выключить."
        )
    else:
        await message.answer("🔎 Уведомления о выборе тренда: ВЫКЛ")


@router.message(Command("errors"))
async def errors_cmd(message: Message, settings: Settings) -> None:
    if not _is_admin(message.from_user.id, settings):
        return
    await message.answer("❌ Логи: смотри таблицу error_logs в БД или расширь выгрузку .txt.")
