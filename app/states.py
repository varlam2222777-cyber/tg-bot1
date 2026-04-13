from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    menu = State()
    category_select = State()  # выбор категории (ребёнок / взрослый)
    trend_list = State()       # просматривает превью трендов выбранной категории
    photo1 = State()           # загружает первое фото
    upsell = State()           # выбирает пакет (1 или 3 видео)
    photo2 = State()           # загружает второе фото (пакет 3)
    photo3 = State()           # загружает третье фото (пакет 3)
    summary_three = State()    # сводка заказа на 3 видео + кнопка оплаты
    confirm_pay_one = State()  # сводка заказа на 1 видео + кнопка оплаты
    wait_payment = State()     # ждёт подтверждения оплаты


class GetFileId(StatesGroup):
    waiting = State()
