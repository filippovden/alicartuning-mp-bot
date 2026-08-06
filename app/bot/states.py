from aiogram.fsm.state import State, StatesGroup


class NewProductStates(StatesGroup):
    """Диалог создания товара /new (раздел 8 ТЗ)."""

    category = State()
    title = State()
    brand = State()
    vendor_code = State()
    cost_price = State()
    price = State()
    barcode = State()
    package_contents = State()
    material = State()
    color = State()
    dimensions = State()
    weight = State()
    photos = State()
    dynamic_attribute = State()
    confirm = State()


class EditProductStates(StatesGroup):
    choosing_field = State()
    entering_value = State()
