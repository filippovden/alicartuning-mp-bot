"""Магазины (СВОИ кабинеты WB/Ozon) — раздел 1 ТЗ v5. SHOPS_JSON парсинг,
обратная совместимость со старым одиночным WB_API_KEY/OZON_*, лимит 4+4,
client_for отдаёт клиента с ключами конкретного магазина."""

from __future__ import annotations

from app.config import settings
from app.db.models import Marketplace
from app.services import shops
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient


def test_empty_shops_json_falls_back_to_single_default_pair(monkeypatch):
    monkeypatch.setattr(settings, "shops_json", "")
    monkeypatch.setattr(settings, "wb_api_key", "wb-token")
    monkeypatch.setattr(settings, "ozon_client_id", "123")
    monkeypatch.setattr(settings, "ozon_api_key", "ozon-token")

    all_shops = shops.list_shops()
    assert {s.platform for s in all_shops} == {Marketplace.WB, Marketplace.OZON}
    wb = shops.default_shop(Marketplace.WB)
    assert wb is not None
    assert wb.id == shops.DEFAULT_WB_SHOP_ID
    assert wb.api_key == "wb-token"


def test_empty_everything_gives_no_shops(monkeypatch):
    monkeypatch.setattr(settings, "shops_json", "")
    monkeypatch.setattr(settings, "wb_api_key", "")
    monkeypatch.setattr(settings, "ozon_client_id", "")
    monkeypatch.setattr(settings, "ozon_api_key", "")

    assert shops.list_shops() == []
    assert shops.default_shop(Marketplace.WB) is None


def test_shops_json_parsed_and_takes_priority_over_defaults(monkeypatch):
    monkeypatch.setattr(settings, "wb_api_key", "old-single-key")
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "TOK1"},'
        '{"id": "wb-kuzov", "name": "WB Кузов", "platform": "wb", "api_key": "TOK2"},'
        '{"id": "ozon-salon", "name": "Ozon Салон", "platform": "ozon", "client_id": "1", "api_key": "TOK3"}]',
    )

    wb_shops = shops.list_shops(platform=Marketplace.WB)
    assert [s.id for s in wb_shops] == ["wb-salon", "wb-kuzov"]
    assert shops.get_shop("wb-salon").api_key == "TOK1"
    # SHOPS_JSON непустой — старый одиночный ключ полностью игнорируется.
    assert all(s.api_key != "old-single-key" for s in shops.list_shops())


def test_shops_json_limits_four_per_platform(monkeypatch):
    entries = [{"id": f"wb-{i}", "name": f"WB {i}", "platform": "wb", "api_key": "x"} for i in range(6)]
    import json

    monkeypatch.setattr(settings, "shops_json", json.dumps(entries))

    wb_shops = shops.list_shops(platform=Marketplace.WB)
    assert len(wb_shops) == 4
    assert [s.id for s in wb_shops] == ["wb-0", "wb-1", "wb-2", "wb-3"]


def test_shops_json_skips_unknown_platform(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "bad", "name": "Bad", "platform": "aliexpress", "api_key": "x"},'
        '{"id": "wb-1", "name": "WB", "platform": "wb", "api_key": "y"}]',
    )
    result = shops.list_shops()
    assert [s.id for s in result] == ["wb-1"]


def test_shops_json_invalid_json_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "shops_json", "not valid json {{{")
    monkeypatch.setattr(settings, "wb_api_key", "fallback-key")
    monkeypatch.setattr(settings, "ozon_client_id", "")
    monkeypatch.setattr(settings, "ozon_api_key", "")

    result = shops.list_shops()
    assert len(result) == 1
    assert result[0].id == shops.DEFAULT_WB_SHOP_ID


def test_shops_json_inactive_shop_excluded(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-1", "name": "WB", "platform": "wb", "api_key": "y", "is_active": false}]',
    )
    assert shops.list_shops() == []


def test_client_for_wb_uses_shop_key():
    shop = shops.Shop(id="wb-1", name="WB", platform=Marketplace.WB, api_key="secret-token")
    client = shops.client_for(shop)
    assert isinstance(client, WildberriesClient)
    assert client.api_key == "secret-token"


def test_client_for_ozon_uses_shop_credentials():
    shop = shops.Shop(id="ozon-1", name="Ozon", platform=Marketplace.OZON, client_id="cid", api_key="secret-token")
    client = shops.client_for(shop)
    assert isinstance(client, OzonClient)
    assert client.client_id == "cid"
    assert client.api_key == "secret-token"


def test_get_shop_unknown_id_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "shops_json", "")
    monkeypatch.setattr(settings, "wb_api_key", "x")
    assert shops.get_shop("does-not-exist") is None
