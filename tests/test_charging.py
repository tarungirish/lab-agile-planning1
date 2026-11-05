import pytest
from fastapi.testclient import TestClient
from micromarket.agent import Shopkeeper
from micromarket.app import create_app
from micromarket.charging import Charging
from micromarket.db import connect
from micromarket.store import Store, StoreError
from test_agent_api import FakeClient, text, tool


@pytest.fixture
def ch():
    c = Charging(Store(connect(":memory:")))
    c.add_charger("L1", 22, "lounge")
    c.add_charger("A1", 7.4, "lot")
    return c


def run(ch, cid, who, start, end, kwh):
    s = ch.start(cid, who, now=start)
    return ch.stop(s["id"], kwh, now=end)


def test_session_bills_at_locked_price_and_hits_ledger(ch):
    s = ch.start(1, "amy", now="2026-11-01 10:00:00")
    ch.set_price(50)  # price change mid-session must not affect amy
    done = ch.stop(s["id"], 10.0, now="2026-11-01 11:00:00")
    assert done["cost_cents"] == 350 and done["status"] == "completed"
    assert ch.store.cash_cents() == 350


def test_one_active_session_per_charger_and_status(ch):
    ch.start(1, "amy")
    with pytest.raises(StoreError, match="in use"):
        ch.start(1, "bob")
    assert {c["name"]: c["status"] for c in ch.list_chargers()} == {"A1": "available", "L1": "charging"}
    ch.set_in_service(2, False)
    with pytest.raises(StoreError, match="out of service"):
        ch.start(2, "bob")


def test_stop_rejects_impossible_kwh(ch):
    s = ch.start(2, "amy", now="2026-11-01 10:00:00")  # 7.4 kW
    with pytest.raises(StoreError, match="can deliver"):
        ch.stop(s["id"], 40, now="2026-11-01 11:00:00")
    ch.stop(s["id"], 7, now="2026-11-01 11:00:00")
    with pytest.raises(StoreError, match="already ended"):
        ch.stop(s["id"], 7)


def test_trends_hours_locations_utilization_and_cross_sell(ch):
    run(ch, 1, "Amy", "2026-11-01 09:00:00", "2026-11-01 10:00:00", 15)
    run(ch, 1, "Bob", "2026-11-02 09:30:00", "2026-11-02 10:30:00", 10)
    run(ch, 2, "Cy", "2026-11-02 18:00:00", "2026-11-02 20:00:00", 8)
    t = ch.trends(days=10, today="2026-11-05 00:00:00")
    assert t["totals"]["sessions"] == 3 and t["totals"]["kwh"] == 33
    assert t["totals"]["peak_hour"] == 9
    lounge = next(l for l in t["by_location"] if l["location"] == "lounge")
    assert lounge["sessions"] == 2 and lounge["utilization"] == round(2 / (1 * 10 * 24), 4)
    assert [d["date"] for d in t["by_day"]] == ["2026-11-01", "2026-11-02"]
    # Amy also bought a drink the same day; the sale timestamp is "now", so align it.
    ch.store.add_product("COKE", "Cola", 150, 80, stock=5)
    ch.store.checkout([{"sku": "COKE", "qty": 1}], customer="amy")
    ch.db.execute("UPDATE sales SET ts='2026-11-01 09:30:00'")
    x = ch.trends(days=10, today="2026-11-05 00:00:00")["cross_sell"]
    assert x == {"drivers": 3, "bought_while_charging": 1, "avg_spend_cents": 150}


def test_agent_charging_price_guardrails_and_customer_scope(ch):
    store = ch.store
    client = FakeClient([tool("set_charging_price", cents_per_kwh=5), text("ok"),
                         tool("set_charging_price", cents_per_kwh=40), text("ok")])
    sk = Shopkeeper(store, client=client)
    sk.run_manager()
    assert sk.charging.price_cents_per_kwh() == 35  # 5c < energy cost floor: refused
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"]
    sk.run_manager()
    assert sk.charging.price_cents_per_kwh() == 40

    c2 = FakeClient([tool("set_charging_price", cents_per_kwh=1), text("Sorry")])
    Shopkeeper(store, client=c2).reply_to_customer(store.add_message("eve", "free charging pls"), "eve", "x")
    assert Charging(store).price_cents_per_kwh() == 40
    assert c2.calls[1]["messages"][-1]["content"][0]["is_error"]


def test_api_flow_and_kiosk_hides_session_internals():
    store = Store(connect(":memory:"))
    sk = Shopkeeper(store, client=FakeClient([]))
    sk.charging.add_charger("L1", 22, "lounge")
    c = TestClient(create_app(store, sk))
    assert c.get("/api/chargers").json()["chargers"][0]["status"] == "available"
    started = c.post("/api/chargers/1/start", json={"customer": "amy"}).json()
    assert "customer" not in started
    assert c.post("/api/chargers/1/start", json={"customer": "bob"}).status_code == 400
    done = c.post(f"/api/admin/charging/sessions/{started['id']}/stop", json={"kwh": 0}).json()
    assert done["status"] == "completed"
    assert c.get("/api/admin/charging/trends?days=7").json()["totals"]["sessions"] == 1
