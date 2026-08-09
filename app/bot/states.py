from aiogram.fsm.state import State, StatesGroup


class NewProductStates(StatesGroup):
    """Диалог создания товара /new (раздел 8 ТЗ)."""

    category = State()
    category_pick_wb = State()
    category_pick_ozon = State()
    title = State()
    brand = State()
    vendor_code = State()
    cost_price = State()
    price = State()
    barcode = State()
    package_contents = State()
    material = State()
    color = State()
    car_model = State()
    dimensions = State()
    weight = State()
    photos = State()
    dynamic_attribute = State()
    confirm = State()


class EditProductStates(StatesGroup):
    choosing_field = State()
    entering_value = State()


class CloneProductStates(StatesGroup):
    """Клонирование карточки под другую модель авто (одна деталь → разные Lada)."""

    car_model = State()
    vendor_code = State()


class CloneBatchStates(StatesGroup):
    """Пакетное клонирование карточки на несколько моделей за раз."""

    car_models = State()
    vendor_code_template = State()


class ReviewReplyStates(StatesGroup):
    entering_reply = State()
