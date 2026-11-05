import pytest
from fastapi.testclient import TestClient
from micromarket.agent import Shopkeeper
from micromarket.app import create_app
from micromarket.autoorder import AutoOrder
from micromarket.db import connect
from micromarket.store import Store, StoreError
from test_agent_api import FakeClient

NOW = "2026-11-05 12:00:00"


def make(shelf=2, room=40, shelf_par=12, with_rule=True):
    s = Store(connect(":memory:"))
    s.deposit(50_000)
    s.add_product("COKE", "Cola", 150, 80, stock=shelf, reorder_point=3)
    a = AutoOrder(s)
    a.set_rule("COKE", par_level=36, pack_size=12, lead_time_days=2)
    if with_rule:
        a.stockroom.set_shelf_rule("COKE", shelf_par)
    if room:
        a.stockroom.set_count("COKE", "stockroom", room, "opening")
    return a


def test_receive_goes_to_stockroom_with_shelf_rule_else_shelf():
    a = make(shelf=5, room=0)
    r = a.store.order_restock("COKE", 10, unit_cost_cents=100)
    a.store.receive_restock(r["id"])
    assert a.store.warehouse_qty(1) == 10 and a.store.list_products()[0]["stock"] == 5
    # weighted average over everything held: (5*80 + 10*100) / 15 = 93.3 -> 93
    assert a.store.list_products()[0]["cost_cents"] == 93


def test_receive_legacy_product_without_shelf_rule_goes_to_shelf():
    a = make(shelf=10, room=0, with_rule=False)
    r = a.store.order_restock("COKE", 10, unit_cost_cents=100)
    a.store.receive_restock(r["id"])
    assert a.store.list_products()[0]["stock"] == 20 and a.store.warehouse_qty(1) == 0


def test_receive_can_override_destination():
    a = make()
    r = a.store.order_restock("COKE", 6)
    a.store.receive_restock(r["id"], to="shelf")
    assert a.store.list_products()[0]["stock"] == 8
    with pytest.raises(StoreError):
        a.store.receive_restock(a.store.order_restock("COKE", 6)["id"], to="moon")


def test_transfer_reserves_stockroom_units_and_completes_onto_shelf():
    a = make(shelf=2, room=10)
    room = a.stockroom
    t = room.create_transfer("COKE", 6)
    assert room.available(1) == 4 and a.store.warehouse_qty(1) == 10   # reserved, not yet moved
    with pytest.raises(StoreError, match="Only 4"):
        room.create_transfer("COKE", 5)
    room.complete_transfer(t["id"])
    assert a.store.warehouse_qty(1) == 4 and a.store.list_products()[0]["stock"] == 8
    with pytest.raises(StoreError, match="already completed"):
        room.complete_transfer(t["id"])


def test_cancel_releases_reservation():
    a = make(room=10)
    t = a.stockroom.create_transfer("COKE", 10)
    a.stockroom.cancel_transfer(t["id"])
    assert a.stockroom.available(1) == 10


def test_count_cannot_drop_below_reserved_and_stockroom_never_negative():
    a = make(room=10)
    t = a.stockroom.create_transfer("COKE", 8)
    with pytest.raises(StoreError, match="reserved"):
        a.stockroom.set_count("COKE", "stockroom", 5)
    a.stockroom.set_count("COKE", "shelf", 0, "spoilage")
    assert a.store.list_products()[0]["stock"] == 0
    a.stockroom.complete_transfer(t["id"])
    assert a.store.warehouse_qty(1) == 2


def test_plan_moves_stock_to_shelf_up_to_par():
    a = make(shelf=2, room=40, shelf_par=12)
    tr = a.plan(NOW)["transfers"][0]
    assert tr["status"] == "move" and tr["qty"] == 10 and tr["short_by"] == 0


def test_plan_limited_by_stockroom_and_reports_shortfall():
    a = make(shelf=2, room=4, shelf_par=12)
    tr = a.plan(NOW)["transfers"][0]
    assert tr["qty"] == 4 and tr["short_by"] == 6 and "short by 6" in tr["reason"]


def test_plan_skips_shelf_that_is_fine_or_has_no_rule_or_is_already_on_pick_list():
    assert make(shelf=10, room=40).plan(NOW)["transfers"] == []
    assert make(shelf=2, room=40, with_rule=False).plan(NOW)["transfers"] == []
    a = make(shelf=2, room=40)
    a.stockroom.create_transfer("COKE", 10)
    assert a.plan(NOW)["transfers"] == []   # shelf 2 + 10 pending = par


def test_purchasing_counts_stockroom_stock():
    # shelf is low but the stockroom is full -> move stock, don't buy
    plan = make(shelf=2, room=40).plan(NOW)
    assert plan["lines"] == [] and plan["transfers"]
    # both empty -> buy for the stockroom
    plan = make(shelf=2, room=0).plan(NOW)
    assert plan["lines"][0]["status"] == "order" and plan["lines"][0]["stockroom"] == 0


def test_buys_when_everything_held_cannot_fill_the_shelf():
    a = make(shelf=2, room=4, shelf_par=12)     # 6 in total < shelf par 12
    assert a.plan(NOW)["lines"][0]["status"] == "order"
    assert make(shelf=2, room=20, shelf_par=12).plan(NOW)["lines"] == []   # 22 covers it


def test_run_creates_pick_list_and_purchases_once():
    a = make(shelf=2, room=20, shelf_par=12)    # enough in the stockroom: move stock, don't buy
    r = a.run(now=NOW)
    assert [t["qty"] for t in r["transfers"]] == [10] and r["placed"] == []
    assert a.run(now=NOW)["transfers"] == []     # idempotent: the pending transfer already reserves the stock
    b = make(shelf=1, room=1, shelf_par=12)
    r = b.run(now=NOW)
    assert len(r["transfers"]) == 1 and len(r["placed"]) == 1
    assert b.stockroom.list_transfers("pending")[0]["qty"] == 1


def test_api_stockroom_flow():
    a = make(shelf=2, room=20)
    c = TestClient(create_app(a.store, Shopkeeper(a.store, client=FakeClient([])), auto_order_interval_s=0))
    c.post("/api/admin/stockroom/transfers", json={"sku": "COKE", "qty": 5})
    c.post("/api/admin/stockroom/transfers", json={"sku": "COKE", "qty": 5})
    st = c.get("/api/admin/stockroom").json()
    assert len(st["pending"]) == 2 and st["stock"][0]["reserved"] == 10
    assert c.post("/api/admin/stockroom/transfers", json={"sku": "COKE", "qty": 11}).status_code == 400
    done = c.post("/api/admin/stockroom/transfers/complete-all").json()
    assert len(done["completed"]) == 2 and not done["errors"]
    assert c.get("/api/products").json()[0]["stock"] == 12
    assert c.post("/api/admin/stockroom/count", json={"sku": "COKE", "location": "stockroom", "qty": 3}).json()["old"] == 10
    r = c.post("/api/admin/restocks", json={"sku": "COKE", "qty": 12}).json()
    c.post(f"/api/admin/restocks/{r['id']}/receive")
    assert a.store.warehouse_qty(1) == 15
