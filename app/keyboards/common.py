from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Тексты reply-кнопок
BACK = "⬅️ Назад"
CANCEL = "❌ Отмена"
MENU = "🏠 В меню"
CREATE = "🎬 Создать видео"
EXAMPLES = "⭐ Примеры"
HOW = "❓ Как это работает?"
PARTNER = "🤝 Партнёрская программа"


def nav_kb(*, include_back: bool = True) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if include_back:
        rows.append([KeyboardButton(text=BACK), KeyboardButton(text=CANCEL)])
    else:
        rows.append([KeyboardButton(text=CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CREATE)],
            [KeyboardButton(text=EXAMPLES), KeyboardButton(text=HOW)],
            [KeyboardButton(text=PARTNER)],
        ],
        resize_keyboard=True,
    )


def category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👶 Ребёнок", callback_data="cat:child"),
                InlineKeyboardButton(text="🧑 Взрослый", callback_data="cat:adult"),
            ],
        ]
    )


def trend_select_kb(trend_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выбрать", callback_data=f"trend:{trend_idx}")]
        ]
    )


def other_category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Другая категория?", callback_data="cat_reset")]
        ]
    )


def upsell_kb(trend_idx: int, token: str) -> InlineKeyboardMarkup:
    t = int(trend_idx)
    tok = token.strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔥 3 видео за 299₽", callback_data=f"pkg:3:{t}:{tok}"),
                InlineKeyboardButton(text="🎬 1 видео за 149₽", callback_data=f"pkg:1:{t}:{tok}"),
            ],
        ]
    )


def pay_kb(amount: int, trend_idx: int, token: str) -> InlineKeyboardMarkup:
    t = int(trend_idx)
    tok = token.strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {amount}₽", callback_data=f"pay:{amount}:{t}:{tok}")],
        ]
    )


def pay_error_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Повторить оплату", callback_data="pay_retry")],
            [InlineKeyboardButton(text="💳 Оплатить другой картой", callback_data="pay_retry")],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="pay_support")],
        ]
    )
