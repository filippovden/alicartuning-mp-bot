"""SEO-коуч карточки (срез v4, раздел 2 ТЗ) — build_query, build_seo_report:
позиция по цене в выдаче WB, недостающие слова в названии, честные ответы при
сбое/пустой выдаче поиска, безопасные (не ниже себестоимости, без запрещённых
слов) предложения по названию и цене.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.bot.handlers import competitors
from app.db.models import ProductStatus
from app.services.product_service import ProductService
from app.services.seo_coach import build_daily_seo_digest, build_query, build_seo_report, format_seo_digest

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"


class _FakeUser:
    def __init__(self, user_id: int = 1):
        self.id = user_id
        self.username = "u"
        self.full_name = "U"


class _FakeMessage:
    def __init__(self):
        self.answered: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append((text, reply_markup))
        return self


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.from_user = _FakeUser()
        self.message = _FakeMessage()
        self.answer_called = False

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answer_called = True


async def _make_product(session, **fields) -> tuple[ProductService, int]:
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    product = await service.create_draft(user.id)
    defaults = dict(
        title="ALICARTUNING / Накладки зеркал",
        brand="ALICARTUNING",
        car_model="Lada Granta",
        material="ABS-пластик",
        color="Чёрный",
        price=1000,
        cost_price=400,
    )
    defaults.update(fields)
    await service.update_fields(product.id, **defaults)
    return service, product.id


def _competitor_items(count: int, *, base_price: int = 800, step: int = 60) -> list[dict]:
    items = []
    for i in range(count):
        name = f"Накладки зеркал Lada Granta {i}"
        if i < 5:
            name += " глянец ABS-пластик"  # >= MIN_WORD_MENTIONS упоминаний
        items.append({"name": name, "salePriceU": (base_price + i * step) * 100, "brand": "Other", "feedbacks": 50 + i})
    return items


# --- build_query ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_query_strips_brand_and_appends_car_model(session):
    _, product_id = await _make_product(session, title="ALICARTUNING / Накладки зеркал", car_model="Lada Granta")
    service = ProductService(session)
    product = await service.get_product(product_id)
    assert build_query(product) == "Накладки зеркал Lada Granta"


@pytest.mark.asyncio
async def test_build_query_falls_back_to_car_model_and_category_when_title_empty(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=2, username="u", full_name="U")
    product = await service.create_draft(user.id)
    category = await service.get_or_fetch_category(name="Карман двери", wb_subject_id=1, ozon_category_id=None, ozon_type_id=None)
    await service.update_fields(product.id, car_model="Lada Priora", category_id=category.id)

    loaded = await service.get_product(product.id)
    assert build_query(loaded) == "Lada Priora Карман двери"


@pytest.mark.asyncio
async def test_build_query_empty_when_nothing_to_go_on(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=3, username="u", full_name="U")
    product = await service.create_draft(user.id)
    loaded = await service.get_product(product.id)
    assert build_query(loaded) == ""


# --- build_seo_report ------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_report_computes_missing_words_and_price_rank(session):
    respx.get(WB_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"data": {"products": _competitor_items(20)}})
    )
    service, product_id = await _make_product(session, title="ALICARTUNING / Накладки зеркал", price=1000)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.items_count == 20
    # "глянец" встречается у ≥3 конкурентов и отсутствует в нашем title дословно
    # (material="ABS-пластик" не в счёт — missing_in_our_title сверяется именно
    # с текстом title, а не со всеми полями товара).
    assert "глянец" in report.missing_in_our_title

    # our_price=1000 среди сгенерированных цен 800..1940 с шагом 60 — есть чёткий ранг.
    assert report.price_rank is not None
    assert 1 <= report.price_rank <= 21

    action_kinds = [a.kind for a in report.actions]
    assert "title" in action_kinds
    assert len(report.actions) <= 5


@pytest.mark.asyncio
@respx.mock
async def test_exclude_brand_still_filters_own_card_out_of_ranking(session):
    products = [{"name": "own card", "salePriceU": 100000, "brand": "ALICARTUNING"}] + _competitor_items(5)
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": products}}))
    service, product_id = await _make_product(session, brand="ALICARTUNING")
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.items_count == 5  # собственная карточка исключена


@pytest.mark.asyncio
@respx.mock
async def test_suggested_price_never_below_cost(session):
    """Если рынок (p25/медиана) ниже себестоимости — демпинговать до этой цены
    нельзя: suggested_price должен остаться None, а не уйти в минус по марже."""
    cheap_market = [
        {"name": f"item {i}", "salePriceU": 30000, "brand": "Other"} for i in range(10)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": cheap_market}}))
    service, product_id = await _make_product(session, cost_price=1000, price=1200)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.market_median == 300.0
    assert report.suggested_price is None
    assert any(a.kind == "price" for a in report.actions)
    assert any("демпинг" in a.text.lower() for a in report.actions)


@pytest.mark.asyncio
@respx.mock
async def test_suggested_price_set_and_above_cost(session):
    market = [{"name": f"item {i}", "salePriceU": 200000, "brand": "Other"} for i in range(10)]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": market}}))
    service, product_id = await _make_product(session, cost_price=500, price=2500)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.suggested_price is not None
    assert report.suggested_price >= 500


@pytest.mark.asyncio
@respx.mock
async def test_empty_search_result_gives_honest_limit_action(session):
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": []}}))
    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.items_count == 0
    assert len(report.actions) == 1
    assert report.actions[0].kind == "honest_limit"


@pytest.mark.asyncio
@respx.mock
async def test_search_network_error_gives_honest_limit_action_not_traceback(session):
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(500))
    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    assert report.items_count == 0
    assert report.actions[0].kind == "honest_limit"
    assert "поиск" in report.actions[0].text.lower()


@pytest.mark.asyncio
async def test_no_query_data_gives_honest_limit_without_network_call(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=9, username="u", full_name="U")
    product = await service.create_draft(user.id)
    loaded = await service.get_product(product.id)

    report = await build_seo_report(loaded)

    assert report.items_count == 0
    assert report.actions[0].kind == "honest_limit"


@pytest.mark.asyncio
@respx.mock
async def test_suggested_title_and_missing_words_never_contain_forbidden_words(session):
    products = [
        {"name": "Накладки Granta оригинал хит скидка", "salePriceU": 90000, "brand": "Other"}
        for _ in range(5)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": products}}))
    service, product_id = await _make_product(session, title=None)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)

    forbidden_terms = {"оригинал", "хит", "скидка", "акция", "распродажа", "копия", "аналог", "реплика", "лучший"}
    assert not (forbidden_terms & set(report.missing_in_our_title))
    if report.suggested_title:
        assert not any(word in report.suggested_title.casefold() for word in forbidden_terms)


@pytest.mark.asyncio
@respx.mock
async def test_photos_action_when_below_minimum(session):
    from app.db.models import StorageFile

    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": _competitor_items(5)}}))
    service, product_id = await _make_product(session)
    storage_file = StorageFile(path="/tmp/p.jpg", url="https://cdn.example.com/p.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product_id, storage_file.id, image_type="main", position=0)

    product = await service.get_product(product_id)
    report = await build_seo_report(product)

    assert report.our_photo_count == 1
    assert any(a.kind == "photos" for a in report.actions)


# --- Хендлеры seo:/seotitle:/seoprice: --------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_seo_report_handler_replies_and_escapes_html_in_query(session):
    """Раздел 7 ТЗ v4: символ «<» в модели авто не должен ломать HTML-отправку —
    query экранируется в тексте хендлера."""
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": _competitor_items(5)}}))
    service, product_id = await _make_product(session, car_model="Lada <script>Vesta")

    cb = _FakeCallback(f"seo:{product_id}")
    await competitors.seo_report(cb, service)

    assert cb.answer_called is True
    assert len(cb.message.answered) == 1
    text, keyboard = cb.message.answered[0]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert keyboard is not None


@pytest.mark.asyncio
@respx.mock
async def test_seo_report_handler_on_empty_search_still_replies(session):
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": []}}))
    service, product_id = await _make_product(session)

    cb = _FakeCallback(f"seo:{product_id}")
    await competitors.seo_report(cb, service)

    text, _ = cb.message.answered[0]
    assert "выдач" in text.lower()


@pytest.mark.asyncio
async def test_seo_apply_title_updates_product_and_warns_about_published_platforms(session):
    service, product_id = await _make_product(session, title="Накладки зеркал", car_model="Lada Granta")
    await service.update_fields(product_id, status=ProductStatus.PUBLISHED)

    cb = _FakeCallback(f"seotitle:{product_id}")
    await competitors.seo_apply_title(cb, service)

    product = await service.get_product(product_id)
    assert product.title.startswith("ALICARTUNING /")
    joined = " ".join(t for t, _ in cb.message.answered)
    assert "обновлено" in joined
    assert "опубликуй ещё раз" in joined or "кабинете" in joined


@pytest.mark.asyncio
async def test_seo_apply_title_no_suggestion_does_not_touch_product(session):
    # Уже в точности в том формате, который построил бы suggested_title_for_product
    # для тех же material/color/car_model — предлагать нечего.
    already_formatted = "ALICARTUNING / Накладки зеркал для Lada Granta (ABS-пластик, Чёрный)"
    service, product_id = await _make_product(session, title=already_formatted, car_model="Lada Granta")
    original = (await service.get_product(product_id)).title

    cb = _FakeCallback(f"seotitle:{product_id}")
    await competitors.seo_apply_title(cb, service)

    product = await service.get_product(product_id)
    assert product.title == original
    assert "нечего" in cb.message.answered[0][0]


@pytest.mark.asyncio
async def test_seo_apply_title_is_idempotent_on_reapply(session):
    """Применить «Подставить название» дважды подряд не должно задваивать
    « для {модель}» и «(материал, цвет)» во второй раз."""
    service, product_id = await _make_product(session, title="Накладки зеркал", car_model="Lada Granta")

    cb1 = _FakeCallback(f"seotitle:{product_id}")
    await competitors.seo_apply_title(cb1, service)
    once = (await service.get_product(product_id)).title
    assert once.count("Lada Granta") == 1
    assert once.count("ABS-пластик") == 1

    cb2 = _FakeCallback(f"seotitle:{product_id}")
    await competitors.seo_apply_title(cb2, service)
    twice = (await service.get_product(product_id)).title
    assert twice == once  # второй раз — «нечего предлагать», title не изменился
    assert "нечего" in cb2.message.answered[0][0]


@pytest.mark.asyncio
async def test_seo_apply_price_sets_price(session):
    service, product_id = await _make_product(session, cost_price=500)

    cb = _FakeCallback(f"seoprice:{product_id}:777.00")
    await competitors.seo_apply_price(cb, service)

    product = await service.get_product(product_id)
    assert float(product.price) == 777.0


@pytest.mark.asyncio
async def test_seo_apply_price_rejects_price_below_cost(session):
    """Повторная защита на случай устаревшего callback_data — seoprice не
    должен ставить цену ниже себестоимости, даже если она пришла в кнопке."""
    service, product_id = await _make_product(session, cost_price=1000, price=1200)

    cb = _FakeCallback(f"seoprice:{product_id}:500.00")
    await competitors.seo_apply_price(cb, service)

    product = await service.get_product(product_id)
    assert float(product.price) == 1200.0  # не изменилась
    assert "ниже себестоимости" in cb.message.answered[0][0]


# --- Ежедневный SEO-дайджест (раздел 4 ТЗ v4) ------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_daily_digest_includes_published_product_with_title_gap(session):
    products = [
        {"name": "Накладки Granta глянец ABS", "salePriceU": 90000, "brand": "Other"} for _ in range(5)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": products}}))
    service, product_id = await _make_product(session, title="ALICARTUNING / Накладки", vendor_code="ART-1")
    await service.update_fields(product_id, status=ProductStatus.PUBLISHED)
    product = await service.get_product(product_id)

    lines = await build_daily_seo_digest([product])

    assert len(lines) == 1
    assert f"#{product_id}" in lines[0]
    assert "ART-1" in lines[0]

    digest_text = format_seo_digest(lines)
    assert digest_text.startswith("📈 <b>Выдача за сутки</b>")


@pytest.mark.asyncio
async def test_daily_digest_skips_draft_products(session):
    service, product_id = await _make_product(session)  # статус остаётся DRAFT по умолчанию
    product = await service.get_product(product_id)

    lines = await build_daily_seo_digest([product])

    assert lines == []


@pytest.mark.asyncio
@respx.mock
async def test_daily_digest_skips_when_only_photos_action(session):
    """Раздел 4 ТЗ: в дайджест попадает блок, только если есть action price/
    title — если у товара «болит» только фото (или ничего), не спамим."""
    # Название конкурентов совпадает по словам с нашим title — missing_in_our_title
    # остаётся пустым, а цена конкурентов равна нашей — suggested_price тоже None.
    good_price_products = [
        {"name": "Накладки Granta глянец ABS", "salePriceU": 100000, "brand": "Other"} for _ in range(10)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": good_price_products}}))
    service, product_id = await _make_product(
        session, title="ALICARTUNING / Накладки Granta глянец ABS", price=1000, cost_price=200
    )
    await service.update_fields(product_id, status=ProductStatus.PUBLISHED)
    product = await service.get_product(product_id)

    report = await build_seo_report(product)
    assert all(a.kind not in ("price", "title") for a in report.actions)  # только photos, если вообще есть

    lines = await build_daily_seo_digest([product])
    assert lines == []


@pytest.mark.asyncio
@respx.mock
async def test_daily_digest_escapes_html_in_vendor_code_label(session):
    products = [
        {"name": "Накладки Granta глянец ABS", "salePriceU": 90000, "brand": "Other"} for _ in range(5)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": products}}))
    service, product_id = await _make_product(
        session, title="ALICARTUNING / Накладки", vendor_code="ART-<script>1"
    )
    await service.update_fields(product_id, status=ProductStatus.PARTIALLY_PUBLISHED)
    product = await service.get_product(product_id)

    lines = await build_daily_seo_digest([product])

    assert len(lines) == 1
    assert "<script>" not in lines[0]
    assert "&lt;script&gt;" in lines[0]
