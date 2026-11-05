import re
from pathlib import Path

from fastapi.testclient import TestClient
from micromarket.agent import Shopkeeper
from micromarket.app import create_app
from micromarket.db import connect
from micromarket.store import Store
from test_agent_api import FakeClient, text

STATIC = Path(__file__).parent.parent / "micromarket" / "static"


def client(monkeypatch, script=None, **env):
    monkeypatch.delenv("MICROMARKET_ALLOW_OPEN_ADMIN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    store = Store(connect(":memory:"))
    return TestClient(create_app(store, Shopkeeper(store, client=FakeClient(script or [])), auto_order_interval_s=0))


def test_kiosk_page_has_no_owner_ui_or_code():
    served = [STATIC / "kiosk.html", STATIC / "assets" / "common.js", STATIC / "assets" / "common.css"]
    blob = "\n".join(p.read_text() for p in served)
    assert "/api/admin" not in blob
    # No owner UI, owner-only functions, admin header, or links to the owner URL.
    # (Friendly copy such as "the owner will follow up" is fine.)
    for marker in ("loadAdmin", "runAgent", 'id="admin"', "X-Admin-Token", 'href="/owner', "api/staff", 'href="/staff'):
        assert marker not in blob, marker


def test_owner_page_is_separate_and_not_indexable(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="x")
    assert "api/admin" not in c.get("/").text
    r = c.get("/owner")
    assert r.status_code == 200 and "api/admin" in r.text
    assert "noindex" in r.headers["x-robots-tag"] and r.headers["cache-control"] == "no-store"


def test_owner_path_is_configurable(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="x", OWNER_PATH="/back-office-7f3")
    assert c.get("/back-office-7f3").status_code == 200
    assert c.get("/owner").status_code == 404


def test_admin_fails_closed_without_token(monkeypatch):
    c = client(monkeypatch)
    r = c.get("/api/admin/summary")
    assert r.status_code == 503 and "ADMIN_TOKEN" in r.json()["detail"]
    assert c.get("/api/products").status_code == 200  # kiosk keeps working


def test_api_docs_not_published(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="x")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


def test_bad_tokens_lock_out_then_even_good_token_is_refused(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="right")
    for _ in range(5):
        assert c.get("/api/admin/summary", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert c.get("/api/admin/summary", headers={"X-Admin-Token": "right"}).status_code == 429


def test_good_token_works(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="right")
    assert c.get("/api/admin/summary", headers={"X-Admin-Token": "right"}).status_code == 200


def test_chat_is_rate_limited(monkeypatch):
    c = client(monkeypatch, script=[text("hi")] * 6, ADMIN_TOKEN="x")
    codes = [c.post("/api/messages", json={"customer": "a", "body": "hello"}).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2


def test_one_typo_does_not_lock_the_owner_out(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="right")
    assert c.get("/api/admin/ping", headers={"X-Admin-Token": "typo"}).status_code == 401
    assert c.get("/api/admin/ping", headers={"X-Admin-Token": "right"}).status_code == 200


def test_client_ip_ignores_forged_forwarded_for_entries(monkeypatch):
    from starlette.requests import Request as R
    from micromarket.app import client_ip

    def req(xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return R({"type": "http", "headers": headers, "client": ("10.0.0.9", 1234)})

    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert client_ip(req("1.2.3.4")) == "10.0.0.9"                    # no proxy configured: header ignored
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert client_ip(req("6.6.6.6, 203.0.113.7")) == "203.0.113.7"     # forged left entry ignored
    assert client_ip(req("203.0.113.7")) == "203.0.113.7"
    assert client_ip(req()) == "10.0.0.9"


def test_lockout_cannot_be_dodged_by_rotating_forged_ips(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="right", TRUSTED_PROXY_HOPS="1")
    for i in range(5):   # attacker forges a new left-most IP each time; the proxy-added one stays the same
        h = {"X-Admin-Token": "wrong", "X-Forwarded-For": f"9.9.9.{i}, 203.0.113.7"}
        assert c.get("/api/admin/ping", headers=h).status_code == 401
    assert c.get("/api/admin/ping", headers={"X-Admin-Token": "wrong",
                                             "X-Forwarded-For": "9.9.9.99, 203.0.113.7"}).status_code == 429


def test_healthz(monkeypatch):
    c = client(monkeypatch, ADMIN_TOKEN="x")
    assert c.get("/healthz").json() == {"ok": True}
