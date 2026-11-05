import pytest
from micromarket.agent import CUSTOMER_TOOLS, Shopkeeper
from micromarket.autoorder import AutoOrder
from micromarket.demo import BANNER, ScriptedClient, build_demo_store
from micromarket.facilities import Facilities
from micromarket.store import Store


@pytest.fixture(scope="module")
def demo() -> Store:
    return build_demo_store(":memory:")


def test_demo_data_shows_every_interesting_state(demo):
    s = demo.summary()
    assert s["sales_count"] > 100 and s["revenue_cents"] > 0
    plan = AutoOrder(demo).plan()
    assert any(t["status"] == "move" for t in plan["transfers"])          # pick list to show
    assert any(l["status"] == "order" for l in plan["lines"])             # something to buy
    fac = Facilities(demo).summary()
    states = {c["status"] for c in fac["cleaning"]}
    assert {"ok", "due_soon", "overdue"} <= states
    assert fac["open_issues"] >= 2 and 0.5 < fac["compliance"]["overall"] <= 1.0
    sessions = demo.db.execute("SELECT status, COUNT(*) n FROM charging_sessions GROUP BY status").fetchall()
    counts = {r["status"]: r["n"] for r in sessions}
    assert counts["active"] == 1 and counts["completed"] > 20


def test_demo_is_deterministic():
    a, b = build_demo_store(":memory:"), build_demo_store(":memory:")
    assert a.summary()["revenue_cents"] == b.summary()["revenue_cents"]


def test_scripted_manager_run_uses_real_tools_and_changes_nothing_risky():
    store = build_demo_store(":memory:")
    prices = {p["sku"]: p["price_cents"] for p in store.list_products()}
    report = Shopkeeper(store, client=ScriptedClient()).run_manager()
    assert BANNER in report and "Morning review" in report and "Trail mix" in report
    assert {p["sku"]: p["price_cents"] for p in store.list_products()} == prices
    assert store.list_notes()[-1]["body"].startswith("Morning review")                   # a real add_note tool call
    assert any(a["action"] == "manager_run" for a in store.list_audit())


def test_scripted_customer_replies_with_public_facts_only():
    store = build_demo_store(":memory:")
    sk = Shopkeeper(store, client=ScriptedClient())
    mid = store.add_message("eve", "how much is the cold brew?")
    reply = sk.reply_to_customer(mid, "eve", "how much is the cold brew?")
    assert "$3.50" in reply and "demo reply" in reply
    assert "cost" not in reply.lower() and "$2.00" not in reply             # wholesale cost not volunteered
    mid = store.add_message("eve", "is a charger free?")
    assert "chargers are free" in sk.reply_to_customer(mid, "eve", "is a charger free?")


def test_customer_script_only_calls_customer_tools():
    client = ScriptedClient()
    seen = set()
    orig = client._create
    client.messages.create = lambda **kw: (seen.update(t["name"] for t in kw["tools"]), orig(**kw))[1]
    store = build_demo_store(":memory:")
    Shopkeeper(store, client=client).reply_to_customer(store.add_message("x", "hi"), "x", "hi")
    assert seen == set(CUSTOMER_TOOLS)
