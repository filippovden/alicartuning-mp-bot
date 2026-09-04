"""Оркестрация: черновик → AI-контент → валидация → публикация (раздел 4, 5 ТЗ)."""

from __future__ import annotations

import asyncio
import html
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
    ImageType,
    ListingStatus,
    Marketplace,
    Product,
    PublishLog,
    PublishStatus,
    ProductStatus,
    ShopListing,
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
    # cmd_status (app/bot/handlers/list_products.py) читает product.publish_logs
    # синхронно — без eager load это ленивая подгрузка вне await-контекста
    # (MissingGreenlet) на async-сессии, а не просто "None по умолчанию".
    selectinload(Product.publish_logs),
    # «Мои товары»/детали товара показывают статус по каждому магазину
    # (раздел 4.5 ТЗ v5) — та же причина eager load, что и у publish_logs выше.
    selectinload(Product.shop_listings),
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

    INFOGRAPHIC_VARIANTS = ("material", "fit")

    async def generate_infographic_images(self, product_id: int, count: int = 1) -> list[Image]:
        """Генерирует инфографику преимуществ товара (раздел 11, 12 ТЗ).

        При заданном XAI_API_KEY использует настоящую AI-генерацию изображения
        через Grok Imagine (app/services/ai/grok_imagine.py): если у товара есть
        главное фото с публичным http(s) URL — правит его (edit_infographic),
        иначе рисует с нуля (generate_infographic) по промпту, собранному из
        буллетов. При наличии ключа всегда отдаём минимум 2 варианта (акцент на
        материал / на совместимость с моделью) — с одной картинкой продавцу
        не из чего выбирать. Без ключа — один Pillow-рендер, дублировать
        одинаковые Pillow-картинки незачем.

        Буллеты сперва пытаемся получить через Claude, но недоступность или
        отсутствие ключа Anthropic не должны ронять инфографику целиком — при
        любом сбое (или сразу, если ключа нет) собираем буллеты из уже
        заполненных полей товара (см. _safe_generate_bullets). Итоговый
        Pillow-путь работает без единого обращения к Anthropic."""
        from app.services.storage import save_bytes

        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        bullets = await self._safe_generate_bullets(product)
        target_count = max(count, 2) if settings.xai_api_key else count

        created: list[Image] = []
        for i in range(target_count):
            variant = self.INFOGRAPHIC_VARIANTS[i % len(self.INFOGRAPHIC_VARIANTS)]
            infographic_bytes = await self._render_one_infographic(product, bullets, variant)
            storage_file = await save_bytes(self.session, infographic_bytes, filename="infographic.png", content_type="image/png")
            image = await self.add_image(product_id, storage_file.id, image_type="infographic", position=100 + i)
            # storage_file уже есть в памяти — присваиваем напрямую, чтобы хендлер
            # не спотыкался о ленивую подгрузку связи на async-сессии (MissingGreenlet).
            image.storage_file = storage_file
            # Транзиентные (неперсистентные) байты только что сгенерированной
            # картинки — хендлер шлёт их пользователю без обращения к диску,
            # чтобы гонка/проблема с volume не превращалась в «файл не найден»
            # для картинки, которая только что реально была создана.
            image._preview_bytes = infographic_bytes
            created.append(image)
        return created

    async def _safe_generate_bullets(self, product: Product) -> list[str]:
        draft = ProductDraft(
            category=product.category.name if product.category else "",
            draft_title=product.title or "",
            car_model=product.car_model or "",
            color=product.color or "",
            material=product.material or "",
            package_contents=product.package_contents or "",
        )
        if not settings.anthropic_api_key or not settings.anthropic_api_key.strip():
            # Пустой ключ — сразу fallback без сетевого запроса: раньше кнопка
            # «Инфографика» без ANTHROPIC_API_KEY всё равно уходила в
            # AsyncAnthropic.messages.create и молча ждала таймаут/ошибку
            # авторизации, прежде чем откатиться на fallback-буллеты.
            return self._fallback_bullets(product)

        try:
            bullets = await self.ai_service.generate_bullets(product.title or "", draft)
            if bullets:
                return bullets
        except Exception as exc:
            # Логируем только текст ошибки (не exc_info) — traceback, задержавшийся
            # в памяти дольше except-блока, ломает видимость последующих
            # async-запросов в той же сессии SQLAlchemy.
            error_text = str(exc)
            logger.warning(
                "Не удалось получить буллеты для инфографики товара %s через AI (%s) — "
                "собираю их из полей товара без обращения к Anthropic",
                product.id,
                error_text,
            )

        return self._fallback_bullets(product)

    @staticmethod
    def _fallback_bullets(product: Product) -> list[str]:
        """Буллеты из уже заполненных полей товара — без единого запроса к AI,
        чтобы недоступность Anthropic не блокировала инфографику совсем."""
        candidates = [
            f"Материал: {product.material}" if product.material else None,
            f"Цвет: {product.color}" if product.color else None,
            f"Подходит для {product.car_model}" if product.car_model else None,
            product.package_contents,
        ]
        bullets = [c for c in candidates if c]
        while len(bullets) < 3:
            bullets.append(f"Качество {product.brand or settings.brand_name}")
        return bullets

    async def _render_one_infographic(self, product: Product, bullets: list[str], variant: str) -> bytes:
        """Один кадр инфографики для заданного варианта (material/fit). Цепочка:
        Grok edit по референс-фото → Grok generate без референса → Pillow с
        настоящим фото товара (если есть локальный файл) → Pillow только текст —
        каждый шаг best-effort, ни один сбой не должен всплыть наружу."""
        if settings.xai_api_key:
            grok_bytes = await self._try_grok_render(product, bullets, variant)
            if grok_bytes is not None:
                return grok_bytes

        from app.services import image_pipeline

        title = product.brand or settings.brand_name
        local_photo = self._main_photo_local_path(product)
        if local_photo is not None:
            return image_pipeline.generate_infographic_with_product(local_photo.read_bytes(), bullets, title=title)

        return image_pipeline.generate_infographic(bullets, title=title)

    async def _try_grok_render(self, product: Product, bullets: list[str], variant: str) -> bytes | None:
        from app.services.ai.grok_imagine import GrokImagineClient

        prompt = self._build_infographic_prompt(product, bullets, variant)
        client = GrokImagineClient()
        reference_url = self._main_photo_public_url(product)

        if reference_url:
            try:
                return await client.edit_infographic(prompt, reference_url)
            except Exception as exc:
                # Правка по референсу не обязана быть поддержана провайдером —
                # не сдаёмся сразу на Pillow, пробуем сгенерировать с нуля.
                # Логируем только текст ошибки (не exc_info) — traceback,
                # задержавшийся в памяти дольше except-блока, ломает видимость
                # последующих async-запросов в той же сессии SQLAlchemy
                # (см. _safe_generate_bullets).
                logger.warning(
                    "Grok Imagine edit недоступен для товара %s (%s) — пробую generate без референса",
                    product.id,
                    str(exc),
                )

        try:
            return await client.generate_infographic(prompt, aspect_ratio="3:4")
        except Exception as exc:
            logger.warning(
                "Grok Imagine недоступен для товара %s (%s) — использую Pillow-fallback для инфографики",
                product.id,
                str(exc),
            )
            return None

    def _build_infographic_prompt(self, product: Product, bullets: list[str], variant: str) -> str:
        from app.services.ai import prompts

        padded_bullets = (list(bullets) + ["", "", ""])[:3]
        focus = prompts.INFOGRAPHIC_VARIANT_FOCUS.get(variant, prompts.INFOGRAPHIC_VARIANT_FOCUS["material"])
        focus = focus.format(car_model=product.car_model or "Lada")
        return prompts.INFOGRAPHIC_PROMPT.format(
            brand=product.brand or settings.brand_name,
            car_model=product.car_model or "",
            material=product.material or "",
            color=product.color or "",
            bullet1=padded_bullets[0],
            bullet2=padded_bullets[1],
            bullet3=padded_bullets[2],
            variant_focus=focus,
        )

    @staticmethod
    def _main_photo_public_url(product: Product) -> str | None:
        """Главное фото товара как публичный http(s) URL — референс для Grok
        Imagine edit_infographic. Без S3 (STORAGE_BACKEND=local) url — это
        локальный путь на диске контейнера, xAI по нему ничего не скачает —
        штатный случай без S3, тогда просто нет референса (не ошибка)."""
        main_images = [img for img in product.images if img.image_type == ImageType.MAIN] or [
            img for img in product.images if img.image_type != ImageType.INFOGRAPHIC
        ]
        if not main_images:
            return None

        photo = sorted(main_images, key=lambda img: img.position)[0]
        if not photo.storage_file:
            return None

        url = photo.storage_file.url
        if url and url.startswith(("http://", "https://")):
            return url
        return None

    @staticmethod
    def _main_photo_local_path(product: Product):
        """Главное фото товара как локальный файл на диске — источник для
        Pillow-инфографики с настоящим товаром (в отличие от
        _main_photo_public_url, который ищет http(s)-ссылку для референса
        Grok). save_bytes всегда пишет файл локально, даже когда настроен S3
        (раздел 5 ТЗ v3) — поэтому путь есть даже без публичного URL."""
        from pathlib import Path

        main_images = [img for img in product.images if img.image_type == ImageType.MAIN]
        for img in sorted(main_images, key=lambda img: img.position):
            if img.storage_file and img.storage_file.path:
                path = Path(img.storage_file.path)
                if path.exists():
                    return path
        return None

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

    # --- Мультимагазинность (срез v5) --------------------------------------

    async def get_listing(self, product_id: int, shop_id: str) -> ShopListing | None:
        stmt = select(ShopListing).where(ShopListing.product_id == product_id, ShopListing.shop_id == shop_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_listings(self, product_id: int) -> list[ShopListing]:
        stmt = select(ShopListing).where(ShopListing.product_id == product_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def _next_variant_index(self, product_id: int) -> int:
        from app.services.listing_variation import VARIANT_COUNT

        count = len(await self.list_listings(product_id))
        return count % VARIANT_COUNT

    async def _get_or_create_listing(self, product: Product, shop) -> ShopListing:
        """Карточка listing под конкретный магазин — если уже создавалась
        раньше (например, publish_to_shop вызван повторно), переиспользуем
        её как есть (не перегенерируем текст/артикул заново — раздел 4.5 ТЗ:
        повторная публикация не должна тихо плодить новые артикулы)."""
        from app.services.listing_variation import build_listing_variation

        existing = await self.get_listing(product.id, shop.id)
        if existing is not None:
            return existing

        variant_index = await self._next_variant_index(product.id)
        variation = await build_listing_variation(product, shop, variant_index, self.session, self.ai_service)

        listing = ShopListing(
            product_id=product.id,
            shop_id=shop.id,
            platform=shop.platform,
            title=variation.title,
            description=variation.description,
            bullets=variation.bullets,
            vendor_code=variation.vendor_code,
            status=ListingStatus.DRAFT,
        )
        self.session.add(listing)
        await self.session.commit()
        await self.session.refresh(listing)
        return listing

    async def publish_to_shop(self, product_id: int, shop_id: str):
        """Публикует основу товара в ОДИН конкретный магазин под своим
        listing (текст/артикул/клиент этого магазина) — раздел 2, 4.4 ТЗ v5.
        Собирает/переиспользует ShopListing, вызывает уже существующие
        _publish_to_wb/_publish_to_ozon с этим listing и клиентом этого
        магазина (см. app/services/shops.py: client_for)."""
        from app.services import shops as shops_service

        product = await self.get_product(product_id)
        if product is None:
            raise ValueError(f"Товар {product_id} не найден")

        shop = shops_service.get_shop(shop_id)
        if shop is None:
            raise ValueError(f"Магазин {shop_id} не найден")

        validation = await validate_product(product, product.attributes, product.images, self.session)
        if not validation.is_valid:
            raise ValueError(f"Карточка не прошла валидацию:\n{validation.as_text()}")

        listing = await self._get_or_create_listing(product, shop)

        # Раздел 4.5 ТЗ v5: повторный вызов на тот же shop_id не должен молча
        # публиковать ещё раз (второй nmID/задача импорта) — карточка там уже есть.
        already_published = (listing.wb_nm_id if shop.platform == Marketplace.WB else listing.ozon_product_id) is not None
        if already_published:
            return listing

        if shop.platform == Marketplace.WB and not (product.category and product.category.wb_subject_id):
            listing.status = ListingStatus.ERROR
            listing.publish_message = "у категории товара не настроен раздел Wildberries — донастройте в /admin."
            await self.session.commit()
            return listing
        if shop.platform == Marketplace.OZON and not (product.category and product.category.ozon_category_id):
            listing.status = ListingStatus.ERROR
            listing.publish_message = "у категории товара не настроен раздел Ozon — донастройте в /admin."
            await self.session.commit()
            return listing

        client = shops_service.client_for(shop)
        if shop.platform == Marketplace.WB:
            log = await self._publish_to_wb(product, listing=listing, client=client)
        else:
            log = await self._publish_to_ozon(product, listing=listing, client=client)

        listing.status = {
            PublishStatus.SUCCESS: ListingStatus.PUBLISHED,
            PublishStatus.PARTIAL: ListingStatus.PARTIAL,
            PublishStatus.ERROR: ListingStatus.ERROR,
        }[log.status]
        listing.publish_message = log.message
        await self.session.commit()
        await self.session.refresh(listing)

        # Обратная совместимость (раздел 2 ТЗ v5): магазин по умолчанию
        # дублирует nmID/ozon_product_id в старые колонки product.* — их
        # читают /list, /status и остальной код, написанный до v5.
        if shop.id in shops_service.DEFAULT_SHOP_IDS:
            if shop.platform == Marketplace.WB:
                product.wb_nm_id = listing.wb_nm_id
            else:
                product.ozon_product_id = listing.ozon_product_id
            await self.session.commit()

        return listing

    async def _publish_to_wb(
        self,
        product: Product,
        listing: ShopListing | None = None,
        client: WildberriesClient | None = None,
    ) -> PublishLog:
        """Публикация в WB — двухшаговая (раздел 7 ТЗ, Content API):

        1. POST /cards/upload создаёт карточку АСИНХРОННО и не возвращает реальный
           nmID в ответе — только эхо переданных данных. Настоящий nmID появляется
           позже и его нужно опрашивать через /get/cards/list (см. _wait_for_wb_nm_id).
        2. Фото грузятся ТОЛЬКО после того, как nmID подтверждён — WB привязывает
           фото к карточке по nmID, а не по vendorCode.

        listing/client — раздел 6 ТЗ v5: если передан ShopListing (публикация под
        конкретный магазин), в WB уходят название/описание/артикул ЭТОГО listing
        через переданного клиента (ключи этого магазина), а не общий текст основы
        товара через клиент из settings. Без них поведение не меняется — прежняя
        публикация «в магазин по умолчанию» (см. publish())."""
        client = client or WildberriesClient()
        vendor_code = listing.vendor_code if listing is not None else product.vendor_code
        title = listing.title if listing is not None else product.title
        description = listing.description if listing is not None else product.description

        try:
            variant = build_wb_variant(
                product, product.attributes, vendor_code=vendor_code, title=title, description=description
            )
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
            nm_id = await self._wait_for_wb_nm_id(client, vendor_code)
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
                message="карточка отправлена, ID ещё не пришёл. Проверь кабинет WB через пару минут.",
            )

        if listing is not None:
            listing.wb_nm_id = str(nm_id)
        else:
            product.wb_nm_id = str(nm_id)

        # Только товарные фото (MAIN) — инфографика/лайфстайл-кадры не должны
        # затирать или разбавлять основные фото карточки на WB (раздел 3 ТЗ v3).
        image_urls = [
            image.storage_file.url
            for image in sorted(product.images, key=lambda img: img.position)
            if image.image_type == ImageType.MAIN
            and image.storage_file
            and image.storage_file.url
            and image.storage_file.url.startswith(("http://", "https://"))
        ]

        if not image_urls:
            from app.services.storage import s3_configured

            reason = (
                "нужен S3 (STORAGE_BACKEND=s3). Текст карточки на площадке есть, картинок нет."
                if not s3_configured()
                else "нет публичной ссылки на файлы."
            )
            return await self._save_publish_log(
                product,
                Marketplace.WB,
                PublishStatus.PARTIAL,
                status_code=200,
                external_id=str(nm_id),
                message=f"карточка (ID {nm_id}), фото не ушли — {reason}",
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
                message=f"карточка (ID {nm_id}), фото не ушли — ошибка загрузки: {exc.message}",
            )

        return await self._save_publish_log(
            product,
            Marketplace.WB,
            PublishStatus.SUCCESS,
            status_code=200,
            external_id=str(nm_id),
            message=f"карточка (ID {nm_id}), фото: {len(image_urls)}",
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

    async def _publish_to_ozon(
        self,
        product: Product,
        listing: ShopListing | None = None,
        client: OzonClient | None = None,
    ) -> PublishLog:
        """listing/client — см. _publish_to_wb: текст и ключи конкретного
        магазина, если публикация идёт под него (раздел 6 ТЗ v5)."""
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
                    "у категории задан ozon_category_id, но не задан ozon_type_id — "
                    "Ozon требует оба поля вместе. Донастройте категорию в /admin."
                ),
            )

        vendor_code = listing.vendor_code if listing is not None else product.vendor_code
        title = listing.title if listing is not None else product.title
        description = listing.description if listing is not None else product.description

        client = client or OzonClient()
        try:
            item = build_ozon_item(
                product, product.attributes, vendor_code=vendor_code, title=title, description=description
            )
            result = await client.import_products([item])
            if listing is not None:
                listing.ozon_product_id = result.external_id
            else:
                product.ozon_product_id = result.external_id
            log = PublishLog(
                product_id=product.id,
                marketplace=Marketplace.OZON,
                status=PublishStatus.SUCCESS,
                status_code=result.status_code,
                external_id=result.external_id,
                message=f"ID {result.external_id}",
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

        safe_vendor_code = html.escape(product.vendor_code)
        message = (
            f"Модерация WB: карточка {safe_vendor_code} видна в каталоге"
            if now_visible
            else f"Модерация WB: карточка {safe_vendor_code} пока не видна в каталоге "
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
