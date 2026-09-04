"""Анализ конкурентов (раздел 4, V2 ТЗ): поиск похожих карточек по ключевому слову,
средняя цена, популярные слова в названиях, рекомендация цены.

WB: используется публичный поисковый API витрины (search.wb.ru) — тот же, которым
пользуется сайт wildberries.ru. Он не требует авторизации, но официально не
документирован для продавцов и может измениться без предупреждения — обращение
обёрнуто в обработку ошибок с понятным сообщением, а не падает бота.

Ozon: официального публичного поиска по каталогу для продавцов не существует — Seller
API не даёт доступа к чужим карточкам. `search_ozon_competitors` явно поднимает
`CompetitorAnalysisError` с объяснением вместо того, чтобы возвращать фиктивные данные.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"
WB_SELLER_CATALOG_URL = "https://catalog.wb.ru/sellers/catalog"
WB_SELLER_LINK_RE = re.compile(r"seller/(\d+)")

WB_MAX_RETRIES = 3
WB_RETRY_BASE_DELAY = 1.0

STOPWORDS = {"для", "с", "и", "на", "в", "от", "по", "к", "из", "или", "не", "без", "под"}

# Эти неофициальные публичные эндпоинты витрины WB отдают 403 Forbidden без
# «браузерных» заголовков — httpx по умолчанию шлёт User-Agent вида
# "python-httpx/0.x", который WB распознаёт как бота и блокирует (особенно
# строго на catalog.wb.ru — search.wb.ru пропускал чаще, но тоже не гарантия).
WB_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.wildberries.ru/",
}


class CompetitorAnalysisError(Exception):
    """Раздел 4 ТЗ v7: несёт status_code (когда он есть), чтобы вызывающий код
    мог показать человеческую фразу вместо str(exc) — сырое исключение httpx
    для HTTPStatusError включает полный URL, который заказчику видеть незачем."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _wb_error_text(status_code: int | None) -> str:
    """Человеческий текст по коду ответа витрины WB — раздел 4 ТЗ v7, таблица:
    403/сеть → «разбор недоступен», 429 → «поиск не пускает», без кода (сетевая
    ошибка) → «сейчас не отвечает». Ни в одном варианте нет URL/пути/str(exc)."""
    if status_code == 429:
        return "Поиск сайта сейчас не пускает. Свои карточки выкладывать можно."
    if status_code == 403:
        return (
            "Разбор чужих магазинов с этого сервера Wildberries не открывает.\n"
            "Свои товары выкладывай через «Новый товар»."
        )
    return "Сайт Wildberries сейчас не отвечает. Попробуйте чуть позже."


async def _get_json_with_retry(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET с retry на 429 (экспоненциальный backoff, уважает Retry-After) —
    те же правила, что и у официальных клиентов WB/Ozon
    (см. BaseMarketplaceClient._retry_delay), но этот публичный неофициальный
    API витрины идёт через свой httpx.AsyncClient, а не через тот базовый
    класс, и раньше падал с первого же 429 без единой попытки повтора."""
    response: httpx.Response | None = None
    for attempt in range(WB_MAX_RETRIES + 1):
        response = await client.get(url, params=params)
        if response.status_code == 429 and attempt < WB_MAX_RETRIES:
            delay = _wb_retry_delay(response, attempt)
            logger.warning("429 от %s, повтор через %.1fс (попытка %d/%d)", url, delay, attempt + 1, WB_MAX_RETRIES)
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()

    # Не должно достигаться (цикл либо возвращает, либо кидает исключение выше).
    raise CompetitorAnalysisError(f"Превышено число повторов при 429 для {url}")


def _wb_retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return WB_RETRY_BASE_DELAY * (2**attempt)


@dataclass
class CompetitorItem:
    name: str
    price: float | None
    brand: str | None = None
    rating: float | None = None
    feedbacks: int | None = None


@dataclass
class CompetitorReport:
    query: str
    items: list[CompetitorItem]

    @property
    def average_price(self) -> float | None:
        prices = [i.price for i in self.items if i.price]
        return round(sum(prices) / len(prices), 2) if prices else None

    @property
    def min_price(self) -> float | None:
        prices = [i.price for i in self.items if i.price]
        return min(prices) if prices else None

    @property
    def max_price(self) -> float | None:
        prices = [i.price for i in self.items if i.price]
        return max(prices) if prices else None

    @property
    def average_rating(self) -> float | None:
        ratings = [i.rating for i in self.items if i.rating]
        return round(sum(ratings) / len(ratings), 2) if ratings else None

    @property
    def total_feedbacks(self) -> int | None:
        feedbacks = [i.feedbacks for i in self.items if i.feedbacks]
        return sum(feedbacks) if feedbacks else None

    def top_keywords(self, limit: int = 10) -> list[str]:
        counter: Counter[str] = Counter()
        for item in self.items:
            for raw_word in item.name.lower().replace(",", " ").replace("(", " ").replace(")", " ").split():
                word = raw_word.strip(".,()/\"'")
                if len(word) < 3 or word in STOPWORDS:
                    continue
                counter[word] += 1
        return [word for word, _ in counter.most_common(limit)]


async def search_wb_competitors(query: str, limit: int = 20, exclude_brand: str | None = None) -> CompetitorReport:
    """Поиск конкурентов на WB через публичный поисковый API витрины (best-effort).

    exclude_brand: если задан, карточки с этим брендом (без учёта регистра)
    отфильтровываются ДО применения limit. Без этого, после того как товар
    опубликован на WB, его собственная карточка нередко попадает в выдачу по
    своему же названию/модели и искажает «среднюю цену конкурентов» ценой
    самого продавца — по сути, схлопывая сигнал в обратную связь с самим собой.
    """
    params = {
        "query": query,
        "resultset": "catalog",
        "limit": limit,
        "curr": "rub",
        "dest": -1257786,  # регион по умолчанию (Москва) — влияет на наличие/цены
        "spp": 30,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=WB_BROWSER_HEADERS) as client:
            payload = await _get_json_with_retry(client, WB_SEARCH_URL, params)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        raise CompetitorAnalysisError(_wb_error_text(status_code), status_code=status_code) from exc
    except httpx.HTTPError as exc:
        logger.warning("Сетевая ошибка при поиске конкурентов WB: %s", exc)
        raise CompetitorAnalysisError(_wb_error_text(None)) from exc

    products = payload.get("data", {}).get("products", [])
    exclude_norm = exclude_brand.strip().casefold() if exclude_brand else None

    items = []
    for p in products:
        brand = p.get("brand")
        if exclude_norm and brand and brand.strip().casefold() == exclude_norm:
            continue

        price_kopecks = p.get("salePriceU") or p.get("priceU")
        price = price_kopecks / 100 if price_kopecks else None
        items.append(
            CompetitorItem(
                name=p.get("name", ""),
                price=price,
                brand=brand,
                rating=p.get("reviewRating") or p.get("rating"),
                feedbacks=p.get("feedbacks"),
            )
        )
        if len(items) >= limit:
            break

    return CompetitorReport(query=query, items=items)


def parse_wb_seller_id(text: str) -> str | None:
    """Достаёт ID продавца из ссылки на магазин WB (wildberries.ru/seller/12345)
    или принимает сам числовой ID, если прислали его напрямую."""
    text = text.strip()
    match = WB_SELLER_LINK_RE.search(text)
    if match:
        return match.group(1)
    if text.isdigit():
        return text
    return None


async def fetch_wb_shop(seller_id: str, max_pages: int = 3, limit: int = 100) -> CompetitorReport:
    """Собирает ассортимент магазина-конкурента на WB по ID продавца (best-effort,
    как и search_wb_competitors — тот же неофициальный публичный API витрины,
    catalog.wb.ru, а не Seller API: доступа к чужому личному кабинету через
    официальный API не бывает, поэтому обёрнуто в те же понятные ошибки)."""
    items: list[CompetitorItem] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=WB_BROWSER_HEADERS) as client:
            for page in range(1, max_pages + 1):
                params = {"dest": -1257786, "supplier": seller_id, "curr": "rub", "spp": 30, "page": page}
                payload = await _get_json_with_retry(client, WB_SELLER_CATALOG_URL, params)
                products = payload.get("data", {}).get("products", [])
                if not products:
                    break
                for p in products:
                    price_kopecks = p.get("salePriceU") or p.get("priceU")
                    price = price_kopecks / 100 if price_kopecks else None
                    items.append(
                        CompetitorItem(
                            name=p.get("name", ""),
                            price=price,
                            brand=p.get("brand"),
                            rating=p.get("reviewRating") or p.get("rating"),
                            feedbacks=p.get("feedbacks"),
                        )
                    )
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        raise CompetitorAnalysisError(_wb_error_text(status_code), status_code=status_code) from exc
    except httpx.HTTPError as exc:
        logger.warning("Сетевая ошибка при разборе магазина WB: %s", exc)
        raise CompetitorAnalysisError(_wb_error_text(None)) from exc

    if not items:
        raise CompetitorAnalysisError("Ничего не нашли.")

    return CompetitorReport(query=seller_id, items=items)


async def search_ozon_competitors(query: str, limit: int = 20) -> CompetitorReport:
    raise CompetitorAnalysisError(
        "Ozon Seller API не предоставляет публичного поиска по каталогу для анализа "
        "карточек конкурентов. Нужен либо парсинг витрины Ozon (нестабильный "
        "неофициальный способ), либо сторонний сервис аналитики (MPStats, Moneyplace "
        "и т.п.) с собственным API-ключом."
    )


def suggest_pricing(report: CompetitorReport, cost_price: float | None, target_margin_pct: float = 35.0) -> dict:
    """Рекомендация розничной цены: себестоимость + целевая маржа, с пометкой о
    позиции относительно среднего чека конкурентов."""
    result: dict[str, float | str | None] = {
        "competitor_avg_price": report.average_price,
        "competitor_min_price": report.min_price,
        "competitor_max_price": report.max_price,
    }

    if cost_price is None:
        result["margin_based_price"] = None
        result["recommended_price"] = report.average_price
        return result

    margin_price = round(cost_price * (1 + target_margin_pct / 100), 2)
    result["margin_based_price"] = margin_price
    result["recommended_price"] = margin_price

    avg = report.average_price
    if avg:
        if margin_price > avg * 1.15:
            result["note"] = "Цена выше среднего по рынку более чем на 15% — проверьте конкурентоспособность"
        elif margin_price < avg * 0.85:
            result["note"] = "Цена заметно ниже конкурентов — возможно, стоит поднять для увеличения маржи"

    return result
