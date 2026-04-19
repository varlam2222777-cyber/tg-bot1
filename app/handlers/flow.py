from __future__ import annotations

import io
import logging
import secrets
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.config import Settings, yookassa_configured
from app.db.models import Order, Payment, User
from app.db.repo import create_order, create_payment_row, set_funnel, upsert_user
from app.keyboards.common import (
    BACK,
    CANCEL,
    category_kb,
    nav_kb,
    other_category_kb,
    pay_error_kb,
    pay_kb,
    trend_select_kb,
    upsell_kb,
)
from app.services.photo import validate_photo_bytes
from app.services import yookassa as yk
from app.debug_flags import notify_trend_debug_if_enabled
from app.states import Flow
from app.trends import trend_by_index, trend_url_by_index, is_adult_trend
from app.worker_queue import enqueue_paid_order

logger = logging.getLogger(__name__)

router = Router(name="flow")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _flow_token_bytes() -> str:
    return secrets.token_hex(3)


def _is_flow_token(s: str) -> bool:
    return len(s) == 6 and all(c in "0123456789abcdef" for c in s)


def _parse_pkg_callback(data: str | None) -> tuple[int, int, str] | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "pkg":
        return None
    if not parts[1].isdigit() or not parts[2].isdigit() or not _is_flow_token(parts[3]):
        return None
    pkg, tr = int(parts[1]), int(parts[2])
    if pkg not in (1, 3):
        return None
    return pkg, tr, parts[3]


def _parse_pay_callback(data: str | None) -> tuple[int, int, str] | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "pay":
        return None
    if not parts[1].isdigit() or not parts[2].isdigit() or not _is_flow_token(parts[3]):
        return None
    amount, tr = int(parts[1]), int(parts[2])
    return amount, tr, parts[3]


_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})


async def _get_photo_bytes_and_file_id(message: Message, bot: Bot) -> tuple[bytes, str] | None:
    try:
        if message.photo:
            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, buf)
            return buf.getvalue(), photo.file_id
        doc = message.document
        if doc and doc.mime_type and doc.mime_type in _IMAGE_MIMES:
            file = await bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await bot.download_file(file.file_path, buf)
            return buf.getvalue(), doc.file_id
    except TelegramBadRequest as e:
        logger.warning("telegram get_file/download: %s", e)
    except Exception:
        logger.exception("download user photo")
    return None


async def _safe_callback_answer(cq: CallbackQuery, *, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await cq.answer(text=text, show_alert=show_alert)
    except TelegramBadRequest as e:
        logger.warning("answer callback: %s", e)


async def _menu(message: Message, state: FSMContext, session: Any, settings: Settings) -> None:
    from app.handlers.start import send_main_menu
    await send_main_menu(message, settings, session, state, message.from_user.id)


async def send_trend_list(message: Message, settings: Settings, category: str) -> None:
    """Отправляет тренды выбранной категории — каждый отдельным сообщением с кнопкой «Выбрать»."""
    trends = settings.child_trends if category == "child" else settings.adult_trends
    await message.answer(
        "Выбери тренд (танец) для своего видео. Под каждым — превью и кнопка выбора:",
        reply_markup=nav_kb(include_back=False),
    )
    for trend in trends:
        kb = trend_select_kb(trend.index)
        if trend.promo_file_id:
            await message.answer_video(trend.promo_file_id, caption=trend.name, reply_markup=kb)
        else:
            await message.answer(trend.name, reply_markup=kb)
    # Кнопка «Другая категория?» в конце списка
    await message.answer("Другая категория?", reply_markup=other_category_kb())


# ─── cancel / back ─────────────────────────────────────────────────────────────

@router.message(
    StateFilter(
        Flow.category_select, Flow.trend_list, Flow.photo1, Flow.photo2, Flow.photo3,
        Flow.upsell, Flow.summary_three, Flow.confirm_pay_one, Flow.wait_payment,
    ),
    F.text == CANCEL,
)
async def cancel_all(message: Message, state: FSMContext, session: Any, settings: Settings) -> None:
    await state.clear()
    await _menu(message, state, session, settings)


@router.message(StateFilter(Flow.category_select), F.text == BACK)
async def back_category(message: Message, state: FSMContext, session: Any, settings: Settings) -> None:
    await state.clear()
    await _menu(message, state, session, settings)


@router.message(StateFilter(Flow.trend_list), F.text == BACK)
async def back_trend_list(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.category_select)
    await message.answer("С кем хотите сгенерировать видео? 👇\n\nВыбери категорию:", reply_markup=category_kb())


@router.message(StateFilter(Flow.photo1), F.text == BACK)
async def back_photo1(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    category = data.get("category", "child")
    await state.set_state(Flow.trend_list)
    await state.update_data(photo_ids=[], trend_first=None, active_pkg_token=None, active_pay_token=None)
    await send_trend_list(message, settings, category)


@router.message(StateFilter(Flow.upsell), F.text == BACK)
async def back_upsell(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    category = data.get("category", "child")
    await state.set_state(Flow.trend_list)
    await state.update_data(photo_ids=[], trend_first=None, active_pkg_token=None, active_pay_token=None)
    await send_trend_list(message, settings, category)


@router.message(StateFilter(Flow.photo2, Flow.photo3), F.text == BACK)
async def back_p23(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = list(data.get("photo_ids") or [])
    st = await state.get_state()
    if st == Flow.photo3.state and len(photos) > 2:
        photos.pop()
        await state.update_data(photo_ids=photos)
        await state.set_state(Flow.photo2)
        await message.answer("Загрузи фото #2 👇", reply_markup=nav_kb())
        return
    if st == Flow.photo2.state:
        photos = photos[:1]
        pkg_tok = _flow_token_bytes()
        await state.update_data(photo_ids=photos, active_pkg_token=pkg_tok, active_pay_token=None)
        await state.set_state(Flow.upsell)
        t_first = int(data.get("trend_first") or 1)
        await message.answer("Выбери вариант 👇", reply_markup=upsell_kb(t_first, pkg_tok))


@router.message(StateFilter(Flow.summary_three), F.text == BACK)
async def back_sum3(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.photo3)
    await message.answer("Загрузи фото #3 👇", reply_markup=nav_kb())


@router.message(StateFilter(Flow.confirm_pay_one, Flow.wait_payment), F.text == BACK)
async def back_pay(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("pkg") == 3:
        await state.set_state(Flow.summary_three)
        await message.answer("Проверь заказ 👇", reply_markup=nav_kb())
    else:
        await state.set_state(Flow.upsell)
        t_first = int(data.get("trend_first") or 1)
        pkg_tok = _flow_token_bytes()
        await state.update_data(active_pkg_token=pkg_tok, active_pay_token=None)
        await message.answer("Выбери вариант 👇", reply_markup=upsell_kb(t_first, pkg_tok))


# ─── category selection ────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"cat:child", "cat:adult"}))
async def category_pick(cq: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await _safe_callback_answer(cq)
    current = await state.get_state()
    if current != Flow.category_select.state:
        if cq.message:
            await cq.message.answer("Сессия сброшена. Нажми /start → «Создать видео».")
        return
    category = "child" if cq.data == "cat:child" else "adult"
    await state.update_data(category=category)
    await state.set_state(Flow.trend_list)
    if cq.message:
        await send_trend_list(cq.message, settings, category)


@router.callback_query(F.data == "cat_reset")
async def cat_reset(cq: CallbackQuery, state: FSMContext) -> None:
    await _safe_callback_answer(cq)
    await state.set_state(Flow.category_select)
    await state.update_data(trend_first=None, active_pkg_token=None, active_pay_token=None)
    if cq.message:
        await cq.message.answer(
            "С кем хотите сгенерировать видео? 👇\n\nВыбери категорию:",
            reply_markup=category_kb(),
        )


# ─── trend selection ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("trend:"))
async def trend_pick(
    cq: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    session: Any,
) -> None:
    await _safe_callback_answer(cq)
    chat_id = cq.from_user.id if cq.message is None else cq.message.chat.id
    current = await state.get_state()
    if current != Flow.trend_list.state:
        await bot.send_message(chat_id, "Сессия сброшена. Нажми /start → «Создать видео».")
        return
    try:
        parts = cq.data.split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError("bad callback")
        idx = int(parts[1])

        tr = trend_by_index(settings, idx)
        if tr is None:
            await bot.send_message(chat_id, "Тренд не найден. Нажми /start и попробуй снова.")
            return
        tname = tr.name

        await set_funnel(session, cq.from_user.id, funnel_selected_trend=True)
        await session.commit()

        await notify_trend_debug_if_enabled(
            bot, settings,
            user_id=cq.from_user.id,
            username=cq.from_user.username,
            trend_id=idx,
            trend_url=tr.video_url,
        )

        await state.update_data(trend_first=idx, active_pkg_token=None, active_pay_token=None)
        await state.set_state(Flow.photo1)

        if cq.message:
            # Показываем промо-видео тренда
            if tr.promo_file_id:
                await cq.message.answer_video(
                    tr.promo_file_id,
                    caption=f"Отличный выбор 👇\n\nТы выбрал: {tname}\n\nТеперь загрузи фото следующим сообщением.",
                )
            else:
                await cq.message.answer(
                    f"Отличный выбор 👇\n\nТы выбрал: {tname}\n\nТеперь загрузи фото следующим сообщением."
                )
            await cq.message.answer(
                "СУПЕР! 📸 ТЕПЕРЬ ОТПРАВЬ ФОТО\n\n"
                "Профессиональная съёмка не нужна — обычное фото с телефона тоже подойдёт 👍\n\n"
                "✅ Подходят фото, где:\n\n"
                "• фото светлое, без фильтров\n"
                "• человек смотрит примерно в камеру\n"
                "• лицо хорошо видно, ничего не закрывает\n"
                "• человек стоит более-менее прямо\n"
                "• в кадре один человек (или питомец)\n\n"
                "❌ Плохой результат, если:\n\n"
                "• человек сильно боком или отвернулся\n"
                "• фото сделано в движении или размыто\n"
                "• лицо в тени или закрыто\n"
                "• часть тела обрезана\n"
                "• в кадре несколько людей\n\n"
                "Загружай фото 👇",
                reply_markup=nav_kb(),
            )
    except Exception:
        logger.exception("trend_pick")
        await bot.send_message(chat_id, "Не получилось обработать выбор тренда. Нажми /start и попробуй ещё раз.")


# ─── photo 1 ──────────────────────────────────────────────────────────────────

@router.message(StateFilter(Flow.photo1), F.photo | F.document)
async def photo1(message: Message, state: FSMContext, session: Any, bot: Bot, settings: Settings) -> None:
    got = await _get_photo_bytes_and_file_id(message, bot)
    if got is None:
        await message.answer("Пришли фото сжатым или файлом JPG / PNG / WebP 📸", reply_markup=nav_kb())
        return
    raw, fid = got
    logger.info("photo1: user=%s size=%d bytes file_id=%s", message.from_user.id, len(raw), fid[:20])
    ok = validate_photo_bytes(raw, "")
    if not ok.ok:
        logger.warning("photo1: REJECTED user=%s reason=%s", message.from_user.id, ok.error_text)
        await message.answer(
            f"Упс! Это фото не подходит 😔\n\n"
            f"Причина: {ok.error_text}\n\n"
            "Попробуй загрузить другое фото 📸",
            reply_markup=nav_kb(),
        )
        return

    await upsert_user(session, message.from_user.id, message.from_user.username)
    await set_funnel(session, message.from_user.id, funnel_uploaded_photo=True)
    await session.commit()

    data = await state.get_data()
    t_first = int(data.get("trend_first") or 1)
    pkg_tok = _flow_token_bytes()
    await state.update_data(photo_ids=[fid], active_pkg_token=pkg_tok, active_pay_token=None)
    await state.set_state(Flow.upsell)

    await message.answer(
        "Фото получено ✅\n\n"
        "Мы готовим ваше видео с выбранным трендом.\n\n"
        "Остался последний шаг — выбрать тариф 👇"
    )
    await message.answer(
        "Выбери тариф:\n\n"
        "🎬 1 видео — 149₽\n"
        "🔥 3 видео — 299₽ (выгоднее)",
    )
    await message.answer("Выбери вариант 👇", reply_markup=upsell_kb(t_first, pkg_tok))


# ─── замена фото на промежуточных шагах ────────────────────────────────────────

@router.message(StateFilter(Flow.upsell, Flow.confirm_pay_one), F.photo | F.document)
async def replace_photo_mid_flow(
    message: Message, state: FSMContext, session: Any, bot: Bot, settings: Settings,
) -> None:
    got = await _get_photo_bytes_and_file_id(message, bot)
    if got is None:
        await message.answer("Пришли фото сжатым или файлом JPG / PNG / WebP 📸", reply_markup=nav_kb())
        return
    raw, fid = got
    ok = validate_photo_bytes(raw, "")
    if not ok.ok:
        await message.answer("Фото не подходит. Попробуй другое 📸", reply_markup=nav_kb())
        return
    await upsert_user(session, message.from_user.id, message.from_user.username)
    await session.commit()
    data = await state.get_data()
    t_first = int(data.get("trend_first") or 1)
    pkg_tok = _flow_token_bytes()
    await state.update_data(photo_ids=[fid], pkg=None, active_pkg_token=pkg_tok, active_pay_token=None)
    await state.set_state(Flow.upsell)
    await message.answer(
        "Новое фото принято ✅ Старое больше не будет использовано.\n\nВыбери вариант заново 👇",
        reply_markup=upsell_kb(t_first, pkg_tok),
    )


# ─── package selection ─────────────────────────────────────────────────────────

@router.callback_query(StateFilter(Flow.upsell), F.data.startswith("pkg:"))
async def pkg_chosen(cq: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await _safe_callback_answer(cq)
    if not cq.message:
        return
    parsed = _parse_pkg_callback(cq.data)
    if parsed is None:
        await cq.message.answer("Эта кнопка тарифа устарела. Нажми «Назад» и выбери тариф заново.")
        return
    pkg, t_first, cb_tok = parsed
    data = await state.get_data()
    if cb_tok != data.get("active_pkg_token"):
        await cq.message.answer(
            "Эта кнопка тарифа больше не действует.\n"
            "Пролистай вниз и нажми «1 видео» / «3 видео» на последнем сообщении."
        )
        return
    await state.update_data(pkg=pkg, trend_first=t_first, active_pkg_token=None)

    tr_sel = trend_by_index(settings, t_first)
    tname = tr_sel.name if tr_sel else str(t_first)

    if pkg == 1:
        pay_tok = _flow_token_bytes()
        await state.update_data(active_pay_token=pay_tok)
        await state.set_state(Flow.confirm_pay_one)
        await cq.message.answer(
            f"К оплате: 149₽ за 1 видео. Нажми кнопку ниже, чтобы оплатить 👇\n\n"
            "Оплачивая, вы соглашаетесь с "
            '<a href="https://drive.google.com/file/d/1i15NAqb8fwzdZGuq1coMuQ-Vtd99HJxG/view?usp=drive_link">политикой конфиденциальности</a>'
            " и "
            '<a href="https://drive.google.com/file/d/1u_c7tFDuU3i-b1achP5mb41fdh7cPy2x/view?usp=drive_link">публичной офертой</a>',
            reply_markup=pay_kb(149, t_first, pay_tok),
        )
        return

    await state.update_data(active_pay_token=None)
    await state.set_state(Flow.photo2)
    await cq.message.answer(
        "Супер выбор! 🎉\n\n"
        f"✅ Тренд: {tname}\n"
        "✅ Фото 1 загружено\n\n"
        "Теперь загрузи ещё 2 фото — для каждого будет своё видео с тем же трендом 📸\n\n"
        "💡 СОВЕТ: Загрузи разные фото (в разных локациях/образах) — видео будут уникальнее!\n\n"
        "Загружай фото #2 👇",
        reply_markup=nav_kb(),
    )


# ─── photo 2 & 3 ──────────────────────────────────────────────────────────────

@router.message(StateFilter(Flow.photo2), F.photo | F.document)
async def photo2(message: Message, state: FSMContext, bot: Bot) -> None:
    got = await _get_photo_bytes_and_file_id(message, bot)
    if got is None:
        await message.answer("Пришли фото сжатым или файлом JPG / PNG / WebP 📸", reply_markup=nav_kb())
        return
    raw, fid = got
    ok = validate_photo_bytes(raw, "")
    if not ok.ok:
        await message.answer("Фото не подходит. Попробуй другое 📸", reply_markup=nav_kb())
        return
    data = await state.get_data()
    photos = list(data.get("photo_ids") or [])
    photos.append(fid)
    await state.update_data(photo_ids=photos)
    await state.set_state(Flow.photo3)
    await message.answer("Отлично! Теперь фото #3 👇", reply_markup=nav_kb())


@router.message(StateFilter(Flow.photo3), F.photo | F.document)
async def photo3(message: Message, state: FSMContext, bot: Bot, settings: Settings) -> None:
    got = await _get_photo_bytes_and_file_id(message, bot)
    if got is None:
        await message.answer("Пришли фото сжатым или файлом JPG / PNG / WebP 📸", reply_markup=nav_kb())
        return
    raw, fid = got
    ok = validate_photo_bytes(raw, "")
    if not ok.ok:
        await message.answer("Фото не подходит. Попробуй другое 📸", reply_markup=nav_kb())
        return
    data = await state.get_data()
    photos = list(data.get("photo_ids") or [])
    photos.append(fid)
    await state.update_data(photo_ids=photos)

    t_first = int(data.get("trend_first") or 1)
    tr_sel = trend_by_index(settings, t_first)
    tname = tr_sel.name if tr_sel else str(t_first)
    pay_tok = _flow_token_bytes()
    await state.update_data(active_pay_token=pay_tok)
    await state.set_state(Flow.summary_three)
    await message.answer(
        "Готово! Все 3 видео — с твоим выбранным трендом и разными фото 😊\n\n"
        f"📦 Твой заказ:\n• Тренд: {tname}\n• Разные фото → 3 разных видео\n\n"
        "Итого: 299₽ за 3 уникальных видео!\n\n"
        "Оплачивая, вы соглашаетесь с "
        '<a href="https://drive.google.com/file/d/1i15NAqb8fwzdZGuq1coMuQ-Vtd99HJxG/view?usp=drive_link">политикой конфиденциальности</a>'
        " и "
        '<a href="https://drive.google.com/file/d/1u_c7tFDuU3i-b1achP5mb41fdh7cPy2x/view?usp=drive_link">публичной офертой</a>',
        reply_markup=pay_kb(299, t_first, pay_tok),
    )


@router.message(StateFilter(Flow.summary_three), F.photo | F.document)
async def photo_while_summary_three(message: Message) -> None:
    await message.answer(
        "Чтобы заменить фото, нажми «Назад» и загрузи снова, или начни с /start.",
        reply_markup=nav_kb(),
    )


# ─── payment ───────────────────────────────────────────────────────────────────

@router.callback_query(StateFilter(Flow.confirm_pay_one, Flow.summary_three), F.data.startswith("pay:"))
async def pay_click(
    cq: CallbackQuery,
    state: FSMContext,
    session: Any,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    await _safe_callback_answer(cq)
    if not cq.message:
        return
    data = await state.get_data()
    uid = cq.from_user.id
    current_state = await state.get_state()
    logger.info("pay_click: user=%s state=%s data_keys=%s callback=%s", uid, current_state, list(data.keys()), cq.data)
    pkg = int(data.get("pkg") or 1)
    photos: list[str] = list(data.get("photo_ids") or [])
    parsed = _parse_pay_callback(cq.data)
    if parsed is None:
        logger.warning("pay_click: REJECT parse failed callback=%r user=%s", cq.data, uid)
        await cq.message.answer("Кнопка «Оплатить» устарела. Нажми «Назад» и снова «Оплатить» или начни с /start.")
        return
    amount, t_first, pay_tok = parsed
    if pay_tok != data.get("active_pay_token"):
        logger.warning("pay_click: REJECT token mismatch user=%s cb_tok=%s fsm_tok=%s", uid, pay_tok, data.get("active_pay_token"))
        await cq.message.answer(
            "Эта кнопка оплаты больше не действует.\nПролистай вниз и нажми «Оплатить» на последнем сообщении."
        )
        return
    logger.info("pay_click: ACCEPTED user=%s amount=%s trend=%s pkg=%s photos=%s", uid, amount, t_first, pkg, len(photos))
    await state.update_data(trend_first=t_first, active_pay_token=None)

    trends = [t_first] if pkg == 1 else [t_first, t_first, t_first]
    credits_needed = 1 if amount == 149 else 3

    r = await session.execute(select(User).where(User.user_id == uid))
    u = r.scalar_one_or_none()
    if not u:
        await upsert_user(session, uid, cq.from_user.username)
        await session.commit()
        r = await session.execute(select(User).where(User.user_id == uid))
        u = r.scalar_one_or_none()
    assert u is not None

    try:
        trend_urls = [trend_url_by_index(settings, t) for t in trends]
    except ValueError as e:
        await cq.message.answer(str(e))
        return

    if pkg == 1 and len(photos) != 1:
        await cq.message.answer("В сессии не то количество фото. Нажми /start и пройди шаги заново.")
        return
    if pkg == 3 and len(photos) != 3:
        await cq.message.answer("В сессии не то количество фото (нужно 3). Нажми /start и пройди шаги заново.")
        return

    if u.balance >= credits_needed:
        order = await create_order(session, uid, pkg, photos, trends, trend_urls=trend_urls)
        order.status = "paid"
        await set_funnel(session, uid, funnel_selected_trend=True, funnel_paid=True)
        await session.commit()
        oid = order.id
        chat_id = cq.message.chat.id
        await state.clear()
        await state.set_state(Flow.menu)
        await cq.message.answer(
            "Списано с баланса ✅ Запускаем генерацию ⏳ 7–15 минут. Можно закрыть бот — пришлём уведомление."
        )
        try:
            await enqueue_paid_order(bot, settings, session_factory, oid, chat_id)
        except Exception:
            logger.exception("enqueue_paid_order order_id=%s", oid)
            await cq.message.answer(f"Не удалось запустить генерацию. Напиши в поддержку: @{settings.support_username}")
        return

    if not yookassa_configured(settings):
        logger.warning("pay_click: YooKassa NOT configured, shop_id=%r", settings.yookassa_shop_id)
        await cq.message.answer(
            "Оплата картой недоступна: не заданы YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY в .env.\n\n"
            "Для теста: /addbalance <user_id> <количество>"
        )
        return

    logger.info("pay_click: creating YooKassa payment user=%s amount=%s", uid, amount)
    order = await create_order(session, uid, pkg, photos, trends, trend_urls=trend_urls)
    await session.flush()

    url, pay_id = await yk.create_payment_url_safe(
        settings,
        amount_rub=amount,
        description=f"Видео ×{credits_needed}",
        metadata={"user_id": str(uid), "order_id": str(order.id), "amount": str(amount)},
    )
    if not url or not pay_id:
        await session.rollback()
        await cq.message.answer(
            f"Не удалось создать платёж в ЮKassa. Попробуй позже или напиши: @{settings.support_username}"
        )
        return

    order.yookassa_payment_id = pay_id
    await create_payment_row(session, uid, amount, order.id, pay_id, "pending")
    await session.commit()

    await state.update_data(last_order_id=order.id, pay_amount=amount)
    await state.set_state(Flow.wait_payment)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="pay_check")],
        ]
    )
    await cq.message.answer(
        f"Счёт на {amount}₽ создан. Оплати по кнопке, затем нажми «Проверить оплату».",
        reply_markup=kb,
    )


@router.callback_query(StateFilter(Flow.wait_payment), F.data == "pay_check")
async def pay_check(
    cq: CallbackQuery,
    state: FSMContext,
    session: Any,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    await _safe_callback_answer(cq)
    if not cq.message:
        return
    data = await state.get_data()
    oid = data.get("last_order_id")
    if not oid:
        await cq.message.answer("Заказ не найден. Начни сначала /start")
        return

    r = await session.execute(select(Order).where(Order.id == oid))
    order = r.scalar_one_or_none()
    if not order or not order.yookassa_payment_id:
        await cq.message.answer("Платёж не найден.")
        return

    if order.status == "paid":
        await cq.message.answer(
            f"Этот заказ уже оплачен. Если видео не пришло — напиши: @{settings.support_username}"
        )
        await state.clear()
        return

    st = await yk.get_payment_status(settings, order.yookassa_payment_id)
    if st is None:
        await cq.message.answer("Не удалось проверить оплату. Попробуй через минуту.")
        return
    if st != "succeeded":
        await cq.message.answer(
            "Если оплата не прошла 😔\n\n"
            "Возможные причины:\n"
            "• Недостаточно средств\n"
            "• Карта отклонена банком\n"
            "• Вы закрыли окно оплаты\n"
            "• Вы оплачиваете картой не из РФ\n\n"
            "🙏🏻 Попробуйте ещё раз через другую карту / способ",
            reply_markup=pay_error_kb(),
        )
        return

    claim = await session.execute(
        update(Order).where(Order.id == oid, Order.status == "pending_payment").values(status="paid")
    )
    if claim.rowcount != 1:
        await session.commit()
        await cq.message.answer(
            f"Заказ уже был обработан ранее. Если видео не пришло — напиши: @{settings.support_username}"
        )
        await state.clear()
        return

    credits = 1 if data.get("pay_amount") == 149 else 3
    uid = order.user_id
    r = await session.execute(select(User).where(User.user_id == uid))
    u = r.scalar_one_or_none()
    if u:
        u.balance += credits
        await set_funnel(session, uid, funnel_selected_trend=True, funnel_paid=True)

    rp = await session.execute(select(Payment).where(Payment.yookassa_payment_id == order.yookassa_payment_id))
    pay = rp.scalar_one_or_none()
    if pay:
        pay.status = "succeeded"

    await session.commit()
    chat_id = cq.message.chat.id
    await cq.message.answer(
        "Оплата прошла ✅ Запускаем генерацию ⏳ 7–15 минут. Можно закрыть бот — пришлём уведомление."
    )
    try:
        await enqueue_paid_order(bot, settings, session_factory, int(oid), chat_id)
    except Exception:
        logger.exception("enqueue_paid_order order_id=%s", oid)
        await cq.message.answer(
            f"Оплата учтена, но очередь генерации не создалась. Напиши: @{settings.support_username}"
        )
    await state.clear()


@router.callback_query(F.data == "pay_retry")
async def pay_retry(cq: CallbackQuery) -> None:
    await _safe_callback_answer(cq)
    if cq.message:
        await cq.message.answer("Повторить оплату 👇\n\nОткрой счёт через «Оплатить» или нажми /start.")


@router.callback_query(F.data == "pay_support")
async def pay_support(cq: CallbackQuery, settings: Settings) -> None:
    await _safe_callback_answer(cq)
    if cq.message:
        await cq.message.answer(f"Напиши: @{settings.support_username}")


@router.callback_query(F.data == "new_video")
async def new_video(cq: CallbackQuery, state: FSMContext, session: Any, settings: Settings) -> None:
    from app.handlers.start import send_main_menu
    await _safe_callback_answer(cq)
    await state.clear()
    if cq.message:
        await send_main_menu(cq.message, settings, session, state, cq.from_user.id)


@router.callback_query(F.data == "upsell_pay")
async def upsell_pay(
    cq: CallbackQuery,
    state: FSMContext,
    session: Any,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Создаёт платёж на 150₽, после оплаты добавляет 2 генерации."""
    await _safe_callback_answer(cq)
    if not cq.message:
        return
    uid = cq.from_user.id

    if not yookassa_configured(settings):
        await cq.message.answer("Оплата временно недоступна. Напиши в поддержку: @" + settings.support_username)
        return

    try:
        url, pay_id = await yk.create_payment_url(
            settings,
            amount_rub=150,
            description="Доп. 2 видео",
            metadata={"user_id": str(uid), "upsell": "1"},
        )
    except Exception:
        logger.exception("upsell_pay create_payment")
        await cq.message.answer("Не удалось создать платёж. Попробуй позже.")
        return

    from app.db.repo import create_payment_row
    await create_payment_row(session, uid, 150, None, pay_id, "pending")
    await session.commit()

    # Сохраняем pay_id в FSM чтобы проверить оплату
    await state.update_data(upsell_pay_id=pay_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
            [InlineKeyboardButton(text="🔄 Я оплатил — проверить", callback_data="upsell_check")],
        ]
    )
    await cq.message.answer(
        "Счёт на 150₽ создан. Оплати по кнопке, затем нажми «Я оплатил — проверить» 👇",
        reply_markup=kb,
    )


@router.callback_query(F.data == "upsell_check")
async def upsell_check(
    cq: CallbackQuery,
    state: FSMContext,
    session: Any,
    settings: Settings,
) -> None:
    await _safe_callback_answer(cq)
    if not cq.message:
        return
    data = await state.get_data()
    pay_id = data.get("upsell_pay_id")
    if not pay_id:
        await cq.message.answer("Платёж не найден. Нажми «Доплатить 150₽» снова.")
        return

    st = await yk.get_payment_status(settings, pay_id)
    if st is None:
        await cq.message.answer("Не удалось проверить оплату. Попробуй через минуту.")
        return
    if st != "succeeded":
        await cq.message.answer(
            "Оплата ещё не прошла 😔\n\nПопробуй ещё раз или используй другую карту.",
            reply_markup=pay_error_kb(),
        )
        return

    from sqlalchemy import select as _select
    from app.db.models import Payment as PaymentModel
    rp = await session.execute(_select(PaymentModel).where(PaymentModel.yookassa_payment_id == pay_id))
    pay_row = rp.scalar_one_or_none()
    if pay_row and pay_row.status == "succeeded":
        await cq.message.answer("Этот платёж уже учтён ✅")
        return

    if pay_row:
        pay_row.status = "succeeded"

    from sqlalchemy import select as _sel
    r = await session.execute(_sel(User).where(User.user_id == cq.from_user.id))
    u = r.scalar_one_or_none()
    if u:
        u.balance += 2

    await session.commit()
    await state.update_data(upsell_pay_id=None)
    await cq.message.answer(
        "Оплата прошла ✅ На твой баланс добавлено +2 генерации!\n\nНажми «Создать видео» 🎬"
    )


# ─── fallback для потерянных состояний ──────────────────────────────────────────
# Эти хендлеры НЕ имеют StateFilter — ловят нажатия когда FSM-состояние потеряно
# (после редеплоя Railway, перезапуска бота, таймаута MemoryStorage)

@router.callback_query(F.data.startswith("pay:"))
async def pay_fallback(cq: CallbackQuery) -> None:
    """Пользователь нажал «Оплатить» но состояние потеряно."""
    logger.warning("pay_fallback: state lost, user=%s callback=%s", cq.from_user.id, cq.data)
    await _safe_callback_answer(cq)
    if cq.message:
        await cq.message.answer(
            "Сессия истекла (бот был перезапущен) 😔\n\n"
            "Нажми /start → «Создать видео» и пройди шаги заново — это быстро!"
        )


@router.callback_query(F.data.startswith("pkg:"))
async def pkg_fallback(cq: CallbackQuery) -> None:
    """Пользователь нажал «1 видео / 3 видео» но состояние потеряно."""
    logger.warning("pkg_fallback: state lost, user=%s callback=%s", cq.from_user.id, cq.data)
    await _safe_callback_answer(cq)
    if cq.message:
        await cq.message.answer(
            "Сессия истекла (бот был перезапущен) 😔\n\n"
            "Нажми /start → «Создать видео» и пройди шаги заново — это быстро!"
        )
