import pytest
from micromarket.charging import Charging
from micromarket.db import connect
from micromarket.facilities import Facilities
from micromarket.store import Store, StoreError

NOW = "2026-11-05 12:00:00"


@pytest.fixture
def fac():
    return Facilities(Store(connect(":memory:")))


def status_of(fac, name, now=NOW):
    return next(c for c in fac.list_cleaning(now) if c["name"] == name)


def test_cleaning_status_ok_due_soon_overdue_never(fac):
    t = fac.add_task("Wipe shelves", 24, "shelves")
    assert status_of(fac, "Wipe shelves")["status"] == "overdue"           # never done
    fac.log_cleaning(t["id"], "Sam", now="2026-11-05 11:00:00")             # 1 h ago
    assert status_of(fac, "Wipe shelves")["status"] == "ok"
    fac.log_cleaning(t["id"], "Sam", now="2026-11-04 16:00:00")             # older log must not win
    assert status_of(fac, "Wipe shelves")["status"] == "ok"
    s = status_of(fac, "Wipe shelves", "2026-11-06 06:00:00")               # due 11:00 -> 5 h left (<6 h)
    assert s["status"] == "due_soon"
    s = status_of(fac, "Wipe shelves", "2026-11-06 12:00:00")               # 1 h late
    assert s["status"] == "overdue" and s["hours_overdue"] == 1.0 and s["last_by"] == "Sam"


def test_overdue_sorted_first_and_inactive_hidden(fac):
    a = fac.add_task("Empty bins", 12)
    b = fac.add_task("Deep clean fridge", 168)
    c = fac.add_task("Retired task", 24)
    fac.log_cleaning(b["id"], "Sam", now="2026-11-05 10:00:00")
    fac.set_task_active(c["id"], False)
    names = [x["name"] for x in fac.list_cleaning(NOW)]
    assert names == ["Empty bins", "Deep clean fridge"]                       # overdue before ok; retired hidden
    with pytest.raises(StoreError, match="switched off"):
        fac.log_cleaning(c["id"], "Sam")


def test_log_needs_a_name_and_task_validation(fac):
    t = fac.add_task("X", 24)
    with pytest.raises(StoreError):
        fac.log_cleaning(t["id"], "  ")
    with pytest.raises(StoreError):
        fac.add_task("Bad", 0)
    with pytest.raises(StoreError, match="already exists"):
        fac.add_task("X", 12)


def test_compliance_is_capped_per_task(fac):
    t = fac.add_task("Daily wipe", 24, now="2026-10-01 00:00:00")
    days = ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    for d in days:                                                             # 7 of 14 expected
        fac.log_cleaning(t["id"], "Sam", now=f"{d} 09:00:00")
    c = fac.compliance(14, NOW)
    assert c["tasks"][0]["done"] == 7 and c["tasks"][0]["rate"] == 0.5 and c["overall"] == 0.5
    u = fac.add_task("Hourly", 1, now="2026-10-01 00:00:00")
    for h in range(0, 400):
        fac.db.execute("INSERT INTO cleaning_logs(task_id,ts,done_by) VALUES (?, '2026-11-05 11:00:00', 'x')", (u["id"],))
    assert next(x for x in fac.compliance(14, NOW)["tasks"] if x["task"] == "Hourly")["rate"] == 1.0


def test_compliance_only_counts_time_the_task_has_existed(fac):
    fac.add_task("Brand new", 24, now="2026-11-05 10:00:00")                    # 2 h old: not measurable yet
    c = fac.compliance(14, NOW)
    assert c["overall"] is None and c["tasks"] == []
    t = fac.add_task("Three days old", 24, now="2026-11-02 12:00:00")           # 3 full intervals so far
    for d in ("2026-11-03", "2026-11-04", "2026-11-05"):
        fac.log_cleaning(t["id"], "Sam", now=f"{d} 09:00:00")
    c = fac.compliance(14, NOW)
    assert c["overall"] == 1.0 and c["tasks"][0]["expected"] == 3.0
    fac.add_task("Missed", 24, now="2026-11-02 12:00:00")                       # same age, never done
    assert fac.compliance(14, NOW)["overall"] == 0.5


def test_issue_severity_drives_asset_status(fac):
    a = fac.add_asset("Fridge 1", "fridge", "market")
    fac.report_issue("Door seal loose", asset_id=a["id"], severity="low")
    assert fac._asset(a["id"])["status"] == "ok"
    fac.report_issue("Not cooling well", asset_id=a["id"], severity="high")
    assert fac._asset(a["id"])["status"] == "needs_attention"
    fac.report_issue("Dead", asset_id=a["id"], severity="critical")
    assert fac._asset(a["id"])["status"] == "out_of_service"
    with pytest.raises(StoreError):
        fac.report_issue("x", severity="apocalyptic")


def test_critical_issue_on_charger_asset_takes_charger_offline_and_resolving_restores(fac):
    ch = Charging(fac.store)
    c = ch.add_charger("Lounge 1", 22, "lounge")
    a = fac.add_asset("Lounge 1 charger", "charger", "lounge", charger_id=c["id"])
    i1 = fac.report_issue("Cable cut", asset_id=a["id"], severity="critical")
    assert not ch.list_chargers()[0]["in_service"] and ch.list_chargers()[0]["status"] == "out_of_service"
    i2 = fac.report_issue("Screen cracked", asset_id=a["id"], severity="normal")
    fac.update_issue(i1["id"], "resolved", "Replaced cable")
    assert fac._asset(a["id"])["status"] == "out_of_service"                  # i2 still open
    fac.update_issue(i2["id"], "resolved")
    assert fac._asset(a["id"])["status"] == "ok" and ch.list_chargers()[0]["in_service"]


def test_issue_lifecycle_rules(fac):
    i = fac.report_issue("Spill near entrance", reported_by="amy", source="kiosk")
    assert i["status"] == "open" and i["source"] == "kiosk"
    fac.update_issue(i["id"], "in_progress")
    done = fac.update_issue(i["id"], "resolved", "Mopped", now=NOW)
    assert done["resolved_at"] == NOW and done["resolution"] == "Mopped"
    with pytest.raises(StoreError, match="reopened"):
        fac.update_issue(i["id"], "open")
    assert fac.list_issues() == [] and len(fac.list_issues(include_resolved=True)) == 1


def test_issues_listed_most_severe_first_and_summary(fac):
    fac.report_issue("minor", severity="low")
    fac.report_issue("big", severity="critical")
    fac.report_issue("mid", severity="normal")
    assert [i["title"] for i in fac.list_issues()] == ["big", "mid", "minor"]
    fac.add_task("Wipe", 24)
    s = fac.summary(NOW)
    assert s["open_issues"] == 3 and s["critical_issues"] == 1 and s["overdue"] == 1


# ---- API, staff scope, public reporting, agent tools -------------------------------------
from fastapi.testclient import TestClient
from micromarket.agent import Shopkeeper
from micromarket.app import create_app
from test_agent_api import FakeClient, text, tool


def api(monkeypatch, script=None, **env):
    monkeypatch.delenv("MICROMARKET_ALLOW_OPEN_ADMIN", raising=False)
    for k in ("ADMIN_TOKEN", "STAFF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    store = Store(connect(":memory:"))
    sk = Shopkeeper(store, client=FakeClient(script or []))
    sk.facilities.add_task("Wipe shelves", 24, "market")
    sk.facilities.add_asset("Fridge 1", "fridge")
    return TestClient(create_app(store, sk, auto_order_interval_s=0)), sk


OWNER, STAFF = {"X-Admin-Token": "own"}, {"X-Admin-Token": "stf"}


def test_staff_token_reaches_cleaning_and_issues_but_not_owner_routes(monkeypatch):
    c, _ = api(monkeypatch, ADMIN_TOKEN="own", STAFF_TOKEN="stf")
    ov = c.get("/api/staff/overview", headers=STAFF).json()
    assert ov["cleaning"][0]["status"] == "overdue"
    done = c.post(f"/api/staff/cleaning/{ov['cleaning'][0]['id']}/done", headers=STAFF, json={"done_by": "Sam"})
    assert done.status_code == 200
    assert c.get("/api/staff/overview", headers=STAFF).json()["cleaning"][0]["status"] == "ok"
    for path in ("/api/admin/summary", "/api/admin/facilities", "/api/admin/autoorder", "/api/admin/audit"):
        assert c.get(path, headers=STAFF).status_code == 401, path
    assert c.get("/api/staff/overview", headers=OWNER).status_code == 200   # owner token also works


def test_staff_cannot_mark_critical_or_take_equipment_offline(monkeypatch):
    c, _ = api(monkeypatch, ADMIN_TOKEN="own", STAFF_TOKEN="stf")
    body = {"title": "Fridge dead", "severity": "critical", "asset_id": 1}
    assert c.post("/api/staff/issues", headers=STAFF, json=body).status_code == 403
    assert c.post("/api/staff/issues", headers=STAFF, json={**body, "severity": "high",
                                                           "take_out_of_service": True}).status_code == 403
    assert c.post("/api/staff/issues", headers=STAFF, json={**body, "severity": "high"}).status_code == 200
    assert c.post("/api/admin/issues", headers=OWNER, json=body).status_code == 200   # owner can


def test_staff_routes_fail_closed_with_no_tokens(monkeypatch):
    c, _ = api(monkeypatch)
    assert c.get("/api/staff/overview").status_code == 503


def test_staff_page_is_separate_and_kiosk_has_no_staff_code(monkeypatch):
    c, _ = api(monkeypatch, ADMIN_TOKEN="own", STAFF_TOKEN="stf")
    r = c.get("/staff")
    assert r.status_code == 200 and "api/staff" in r.text and "noindex" in r.headers["x-robots-tag"]
    assert "api/staff" not in c.get("/").text and "api/admin" not in r.text


def test_public_problem_report_is_normal_severity_rate_limited_and_cannot_set_severity(monkeypatch):
    c, sk = api(monkeypatch, ADMIN_TOKEN="own")
    r = c.post("/api/issues", json={"title": "Fridge is leaking", "asset_id": 1, "reporter": "amy",
                                   "severity": "critical", "take_out_of_service": True})
    assert r.status_code == 200
    i = sk.facilities.list_issues()[0]
    assert i["severity"] == "normal" and i["source"] == "kiosk" and sk.facilities._asset(1)["status"] == "ok"
    codes = [c.post("/api/issues", json={"title": "Spill by the door"}).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]       # 3 per minute including the first
    assert c.post("/api/issues", json={"title": "x" * 500}).status_code in (422, 429)
    pub = c.get("/api/assets").json()[0]
    assert set(pub) == {"id", "name", "location"}


def test_admin_facilities_summary_and_charger_link(monkeypatch):
    c, sk = api(monkeypatch, ADMIN_TOKEN="own")
    ch = sk.charging.add_charger("L1", 22, "lounge")
    a = c.post("/api/admin/assets", headers=OWNER, json={"name": "L1 charger", "kind": "charger",
                                                          "location": "lounge", "charger_id": ch["id"]}).json()
    c.post("/api/admin/issues", headers=OWNER, json={"title": "Cable cut", "severity": "critical", "asset_id": a["id"]})
    st = c.get("/api/admin/facilities", headers=OWNER).json()
    assert st["critical_issues"] == 1 and st["assets_down"] == 1 and st["chargers"][0]["name"] == "L1"
    assert c.get("/api/chargers").json()["chargers"][0]["status"] == "out_of_service"


def test_agent_facilities_tools_and_limits(monkeypatch):
    store = Store(connect(":memory:"))
    client = FakeClient([tool("report_issue", title="Fridge warm", severity="critical", asset="Fridge 1"), text("ok"),
                         tool("report_issue", title="Fridge warm", severity="high", asset="fridge 1"), text("ok")])
    sk = Shopkeeper(store, client=client)
    sk.facilities.add_asset("Fridge 1", "fridge")
    sk.run_manager()
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] and sk.facilities.list_issues() == []
    sk.run_manager()
    i = sk.facilities.list_issues()[0]
    assert i["severity"] == "high" and i["source"] == "agent" and i["asset"] == "Fridge 1"
    c2 = FakeClient([tool("report_problem", title="Spill near fridge"), text("Thanks, passed on."),
                     tool("report_issue", title="x", severity="high"), text("no")])
    csk = Shopkeeper(store, client=c2)
    csk.reply_to_customer(store.add_message("eve", "spill"), "eve", "x")
    assert [x["source"] for x in csk.facilities.list_issues()].count("customer-chat") == 1
    c3 = FakeClient([tool("report_issue", title="x", severity="high"), text("no")])
    Shopkeeper(store, client=c3).reply_to_customer(store.add_message("eve", "y"), "eve", "y")
    assert c3.calls[1]["messages"][-1]["content"][0]["is_error"]       # customer chat can't use report_issue
