import pytest
from micromarket.db import connect
from micromarket.store import Store, StoreError


@pytest.fixture
def store():
    s = Store(connect(":memory:"))
    s.add_product("COKE", "Cola", 150, 80, stock=10)
    s.deposit(20_000)
    return s


def test_checkout_decrements_stock_and_books_cash(store):
    sale = store.checkout([{"sku": "COKE", "qty": 3}])
    assert sale["total_cents"] == 450
    assert store.list_products()[0]["stock"] == 7
    assert store.cash_cents() == 20_450


def test_checkout_is_atomic_on_insufficient_stock(store):
    store.add_product("CHIPS", "Chips", 175, 100, stock=1)
    with pytest.raises(StoreError):
        store.checkout([{"sku": "COKE", "qty": 2}, {"sku": "CHIPS", "qty": 5}])
    stocks = {p["sku"]: p["stock"] for p in store.list_products()}
    assert stocks == {"COKE": 10, "CHIPS": 1}
    assert store.cash_cents() == 20_000


def test_duplicate_lines_are_merged_and_cannot_bypass_stock(store):
    with pytest.raises(StoreError):
        store.checkout([{"sku": "COKE", "qty": 6}, {"sku": "COKE", "qty": 6}])


def test_refund_restores_stock_once(store):
    sale = store.checkout([{"sku": "COKE", "qty": 2}])
    store.refund(sale["id"])
    assert store.list_products()[0]["stock"] == 10
    assert store.cash_cents() == 20_000
    with pytest.raises(StoreError):
        store.refund(sale["id"])


def test_agent_cannot_price_below_margin_floor(store):
    with pytest.raises(StoreError, match="margin floor"):
        store.set_price("COKE", 85, actor="agent")  # cost 80, floor 88
    assert store.set_price("COKE", 100, actor="agent")["price_cents"] == 100


def test_agent_price_change_limited_but_staff_can_override(store):
    with pytest.raises(StoreError, match="limit"):
        store.set_price("COKE", 300, actor="agent")
    assert store.set_price("COKE", 300, actor="staff")["price_cents"] == 300


def test_agent_restock_caps(store):
    with pytest.raises(StoreError, match="per-order"):
        store.order_restock("COKE", 1000, actor="agent")
    small = Store(connect(":memory:"))
    small.add_product("COKE", "Cola", 150, 80)
    small.deposit(2_500)
    with pytest.raises(StoreError, match="reserve"):
        small.order_restock("COKE", 10, actor="agent")


def test_receive_restock_updates_stock_cost_and_cash(store):
    r = store.order_restock("COKE", 10, unit_cost_cents=100)
    store.receive_restock(r["id"])
    p = store.list_products()[0]
    assert p["stock"] == 20 and p["cost_cents"] == 90  # avg of 80 and 100
    assert store.cash_cents() == 20_000 - 1000
    with pytest.raises(StoreError):
        store.receive_restock(r["id"])


def test_summary_profit(store):
    store.checkout([{"sku": "COKE", "qty": 4}])
    s = store.summary()
    assert s["revenue_cents"] == 600 and s["gross_profit_cents"] == 280
