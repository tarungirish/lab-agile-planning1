"""Owner-set spending limits for automatic, plan-driven and shopkeeper purchase orders."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from micromarket.agent import MANAGER_TOOLS, Shopkeeper
from micromarket.app import create_app
from micromarket.autoorder import AutoOrder
from micromarket.db import connect
from micromarket.store import SPENDING_DEFAULTS, Store, StoreError
from test_agent_api import FakeClient

NOW = "2026-11-05 12:00:00"


def store(cash=1_000_000):
    s = Store(connect(":memory:"))
    s.deposit(cash)
    s.add_product("COKE", "Cola", 150, 80, stock=0, reorder_point=3)
    s.add_product("COFFEE", "Cold brew", 350, 200, stock=0, reorder_point=3)
    return s


def test_defaults_are_sized_for_bulk_buying():
    s = store()
    assert s.spending_limits() == SPENDING_DEFAULTS == {
        "per_order_cents": 50_000, "daily_cents": 150_000, "cash_reserve_cents": 20_000}
    r = s.order_restock("COKE", 480, actor="agent")          # 20 cases of 24 = $384: refused under the old $100 cap
    assert r["qty"] == 480 and r["ordered_by"] == "agent"
    with pytest.raises(StoreError, match=r"\$500.00 per-order"):
        s.order_restock("COKE", 700, actor="agent")           # $560


def test_daily_limit_counts_automatic_orders_only():
    s = store()
    s.set_spending_limits(per_order_cents=40_000, daily_cents=50_000)
    s.order_restock("COKE", 4_000, actor="staff")            # owner's own $3,200 order: not limited, not counted
    s.order_restock("COKE", 400, actor="agent")              # $320
    assert s.spent_today_cents() == 32_000
    with pytest.raises(StoreError, match=r"today's \$500.00 limit .*\$320.00 already ordered"):
        s.order_restock("COFFEE", 100, actor="auto")         # $200 more would make $520
    s.order_restock("COFFEE", 90, actor="staff-approved")    # $180 -> exactly $500
    assert s.spent_today_cents() == 50_000


def test_cash_reserve_still_applies():
    s = store(cash=30_000)                                    # $300 cash, $200 reserve -> $100 to spend
    with pytest.raises(StoreError, match=r"\$200.00 reserve"):
        s.order_restock("COKE", 200, actor="agent")           # $160
    s.order_restock("COKE", 100, actor="agent")               # $80


def test_planner_trims_to_whatever_limit_binds_first():
    s = store()
    a = AutoOrder(s)
    a.set_rule("COKE", par_level=480, pack_size=24)           # wants 480 = $384
    a.set_rule("COFFEE", par_level=240, pack_size=6)          # wants 240 = $480
    s.set_spending_limits(per_order_cents=30_000, daily_cents=50_000)
    plan = {l["sku"]: l for l in a.plan(NOW)["lines"]}
    assert plan["COFFEE"]["cost_cents"] <= 30_000 and "per-order" in plan["COFFEE"]["reason"]
    total = sum(l["cost_cents"] for l in plan.values())
    assert total <= 50_000 and any("today's spending limit" in l["reason"] for l in plan.values())
    assert a.plan(NOW)["limits"]["daily_cents"] == 50_000
    r = a.run(actor="agent", now=NOW)                         # everything the plan says fits the store's checks
    assert r["errors"] == [] and s.spent_today_cents() == total


def test_only_the_owner_can_change_limits_and_values_are_validated():
    s = store()
    with pytest.raises(StoreError, match="Only the owner"):
        s.set_spending_limits(per_order_cents=1, actor="agent")
    with pytest.raises(StoreError, match="higher than the daily"):
        s.set_spending_limits(per_order_cents=200_000, daily_cents=100_000)
    with pytest.raises(StoreError, match="positive"):
        s.set_spending_limits(daily_cents=0)
    with pytest.raises(StoreError, match="exceed"):
        s.set_spending_limits(daily_cents=50_000_000)
    s.set_spending_limits(cash_reserve_cents=0)
    assert s.spending_limits()["cash_reserve_cents"] == 0
    assert "spending_limits" in [a["action"] for a in s.list_audit()]
    assert not any("limit" in t for t in MANAGER_TOOLS)       # no tool lets the shopkeeper change them


def test_api_owner_only(monkeypatch):
    monkeypatch.delenv("MICROMARKET_ALLOW_OPEN_ADMIN", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "own")
    monkeypatch.setenv("STAFF_TOKEN", "stf")
    s = store()
    c = TestClient(create_app(s, Shopkeeper(s, client=FakeClient([])), auto_order_interval_s=0))
    body = {"per_order_cents": 80_000, "daily_cents": 200_000}
    assert c.put("/api/admin/spending-limits", json=body, headers={"X-Admin-Token": "stf"}).status_code == 401
    r = c.put("/api/admin/spending-limits", json=body, headers={"X-Admin-Token": "own"})
    assert r.status_code == 200 and r.json()["per_order_cents"] == 80_000
    bad = c.put("/api/admin/spending-limits", json={"daily_cents": 1}, headers={"X-Admin-Token": "own"})
    assert bad.status_code == 400
    plan = c.get("/api/admin/autoorder", headers={"X-Admin-Token": "own"}).json()["plan"]
    assert plan["limits"]["daily_cents"] == 200_000 and plan["spent_today_cents"] == 0


def test_old_restocks_table_gains_ordered_by(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE restocks(id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')),
        product_id INTEGER NOT NULL, qty INTEGER NOT NULL, unit_cost_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
        INSERT INTO restocks(product_id, qty, unit_cost_cents) VALUES (1, 10, 80);""")
    c.commit()
    c.close()
    db = connect(str(p))
    assert db.execute("SELECT ordered_by FROM restocks").fetchone()[0] == "staff"   # old orders count as manual
