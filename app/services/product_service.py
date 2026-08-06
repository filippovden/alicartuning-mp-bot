"""Оркестрация: черновик → AI-контент → валидация → публикация (раздел 4, 5 ТЗ)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

PRODUCT_LOAD_OPTIONS = (
    selectinload(Product.category).selectinload(Category.attrs),
    selectinload(Product.attributes).selectinload(Attribute.category_attr),
    selectinload(Product.images),
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
        if not logs:
            product.status = ProductStatus.ERROR
        elif all(log.status == PublishStatus.SUCCESS for log in logs):
            product.status = ProductStatus.PUBLISHED
        elif any(log.status == PublishStatus.SUCCESS for log in logs):
            product.status = ProductStatus.PARTIALLY_PUBLISHED
        else:
            product.status = ProductStatus.ERROR
        await self.session.commit()
        return summary

    async def _publish_to_wb(self, product: Product) -> PublishLog:
        client = WildberriesClient()
        try:
            variant = build_wb_variant(product, product.attributes)
            result = await client.create_card(product.category.wb_subject_id, [variant])
            product.wb_nm_id = result.external_id
            log = PublishLog(
                product_id=product.id,
                marketplace=Marketplace.WB,
                status=PublishStatus.SUCCESS,
                status_code=result.status_code,
                external_id=result.external_id,
                message="Карточка создана",
            )
        except MarketplaceAPIError as exc:
            log = PublishLog(
                product_id=product.id,
                marketplace=Marketplace.WB,
                status=PublishStatus.ERROR,
                status_code=exc.status_code,
                message=exc.message,
            )
        self.session.add(log)
        await self.session.commit()
        return log

    async def _publish_to_ozon(self, product: Product) -> PublishLog:
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
