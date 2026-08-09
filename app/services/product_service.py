"""Оркестрация: черновик → AI-контент → валидация → публикация (раздел 4, 5 ТЗ)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    Attribute,
    BotDialog,
    Category,
    CategoryAttr,
    Image,
    Marketplace,
    Product,
    PublishLog,
    PublishStatus,
    ProductStatus,
    User,
)
from app.services.ai.client import AIContentService, ProductDraft
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.mapping import build_ozon_item, build_wb_variant
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient
from app.services.validation import ValidationResult, validate_product

logger = logging.getLogger(__name__)

PRODUCT_LOAD_OPTIONS = (
    selectinload(Product.category).selectinload(Category.attrs),
    selectinload(Product.attributes).selectinload(Attribute.category_attr),
    selectinload(Product.images).selectinload(Image.storage_file),
    selectinload(Product.variants),
)


@dataclass
class PublishSummary:
    wb: PublishLog | None
    ozon: PublishLog | None

    @property
    def all_succeeded(self) -> bool:
        results = [log for log in (self.wb, self.ozon) if log is not None]
        return bool(results) and all(log.status == PublishStatus.SUCCESS for log in results)


class ProductService:
    def __init__(self, session: AsyncSession, ai_service: AIContentService | None = None):
        self.session = session
        self.ai_service = ai_service or AIContentService()

    # --- Users -------------------------------------------------------

    async def get_or_create_user(self, telegram_id: int, username: str | None, full_name: str | None) -> User:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await self.session.execute(stmt)).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, username=username, full_name=full_name)
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
        return user

    # --- Drafts --------------------------------------------------------

    async def create_draft(self, user_id: int) -> Product:
        product = Product(user_id=user_id, status=ProductStatus.DRAFT)
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def get_product(self, product_id: int) -> Product | None:
        stmt = select(Product).where(Product.id == product_id).options(*PRODUCT_LOAD_OPTIONS)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_products(self, user_id: int, status: ProductStatus | None = None) -> list[Product]:
        stmt = select(Product).where(Product.user_id == user_id).order_by(Product.created_at.desc())
        if status is not None:
            stmt = stmt.where(Product.status == status)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_fields(self, product_id: int, **fields) -> Product:
        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")
        for key, value in fields.items():
            setattr(product, key, value)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def clone_product(self, source_product_id: int) -> Product:
        """Клонирует карточку под другую модель авто — одна деталь автотюнинга
        часто подходит сразу нескольким моделям Lada, и проще размножить
        карточку, чем заполнять её заново.

        Копирует категорию, бренд, материал, цвет, комплектацию, габариты,
        вес, цену (себестоимость/розничную — без неё клон нельзя опубликовать,
        а деталь физически та же), фото и заполненные характеристики категории
        (Attribute — это тоже свойства детали, не карточки-источника, поэтому
        переносятся как есть; variant_id не копируется, у клона нет вариаций
        исходного товара). НЕ копирует vendor_code/barcode (должны быть
        уникальными на маркетплейсах — новый клон получает их через диалог
        клонирования/через /edit), title/description (генерируются заново под
        новую модель — см. generate_ai_content) и wb_nm_id/ozon_product_id
        (это идентификаторы КОНКРЕТНОЙ опубликованной карточки-источника, у
        клона их не может быть, пока он не опубликован сам)."""
        source = await self.get_product(source_product_id)
        if source is None:
            raise ValueError(f"Товар {source_product_id} не найден")

        clone = Product(
            user_id=source.user_id,
            status=ProductStatus.DRAFT,
            category_id=source.category_id,
            brand=source.brand,
            material=source.material,
            color=source.color,
            package_contents=source.package_contents,
            cost_price=source.cost_price,
            price=source.price,
            weight_g=source.weight_g,
            length_mm=source.length_mm,
            width_mm=source.width_mm,
            height_mm=source.height_mm,
        )
        self.session.add(clone)
        await self.session.commit()
        await self.session.refresh(clone)

        for image in source.images:
            await self.add_image(clone.id, image.storage_file_id, image_type=image.image_type, position=image.position)

        for attribute in source.attributes:
            self.session.add(
                Attribute(product_id=clone.id, category_attr_id=attribute.category_attr_id, value=attribute.value)
            )
        if source.attributes:
            await self.session.commit()

        return await self.get_product(clone.id)

    # --- Категории и характеристики ------------------------------------

    async def get_or_fetch_category(
        self, name: str, wb_subject_id: int | None, ozon_category_id: int | None, ozon_type_id: int | None
    ) -> Category:
        stmt = select(Category).where(Category.name == name)
        category = (await self.session.execute(stmt)).scalar_one_or_none()
        if category is None:
            category = Category(
                name=name,
                wb_subject_id=wb_subject_id,
                ozon_category_id=ozon_category_id,
                ozon_type_id=ozon_type_id,
            )
            self.session.add(category)
            await self.session.commit()
            await self.session.refresh(category)
        return category

    async def sync_category_attributes(self, category: Category) -> list[CategoryAttr]:
        """Подтягивает обязательные характеристики категории из WB/Ozon API и кэширует их в БД."""
        created: list[CategoryAttr] = []

        if category.wb_subject_id:
            wb_client = WildberriesClient()
            wb_attrs = await wb_client.get_category_characteristics(category.wb_subject_id)
            for attr in wb_attrs:
                created.append(
                    await self._upsert_category_attr(category, Marketplace.WB, attr.external_id, attr.name, attr.required, attr.attr_type, attr.dictionary)
                )

        if category.ozon_category_id and category.ozon_type_id:
            ozon_client = OzonClient()
            ozon_attrs = await ozon_client.get_category_attributes(category.ozon_category_id, category.ozon_type_id)
            for attr in ozon_attrs:
                created.append(
                    await self._upsert_category_attr(category, Marketplace.OZON, attr.external_id, attr.name, attr.required, attr.attr_type, attr.dictionary)
                )

        return created

    async def _upsert_category_attr(
        self,
        category: Category,
        marketplace: Marketplace,
        external_id: str,
        name: str,
        required: bool,
        attr_type: str,
        dictionary: list | None,
    ) -> CategoryAttr:
        stmt = select(CategoryAttr).where(
            CategoryAttr.category_id == category.id,
            CategoryAttr.marketplace == marketplace,
            CategoryAttr.external_attr_id == external_id,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.name, existing.required, existing.attr_type, existing.dictionary = name, required, attr_type, dictionary
            await self.session.commit()
            return existing

        row = CategoryAttr(
            category_id=category.id,
            marketplace=marketplace,
            external_attr_id=external_id,
            name=name,
            required=required,
            attr_type=attr_type,
            dictionary=dictionary,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def set_attribute_value(self, product_id: int, category_attr_id: int, value: str) -> Attribute:
        stmt = select(Attribute).where(Attribute.product_id == product_id, Attribute.category_attr_id == category_attr_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            existing = Attribute(product_id=product_id, category_attr_id=category_attr_id, value=value)
            self.session.add(existing)
        await self.session.commit()
        await self.session.refresh(existing)
        return existing

    # --- Изображения -----------------------------------------------------

    async def add_image(self, product_id: int, storage_file_id: int, image_type: str = "main", position: int = 0) -> Image:
        image = Image(product_id=product_id, storage_file_id=storage_file_id, image_type=image_type, position=position)
        self.session.add(image)
        await self.session.commit()
        await self.session.refresh(image)
        return image

    # --- Обработка изображений (раздел 11 ТЗ, V2) -------------------------

    async def process_product_images(self, product_id: int) -> list[Image]:
        """Прогоняет все фото товара (image_type=main) через удаление фона и
        приведение к фирменному шаблону, сохраняя результат как новые файлы."""
        from pathlib import Path

        from app.services import image_pipeline
        from app.services.storage import save_bytes

        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        processed: list[Image] = []
        for image in [img for img in product.images if img.image_type == "main"]:
            source_path = Path(image.storage_file.path)
            if not source_path.exists():
                continue
            processed_bytes = image_pipeline.process_product_photo(source_path.read_bytes())
            storage_file = await save_bytes(self.session, processed_bytes, filename="processed.jpg", content_type="image/jpeg")
            new_image = await self.add_image(product_id, storage_file.id, image_type="main", position=image.position)
            processed.append(new_image)
        return processed

    async def generate_infographic_images(self, product_id: int, count: int = 1) -> list[Image]:
        """Генерирует инфографику преимуществ товара (раздел 11, 12 ТЗ).

        При заданном XAI_API_KEY использует настоящую AI-генерацию изображения
        через Grok Imagine (app/services/ai/grok_imagine.py) по промпту,
        собранному из буллетов Claude. Без ключа или при сбое API — fallback на
        MVP-рендер Pillow (текст на белом фоне, app/services/image_pipeline.py),
        бот не падает."""
        from app.services.storage import save_bytes

        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        draft = ProductDraft(
            category=product.category.name if product.category else "",
            draft_title=product.title or "",
            car_model=product.car_model or "",
            color=product.color or "",
            material=product.material or "",
            package_contents=product.package_contents or "",
        )
        bullets = await self.ai_service.generate_bullets(product.title or "", draft)

        created: list[Image] = []
        for i in range(count):
            infographic_bytes = await self._render_infographic(product, bullets)
            storage_file = await save_bytes(self.session, infographic_bytes, filename="infographic.png", content_type="image/png")
            image = await self.add_image(product_id, storage_file.id, image_type="infographic", position=100 + i)
            created.append(image)
        return created

    async def _render_infographic(self, product: Product, bullets: list[str]) -> bytes:
        if settings.xai_api_key:
            try:
                return await self._render_infographic_via_grok(product, bullets)
            except MarketplaceAPIError:
                logger.warning(
                    "Grok Imagine недоступен для товара %s — использую Pillow-fallback для инфографики",
                    product.id,
                    exc_info=True,
                )

        from app.services import image_pipeline

        return image_pipeline.generate_infographic(bullets, title=product.brand)

    async def _render_infographic_via_grok(self, product: Product, bullets: list[str]) -> bytes:
        from app.services.ai import prompts
        from app.services.ai.grok_imagine import GrokImagineClient

        padded_bullets = (list(bullets) + ["", "", ""])[:3]
        prompt = prompts.INFOGRAPHIC_PROMPT.format(
            brand=product.brand or settings.brand_name,
            title=product.title or "",
            car_model=product.car_model or "",
            material=product.material or "",
            color=product.color or "",
            bullets=padded_bullets,
        )
        client = GrokImagineClient()
        return await client.generate_infographic(prompt)

    # --- AI-контент ---------------------------------------------------

    async def generate_ai_content(self, product_id: int) -> Product:
        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        draft = ProductDraft(
            category=product.category.name if product.category else "",
            draft_title=product.title or "",
            car_model=product.car_model or "",
            color=product.color or "",
            material=product.material or "",
            package_contents=product.package_contents or "",
        )
        content = await self.ai_service.generate_full_content(draft)

        product.title = content["title"]
        bullets = "\n".join(f"✔ {b}" for b in content["bullets"])
        description = content["description"]
        if bullets:
            description = f"{description}\n\n{bullets}"
        product.description = description
        product.keywords = ", ".join(content["keywords"])

        await self.session.commit()
        await self.session.refresh(product)
        return product

    # --- Валидация -------------------------------------------------------

    async def validate(self, product_id: int) -> ValidationResult:
        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")
        return await validate_product(product, product.attributes, product.images, self.session)

    # --- Публикация --------------------------------------------------------

    async def publish(self, product_id: int) -> PublishSummary:
        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        validation = await validate_product(product, product.attributes, product.images, self.session)
        if not validation.is_valid:
            raise ValueError(f"Карточка не прошла валидацию:\n{validation.as_text()}")

        product.status = ProductStatus.PUBLISHING
        await self.session.commit()

        wb_log = await self._publish_to_wb(product) if product.category and product.category.wb_subject_id else None
        ozon_log = await self._publish_to_ozon(product) if product.category and product.category.ozon_category_id else None

        summary = PublishSummary(wb=wb_log, ozon=ozon_log)
        logs = [log for log in (wb_log, ozon_log) if log is not None]
        # PARTIAL (карточка создана, но что-то — например, фото — не доехало)
        # не считается полным успехом, но и не ошибкой: карточка уже живая на
        # площадке, поэтому учитывается наравне с SUCCESS в проверке «хоть
        # что-то опубликовано».
        published_like = (PublishStatus.SUCCESS, PublishStatus.PARTIAL)
        if not logs:
            product.status = ProductStatus.ERROR
        elif all(log.status == PublishStatus.SUCCESS for log in logs):
            product.status = ProductStatus.PUBLISHED
        elif any(log.status in published_like for log in logs):
            product.status = ProductStatus.PARTIALLY_PUBLISHED
        else:
            product.status = ProductStatus.ERROR
        await self.session.commit()
        return summary

    async def _publish_to_wb(self, product: Product) -> PublishLog:
        """Публикация в WB — двухшаговая (раздел 7 ТЗ, Content API):

        1. POST /cards/upload создаёт карточку АСИНХРОННО и не возвращает реальный
           nmID в ответе — только эхо переданных данных. Настоящий nmID появляется
           позже и его нужно опрашивать через /get/cards/list (см. _wait_for_wb_nm_id).
        2. Фото грузятся ТОЛЬКО после того, как nmID подтверждён — WB привязывает
           фото к карточке по nmID, а не по vendorCode.
        """
        client = WildberriesClient()
        try:
            variant = build_wb_variant(product, product.attributes)
            await client.create_card(product.category.wb_subject_id, [variant])
        except MarketplaceAPIError as exc:
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.ERROR,
                status_code=exc.status_code,
                message=f"Ошибка создания карточки: {exc.message}",
            )

        try:
            nm_id = await self._wait_for_wb_nm_id(client, product.vendor_code)
        except MarketplaceAPIError as exc:
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.ERROR,
                status_code=exc.status_code,
                message=f"Карточка создана, но не удалось проверить статус в WB: {exc.message}",
            )

        if nm_id is None:
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.ERROR,
                message=(
                    "Карточка отправлена в WB, но nmID не подтверждён за отведённое "
                    "время — проверьте позже в личном кабинете WB."
                ),
            )

        product.wb_nm_id = str(nm_id)

        image_urls = [
            image.storage_file.url
            for image in sorted(product.images, key=lambda img: img.position)
            if image.storage_file
            and image.storage_file.url
            and image.storage_file.url.startswith(("http://", "https://"))
        ]

        if not image_urls:
            from app.services.storage import s3_configured

            reason = (
                "текущее хранилище отдаёт локальный путь к файлу, а не публичный "
                "http(s) URL, который требует WB. Настройте S3-совместимое "
                "хранилище (STORAGE_BACKEND=s3 + S3_* в .env)."
                if not s3_configured()
                else "не нашлось ни одного файла с уже загруженным публичным URL."
            )
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.PARTIAL,
                status_code=200,
                external_id=str(nm_id),
                message=f"Карточка создана (nmID {nm_id}), но фото не загружены: {reason}",
            )

        try:
            await client.upload_images(nm_id, image_urls)
        except MarketplaceAPIError as exc:
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.PARTIAL,
                status_code=200,
                external_id=str(nm_id),
                message=f"Карточка создана (nmID {nm_id}), но фото не загрузились: {exc.message}",
            )

        return await self._save_publish_log(
            product,
            Marketplace.WB,
            PublishStatus.SUCCESS,
            status_code=200,
            external_id=str(nm_id),
            message=f"Карточка создана (nmID {nm_id}), загружено фото: {len(image_urls)}",
        )

    async def _wait_for_wb_nm_id(
        self,
        client: WildberriesClient,
        vendor_code: str,
        attempts: int | None = None,
        delay_seconds: float | None = None,
    ) -> int | None:
        """Опрашивает POST /get/cards/list, пока карточка с нужным vendorCode не
        получит nmID от WB, либо не кончится бюджет попыток (см. _publish_to_wb).

        Если хотя бы одна попытка отвечает успешно (даже пустым списком), значит
        API и ключ рабочие — карточка просто ещё не проиндексирована, и после
        исчерпания попыток возвращаем None (общее сообщение «не подтверждён»).
        Но если WB не ответил успешно НИ РАЗУ (например, невалидный WB_API_KEY),
        пробрасываем исходную ошибку — иначе постоянный сбой авторизации
        маскируется под временную задержку WB, и пользователь не понимает, что
        на самом деле нужно чинить ключ, а не «проверить позже».
        """
        attempts = attempts if attempts is not None else settings.wb_nm_id_poll_attempts
        delay_seconds = delay_seconds if delay_seconds is not None else settings.wb_nm_id_poll_delay_seconds

        last_error: MarketplaceAPIError | None = None
        for attempt in range(attempts):
            try:
                cards = await client.get_cards_list(vendor_codes=[vendor_code])
            except MarketplaceAPIError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(delay_seconds)
                continue

            last_error = None
            for card in cards:
                if card.get("vendorCode") == vendor_code and card.get("nmID"):
                    return int(card["nmID"])
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)

        if last_error is not None:
            raise last_error
        return None

    async def _save_publish_log(
        self,
        product: Product,
        marketplace: Marketplace,
        status: PublishStatus,
        *,
        status_code: int | None = None,
        external_id: str | None = None,
        message: str | None = None,
    ) -> PublishLog:
        log = PublishLog(
            product_id=product.id,
            marketplace=marketplace,
            status=status,
            status_code=status_code,
            external_id=external_id,
            message=message,
        )
        self.session.add(log)
        await self.session.commit()
        return log

    async def _publish_to_ozon(self, product: Product) -> PublishLog:
        # Ozon требует category_id и type_id ВМЕСТЕ (см. build_ozon_item — двухуровневая
        # категоризация). Сюда попадаем только когда ozon_category_id уже задан (см.
        # publish()), поэтому проверяем именно отсутствие пары — type_id. Без этой
        # проверки запрос уходит в Ozon с type_id: null и падает с непонятной для
        # пользователя ошибкой API вместо явного «донастройте категорию».
        category = product.category
        if category and category.ozon_category_id and not category.ozon_type_id:
            return await self._save_publish_log(
                product,
                Marketplace.OZON,
                PublishStatus.ERROR,
                message=(
                    "У категории задан ozon_category_id, но не задан ozon_type_id — "
                    "Ozon требует оба поля вместе. Донастройте категорию в /admin."
                ),
            )

        client = OzonClient()
        try:
            item = build_ozon_item(product, product.attributes)
            result = await client.import_products([item])
            product.ozon_product_id = result.external_id
            log = PublishLog(
                product_id=product.id,
                marketplace=Marketplace.OZON,
                status=PublishStatus.SUCCESS,
                status_code=result.status_code,
                external_id=result.external_id,
                message="Задача импорта создана",
            )
        except MarketplaceAPIError as exc:
            log = PublishLog(
                product_id=product.id,
                marketplace=Marketplace.OZON,
                status=PublishStatus.ERROR,
                status_code=exc.status_code,
                message=exc.message,
            )
        self.session.add(log)
        await self.session.commit()
        return log

    # --- Статус модерации WB (заготовка, раздел E.2 ТЗ) -------------------

    async def check_wb_card_status(self, product: Product) -> str | None:
        """Best-effort проверка, видна ли уже опубликованная карточка WB в каталоге.

        WB Content API не отдаёт отдельного поля «на модерации / одобрено /
        отклонено» в общедоступном /get/cards/list — это лишь эвристика:
        карточка с загруженными фото (mediaFiles) в ответе API считается прошедшей
        размещение. Полноценная проверка причин отклонения потребовала бы
        отдельного эндпоинта WB, непроверенного в этом проекте вживую — заготовка
        под него оставлена явно, а не выдаётся за готовую функциональность.

        Возвращает человекочитаемое уведомление только если статус изменился с
        прошлой проверки (чтобы не слать один и тот же дайджест каждый запуск),
        иначе None.
        """
        if not product.wb_nm_id or not product.vendor_code:
            return None

        client = WildberriesClient()
        try:
            cards = await client.get_cards_list(vendor_codes=[product.vendor_code])
        except MarketplaceAPIError:
            logger.warning("Не удалось проверить статус карточки WB для товара %s", product.id, exc_info=True)
            return None

        card = next((c for c in cards if c.get("vendorCode") == product.vendor_code), None)
        now_visible = bool(card and card.get("mediaFiles"))

        stmt = (
            select(PublishLog)
            .where(
                PublishLog.product_id == product.id,
                PublishLog.marketplace == Marketplace.WB,
                PublishLog.message.like("Модерация WB:%"),
            )
            .order_by(PublishLog.created_at.desc())
            .limit(1)
        )
        last = (await self.session.execute(stmt)).scalar_one_or_none()
        was_visible = bool(last and "видна в каталоге" in last.message)

        if last is not None and now_visible == was_visible:
            return None

        message = (
            f"Модерация WB: карточка {product.vendor_code} видна в каталоге"
            if now_visible
            else f"Модерация WB: карточка {product.vendor_code} пока не видна в каталоге "
            "(фото не подтянулись или карточка ещё на проверке)"
        )
        await self._save_publish_log(
            product,
            Marketplace.WB,
            PublishStatus.SUCCESS if now_visible else PublishStatus.PARTIAL,
            message=message,
        )
        return message

    # --- BotDialog (черновик диалога) ------------------------------------

    async def get_or_create_dialog(self, user_id: int) -> BotDialog:
        stmt = select(BotDialog).where(BotDialog.user_id == user_id).order_by(BotDialog.updated_at.desc())
        dialog = (await self.session.execute(stmt)).scalars().first()
        if dialog is None:
            dialog = BotDialog(user_id=user_id, data={})
            self.session.add(dialog)
            await self.session.commit()
            await self.session.refresh(dialog)
        return dialog

    async def save_dialog_state(self, dialog_id: int, state: str | None, data: dict) -> BotDialog:
        stmt = select(BotDialog).where(BotDialog.id == dialog_id)
        dialog = (await self.session.execute(stmt)).scalar_one()
        dialog.state = state
        dialog.data = data
        await self.session.commit()
        await self.session.refresh(dialog)
        return dialog
