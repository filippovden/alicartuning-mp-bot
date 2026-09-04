"""Магазины (СВОИ кабинеты WB/Ozon продавца) — раздел 1 ТЗ v5.

Не путать с ShopSnapshot / `/shop` (app/services/pricing_intelligence.py,
app/services/competitor_analysis.py) — там про снимки ЧУЖИХ магазинов
конкурентов на Wildberries. Здесь — про кабинеты, куда бот сам публикует
карточки.

Список магазинов задаётся `SHOPS_JSON` в `.env` — намеренно НЕ таблица в БД
(см. раздел 9 ТЗ v5, п.2): секреты (api_key/client_id) не должны лежать в
базе, а раз источник истины и так `.env`, отдельная таблица не нужна. Если
`SHOPS_JSON` пуст — единственный магазин на платформу собирается из старых
одиночных полей `WB_API_KEY` / `OZON_CLIENT_ID`+`OZON_API_KEY` (обратная
совместимость со старым деплоем — раздел 1 ТЗ, «запасной магазин по
умолчанию»)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.db.models import Marketplace
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient

logger = logging.getLogger(__name__)

MAX_SHOPS_PER_PLATFORM = 4

DEFAULT_WB_SHOP_ID = "wb-default"
DEFAULT_OZON_SHOP_ID = "ozon-default"
DEFAULT_SHOP_IDS = frozenset({DEFAULT_WB_SHOP_ID, DEFAULT_OZON_SHOP_ID})


@dataclass(frozen=True)
class Shop:
    id: str
    name: str
    platform: Marketplace
    api_key: str = ""
    client_id: str = ""
    is_active: bool = True


def _parse_platform(raw: str | None) -> Marketplace | None:
    value = (raw or "").strip().lower()
    if value == "wb":
        return Marketplace.WB
    if value == "ozon":
        return Marketplace.OZON
    return None


def _parse_shops_json() -> list[Shop]:
    raw = (settings.shops_json or "").strip()
    if not raw:
        return []

    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("SHOPS_JSON: не удалось разобрать как JSON — использую магазин по умолчанию")
        return []

    if not isinstance(items, list):
        logger.warning("SHOPS_JSON: ожидался список магазинов — использую магазин по умолчанию")
        return []

    parsed: list[Shop] = []
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            logger.warning("SHOPS_JSON: пропускаю запись без id: %r", item)
            continue
        platform = _parse_platform(item.get("platform"))
        if platform is None:
            logger.warning(
                "SHOPS_JSON: неизвестная platform=%r у магазина %r — пропускаю", item.get("platform"), item.get("id")
            )
            continue
        parsed.append(
            Shop(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                platform=platform,
                api_key=str(item.get("api_key") or ""),
                client_id=str(item.get("client_id") or ""),
                is_active=bool(item.get("is_active", True)),
            )
        )

    # До 4 WB + до 4 Ozon (раздел 1 ТЗ) — лишние молча не показываем в бота,
    # только предупреждение в лог.
    result: list[Shop] = []
    counts = {Marketplace.WB: 0, Marketplace.OZON: 0}
    for shop in parsed:
        if counts[shop.platform] >= MAX_SHOPS_PER_PLATFORM:
            logger.warning(
                "SHOPS_JSON: превышен лимит %d магазинов для %s — %r пропущен",
                MAX_SHOPS_PER_PLATFORM,
                shop.platform.value,
                shop.id,
            )
            continue
        counts[shop.platform] += 1
        result.append(shop)
    return result


def _default_shops() -> list[Shop]:
    shops: list[Shop] = []
    if settings.wb_api_key:
        shops.append(Shop(id=DEFAULT_WB_SHOP_ID, name="Wildberries", platform=Marketplace.WB, api_key=settings.wb_api_key))
    if settings.ozon_client_id and settings.ozon_api_key:
        shops.append(
            Shop(
                id=DEFAULT_OZON_SHOP_ID,
                name="Ozon",
                platform=Marketplace.OZON,
                api_key=settings.ozon_api_key,
                client_id=settings.ozon_client_id,
            )
        )
    return shops


def list_shops(platform: Marketplace | None = None) -> list[Shop]:
    """Активные магазины, SHOPS_JSON если задан, иначе — магазин(ы) по
    умолчанию из одиночных полей .env."""
    shops = _parse_shops_json() or _default_shops()
    shops = [s for s in shops if s.is_active]
    if platform is not None:
        shops = [s for s in shops if s.platform == platform]
    return shops


def get_shop(shop_id: str) -> Shop | None:
    for shop in list_shops():
        if shop.id == shop_id:
            return shop
    return None


def default_shop(platform: Marketplace) -> Shop | None:
    """Первый активный магазин платформы — используется отзывами, аналитикой,
    /shop-конкурентами и check_wb_card_status (раздел 1 ТЗ: «остаются на
    магазине по умолчанию, не расползайся на 8 аналитик»), а также как
    неявный выбор публикации, когда магазинов на платформу не больше одного
    (раздел 4.2 ТЗ — экран выбора можно пропустить)."""
    shops = list_shops(platform=platform)
    return shops[0] if shops else None


def client_for(shop: Shop) -> WildberriesClient | OzonClient:
    """Существующие клиенты WB/Ozon, но с ключами конкретного магазина —
    «главный шов» мультимагазинности (раздел 1 ТЗ v5)."""
    if shop.platform == Marketplace.WB:
        return WildberriesClient(api_key=shop.api_key)
    return OzonClient(client_id=shop.client_id, api_key=shop.api_key)
