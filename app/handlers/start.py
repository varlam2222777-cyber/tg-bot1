from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.db.repo import upsert_user
from app.keyboards.common import CREATE, EXAMPLES, HOW, MENU, PARTNER, main_menu_kb, nav_kb
from app.states import Flow

router = Router(name="start")

MAIN_MENU_TEXT = (
    "ЗАСТАВЬ СЕБЯ, РОДИТЕЛЕЙ, РЕБЁНКА ИЛИ ПИТОМЦА ТАНЦЕВАТЬ\n\n"
    "Загрузи любое фото —\n"
    "и я оживлю его, сделаю трендовый танец.\n\n"
    "Можно загрузить:\n"
    "👶 ребёнка\n"
    "👥 себя, друзей или родителей\n"
    "🐶🐈 питомца\n\n"
    "1 фото → готово"
)


async def send_main_menu(message: Message, settings: Settings, session, state: FSMContext, user_id: int) -> None:
    from sqlalchemy import select
    from app.db.models import User

    await state.clear()
    await state.set_state(Flow.menu)
    r = await session.execute(select(User).where(User.user_id == user_id))
    u = r.scalar_one_or_none()
    bal = u.balance if u else 0

    text = MAIN_MENU_TEXT + f"\n\n🎟 Доступно генераций: {bal}"

    if settings.promo_video_file_id:
        await message.answer_video(settings.promo_video_file_id, caption=text, reply_markup=main_menu_kb())
    else:
        await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session, settings: Settings) -> None:
    await upsert_user(session, message.from_user.id, message.from_user.username)
    await session.commit()
    await send_main_menu(message, settings, session, state, message.from_user.id)


@router.message(F.text == MENU)
async def to_menu(message: Message, state: FSMContext, session, settings: Settings) -> None:
    await send_main_menu(message, settings, session, state, message.from_user.id)


@router.message(F.text == HOW)
async def how_it_works(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.menu)
    await message.answer(
        "Как это работает? 🤔\n\n"
        "1️⃣ Ты загружаешь фото с ребёнком / взрослым / питомцем 📸\n"
        "2️⃣ Выбираешь трендовый танец из вариантов 🎬\n"
        "3️⃣ Я создаю танцующее видео ✨\n"
        "4️⃣ Ты получаешь результат через 7-15 минут ⏰",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text == EXAMPLES)
async def examples(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.set_state(Flow.menu)

    captions = [
        "Вот что пишут близкие и родители, когда получают видео, будто их дети — профессиональные танцоры. ⭐⭐⭐⭐⭐\n\nПрисоединяйся! 💙",
        "Тренды из нашего бота набирают миллионы просмотров в TikTok по всему миру — дети буквально становятся звёздами. 🚀\n\nДрузья и близкие восхищаются, умиляются и тоже хотят так же 🤩",
        "Мамочка милой девочки сделала видео через нейросеть и показала бабушке — та сначала подумала, что это реальное видео. 🥹\n\nПолучился очень милый пранк 😊\n\nХочешь так же удивить свою семью?",
    ]
    file_ids = [
        settings.example_video_1_file_id,
        settings.example_video_2_file_id,
        settings.example_video_3_file_id,
    ]

    any_sent = False
    for fid, cap in zip(file_ids, captions):
        if fid:
            await message.answer_video(fid, caption=cap)
            any_sent = True

    if not any_sent:
        await message.answer(
            "Примеры скоро появятся здесь! 🎬\n\nПока жми «Создать видео» и убедись сам 👇",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer("Жми «Создать видео» 👇", reply_markup=main_menu_kb())


@router.message(F.text == PARTNER)
async def partner(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.set_state(Flow.menu)
    uid = message.from_user.id
    bot_username = settings.bot_username or "your_bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"

    await message.answer(
        "🤝 Стань партнёром бота и зарабатывай вместе с нами\n\n"
        "Ты приводишь людей — бот делает магию — тебе капает 30% с каждой оплаты.\n\n"
        "Твоя партнёрская ссылка:\n\n"
        f"{ref_link}",
        reply_markup=main_menu_kb(),
    )
    await message.answer(
        "💸 Вывод пока недоступен\n\n"
        "Минимальная сумма вывода — 500 ₽.\n\n"
        "Сейчас доступно: 0 ₽\n\n"
        "Хочешь быстрее набрать? Делись ссылкой в сторис/чатах.",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text == CREATE)
async def create_from_menu(message: Message, state: FSMContext) -> None:
    from app.keyboards.common import category_kb
    await state.clear()
    await state.set_state(Flow.category_select)
    await state.update_data(photo_ids=[], trend_first=None, pkg=None, category=None,
                            active_pkg_token=None, active_pay_token=None)
    await message.answer(
        "С кем хотите сгенерировать видео? 👇\n\nВыбери категорию:",
        reply_markup=category_kb(),
    )
