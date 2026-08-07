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

from collections import Counter
from dataclasses import dataclass

import httpx

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"

STOPWORDS = {"для", "с", "и", "на", "в", "от", "по", "к", "из", "или", "не", "без", "под"}


class CompetitorAnalysisError(Exception):
    pass


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
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(WB_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise CompetitorAnalysisError(f"Не удалось получить данные поиска WB: {exc}") from exc

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
