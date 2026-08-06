"""Веб-админка (SQLAdmin) — прод-реализация «admin-инструмента подбора категории» из
ТЗ (раздел 4, V2/V3) поверх бот-кнопок: просмотр и правка категорий, товаров,
отзывов и логов публикации через браузер. Смонтирована на /admin, только если задан
ADMIN_PANEL_PASSWORD — без пароля панель не поднимается (secure-by-default).

Идея подсмотрена в открытых аналогах Telegram-магазинов на aiogram + FastAPI +
SQLAlchemy (например, AiogramShopBot использует тот же SQLAdmin для админки).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.config import settings
from app.db.models import (
    Category,
    CategoryAttr,
    OzonCategoryNode,
    Product,
    PublishLog,
    Review,
    User,
)
from app.db.session import engine

logger = logging.getLogger(__name__)


class AdminAuth(AuthenticationBackend):
    """Простая логин/пароль авторизация на сессионной куке (см. настройки
    ADMIN_PANEL_USERNAME/ADMIN_PANEL_PASSWORD)."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username, password = form.get("username"), form.get("password")
        if username == settings.admin_panel_username and password == settings.admin_panel_password:
            request.session.update({"admin_authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("admin_authenticated"))


class CategoryAdmin(ModelView, model=Category):
    name = "Категория"
    name_plural = "Категории (сопоставление WB/Ozon)"
    icon = "fa-solid fa-list"
    column_list = [Category.id, Category.name, Category.wb_subject_id, Category.ozon_category_id, Category.ozon_type_id]
    form_columns = [Category.name, Category.wb_subject_id, Category.ozon_category_id, Category.ozon_type_id]


class CategoryAttrAdmin(ModelView, model=CategoryAttr):
    name = "Характеристика категории"
    name_plural = "Характеристики категорий"
    icon = "fa-solid fa-tags"
    column_list = [CategoryAttr.id, CategoryAttr.category_id, CategoryAttr.marketplace, CategoryAttr.name, CategoryAttr.required]
    can_create = False  # заполняется синхронизацией с API, не вручную


class OzonCategoryNodeAdmin(ModelView, model=OzonCategoryNode):
    name = "Категория Ozon (кэш)"
    name_plural = "Кэш дерева категорий Ozon"
    icon = "fa-solid fa-sitemap"
    column_list = [OzonCategoryNode.id, OzonCategoryNode.category_id, OzonCategoryNode.type_id, OzonCategoryNode.name]
    column_searchable_list = [OzonCategoryNode.name]
    can_create = False
    can_edit = False


class ProductAdmin(ModelView, model=Product):
    name = "Товар"
    name_plural = "Товары"
    icon = "fa-solid fa-box"
    column_list = [Product.id, Product.title, Product.vendor_code, Product.status, Product.price, Product.category_id]
    column_searchable_list = [Product.title, Product.vendor_code]
    column_sortable_list = [Product.id, Product.created_at, Product.price]
    form_excluded_columns = [Product.attributes, Product.images, Product.variants, Product.publish_logs]


class ReviewAdmin(ModelView, model=Review):
    name = "Отзыв"
    name_plural = "Отзывы"
    icon = "fa-solid fa-comment"
    column_list = [Review.id, Review.marketplace, Review.rating, Review.sentiment, Review.is_answered, Review.created_at]
    column_sortable_list = [Review.created_at, Review.rating]
    can_create = False


class PublishLogAdmin(ModelView, model=PublishLog):
    name = "Лог публикации"
    name_plural = "Логи публикации"
    icon = "fa-solid fa-clock-rotate-left"
    column_list = [PublishLog.id, PublishLog.product_id, PublishLog.marketplace, PublishLog.status, PublishLog.created_at]
    can_create = False
    can_edit = False
    can_delete = False


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи бота"
    icon = "fa-solid fa-user"
    column_list = [User.id, User.telegram_id, User.username, User.is_admin, User.created_at]
    can_create = False


def register_admin(app: FastAPI) -> Admin | None:
    """Монтирует /admin, только если задан пароль — иначе выключено по умолчанию."""
    if not settings.admin_panel_password:
        logger.info("ADMIN_PANEL_PASSWORD не задан — веб-админка /admin отключена")
        return None

    secret_key = settings.admin_panel_secret_key or settings.admin_panel_password
    admin = Admin(app, engine, authentication_backend=AdminAuth(secret_key=secret_key))

    for view in (CategoryAdmin, CategoryAttrAdmin, OzonCategoryNodeAdmin, ProductAdmin, ReviewAdmin, PublishLogAdmin, UserAdmin):
        admin.add_view(view)

    logger.info("Веб-админка смонтирована на /admin")
    return admin
