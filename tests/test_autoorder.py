import pytest
from fastapi.testclient import TestClient
from micromarket.agent import Shopkeeper
from micromarket.app import create_app
from micromarket.autoorder import AutoOrder
from micromarket.db import connect
from micromarket.store import Store, StoreError
from test_agent_api import FakeClient, text, tool

NOW = "2026-11-05 12:00:00"


def set_cash(store, target):
    store.deposit(target - store.cash_cents())


def sell(store, sku, qty, ts="2026-11-01 12:00:00"):
    sale = store.checkout([{"sku": sku, "qty": qty}])
    store.db.execute("UPDATE sales SET ts=? WHERE id=?", (ts, sale["id"]))


def make(cash=50_000, stock=30):
    s = Store(connect(":memory:"))
    s.deposit(cash)
    s.add_product("COKE", "Cola", 150, 80, stock=stock, reorder_point=3)
    a = AutoOrder(s)
    a.set_rule("COKE", par_level=36, pack_size=12, lead_time_days=2)
    return a


def lines(plan):
    return {l["sku"]: l for l in plan["lines"]}


def test_no_order_when_comfortably_stocked():
    assert make().plan(NOW)["lines"] == []


def test_static_reorder_point_triggers_and_rounds_up_to_packs():
    a = make(stock=2)                      # no sales; trigger = static point 3
    l = lines(a.plan(NOW))["COKE"]
    assert l["status"] == "order" and l["qty"] == 36   # need 34 -> 3 packs of 12
    assert l["cost_cents"] == 36 * 80


def test_sales_velocity_raises_the_trigger():
    a = make(stock=30)
    sell(a.store, "COKE", 28)              # 2 units/day over the 14-day window; stock now 2
    a.store.db.execute("UPDATE products SET stock=5 WHERE sku='COKE'")
    assert lines(a.plan(NOW))["COKE"]["status"] == "order"   # trigger ceil(2*(2+1)) = 6 >= 5
    a.store.db.execute("UPDATE products SET stock=7 WHERE sku='COKE'")
    assert a.plan(NOW)["lines"] == []


def test_old_sales_outside_window_are_ignored():
    a = make(stock=30)
    sell(a.store, "COKE", 28, ts="2026-09-01 12:00:00")
    a.store.db.execute("UPDATE products SET stock=5 WHERE sku='COKE'")
    assert a.plan(NOW)["lines"] == []      # velocity 0, static point 3


def test_pending_orders_count_toward_projected_stock():
    a = make(stock=2)
    a.store.order_restock("COKE", 36)
    assert a.plan(NOW)["lines"] == []


def test_trimmed_to_available_cash_above_reserve():
    a = make(cash=4_000, stock=2)          # budget = 4000 - 2000 reserve = 2000 -> 25 units -> 24
    l = lines(a.plan(NOW))["COKE"]
    assert l["qty"] == 24 and "cash" in l["reason"]


def test_trimmed_to_per_order_cap():
    s = Store(connect(":memory:"))
    s.deposit(100_000)
    s.add_product("TV", "Tablet", 900, 400, stock=0)
    a = AutoOrder(s)
    a.set_rule("TV", par_level=100)
    l = lines(a.plan(NOW))["TV"]
    assert l["qty"] == 25 and l["cost_cents"] == 10_000   # cap $100 / $4


def test_scarce_cash_goes_to_most_urgent_item():
    a = make(stock=30)
    s = a.store
    s.add_product("CHIPS", "Chips", 175, 100, stock=3, reorder_point=3)
    a.set_rule("CHIPS", par_level=12, pack_size=12)
    sell(s, "COKE", 28)                    # 2/day; leaves 2 in stock => 1 day of cover
    set_cash(s, 2_000 + 12 * 80)           # exactly one pack of cola above the reserve
    p = lines(a.plan(NOW))
    assert p["COKE"]["status"] == "order"
    assert p["CHIPS"]["status"] == "skipped"


def test_run_places_pending_restocks_once_and_audits():
    a = make(stock=2)
    r = a.run(now=NOW)
    assert len(r["placed"]) == 1 and not r["errors"]
    assert a.store.list_restocks("pending")[0]["qty"] == 36
    assert a.run(now=NOW)["placed"] == []   # second run is a no-op
    assert any(x["action"] == "auto_order_run" and x["actor"] == "auto" for x in a.store.list_audit())


def test_store_still_enforces_caps_even_if_plan_is_wrong():
    a = make(cash=1_000, stock=2)
    with pytest.raises(StoreError, match="reserve"):
        a.store.order_restock("COKE", 12, actor="auto")


def test_modes():
    a = make(stock=2)
    assert a.mode() == "suggest"
    assert a.run_if_auto() is None
    with pytest.raises(StoreError):
        a.set_mode("yolo")
    a.set_mode("auto")
    assert a.run_if_auto()["placed"]


def test_agent_can_tune_rules_within_limits_and_customers_cannot():
    a = make()
    client = FakeClient([tool("set_reorder_rule", sku="COKE", par_level=200), text("ok"),
                         tool("set_reorder_rule", sku="COKE", par_level=60), text("ok")])
    sk = Shopkeeper(a.store, client=client)
    sk.run_manager()
    assert a.get_rule("COKE")["par_level"] == 36 and client.calls[1]["messages"][-1]["content"][0]["is_error"]
    sk.run_manager()
    assert a.get_rule("COKE")["par_level"] == 60 and a.get_rule("COKE")["pack_size"] == 12  # preserved
    c2 = FakeClient([tool("set_reorder_rule", sku="COKE", par_level=61), text("no")])
    Shopkeeper(a.store, client=c2).reply_to_customer(a.store.add_message("eve", "x"), "eve", "x")
    assert a.get_rule("COKE")["par_level"] == 60


def test_api_flow():
    a = make(stock=2)
    sk = Shopkeeper(a.store, client=FakeClient([]))
    c = TestClient(create_app(a.store, sk, auto_order_interval_s=0))
    st = c.get("/api/admin/autoorder").json()
    assert st["mode"] == "suggest" and st["plan"]["lines"][0]["qty"] == 36
    assert c.put("/api/admin/autoorder/mode", json={"mode": "nope"}).status_code == 400
    assert c.put("/api/admin/autoorder/rules/COKE", json={"par_level": 24, "pack_size": 6}).json()["par_level"] == 24
    r = c.post("/api/admin/autoorder/run").json()
    assert r["placed"][0]["qty"] == 24 and not r["errors"]
