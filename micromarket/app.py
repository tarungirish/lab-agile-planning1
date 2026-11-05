"""FastAPI app: kiosk (public) + admin (token-protected when ADMIN_TOKEN is set)."""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Shopkeeper
from .db import connect
from .store import Store, StoreError

STATIC = Path(__file__).parent / "static"


class RateLimiter:
    """In-memory sliding window per key. Fine for one kiosk process; use a proxy limiter if you scale out."""
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window, self.hits = limit, window_s, defaultdict(deque)

    def _trim(self, key):
        q, now = self.hits[key], time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key) -> bool:
        return len(self._trim(key)) >= self.limit

    def hit(self, key):
        self._trim(key).append(time.monotonic())


class CartLine(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class CheckoutIn(BaseModel):
    items: list[CartLine]
    payment_method: str = "card"
    customer: str | None = None


class ProductIn(BaseModel):
    sku: str
    name: str
    price_cents: int = Field(gt=0)
    cost_cents: int = Field(ge=0)
    stock: int = Field(0, ge=0)
    reorder_point: int = Field(3, ge=0)


class PriceIn(BaseModel):
    price_cents: int


class RestockIn(BaseModel):
    sku: str
    qty: int
    unit_cost_cents: int | None = None


class DepositIn(BaseModel):
    amount_cents: int
    memo: str = "owner deposit"


class MessageIn(BaseModel):
    customer: str = Field(max_length=80)
    body: str = Field(max_length=1000)


class ChargerIn(BaseModel):
    name: str
    power_kw: float = Field(gt=0)
    location: str = "lot"


class StartIn(BaseModel):
    customer: str = Field(max_length=80)


class StopIn(BaseModel):
    kwh: float = Field(ge=0)


class ChargePriceIn(BaseModel):
    cents_per_kwh: int


class ModeIn(BaseModel):
    mode: str


class RuleIn(BaseModel):
    par_level: int = Field(gt=0)
    pack_size: int = Field(1, gt=0)
    lead_time_days: float = Field(2, ge=0)
    supplier: str | None = None
    enabled: bool = True


class ShelfRuleIn(BaseModel):
    par_level: int = Field(gt=0)
    enabled: bool = True


class CountIn(BaseModel):
    sku: str
    location: str
    qty: int = Field(ge=0)
    reason: str = Field("stock count", max_length=200)


class TransferIn(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class TaskIn(BaseModel):
    task: str | None = Field(None, max_length=2000)


def create_app(store: Store | None = None, shopkeeper: Shopkeeper | None = None,
               auto_order_interval_s: float | None = None) -> FastAPI:
    store = store or Store(connect())
    agent = shopkeeper or Shopkeeper(store)
    charging = agent.charging
    auto = agent.autoorder
    room = auto.stockroom
    if auto_order_interval_s is None:
        auto_order_interval_s = float(os.environ.get("AUTO_ORDER_INTERVAL_MIN", "60")) * 60

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async def loop():
            while True:
                await asyncio.sleep(auto_order_interval_s)
                try:  # only acts when mode == 'auto'; never let a failure kill the loop
                    auto.run_if_auto()
                except Exception:
                    pass
        task = asyncio.create_task(loop()) if auto_order_interval_s > 0 else None
        yield
        if task:
            task.cancel()

    # The kiosk is public, so don't publish the API schema (it lists every admin route).
    app = FastAPI(title="Micromarket POS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    owner_path = "/" + os.environ.get("OWNER_PATH", "owner").strip("/")
    chat_limit = RateLimiter(limit=5, window_s=60)        # each message can cost model tokens
    login_fails = RateLimiter(limit=5, window_s=300)      # lock an IP out after 5 bad tokens

    def admin(request: Request, x_admin_token: str | None = Header(None)):
        expected = os.environ.get("ADMIN_TOKEN")
        if not expected:
            # Fail closed: a public kiosk must never expose admin routes by accident.
            if os.environ.get("MICROMARKET_ALLOW_OPEN_ADMIN") == "1":
                return  # explicit local-dev escape hatch
            raise HTTPException(503, "Owner access is disabled: set the ADMIN_TOKEN environment variable")
        ip = request.client.host if request.client else "?"
        if login_fails.blocked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
        if not secrets.compare_digest((x_admin_token or "").encode(), expected.encode()):
            login_fails.hit(ip)
            raise HTTPException(401, "Admin token required")

    @app.exception_handler(StoreError)
    async def _store_error(_, exc: StoreError):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(exc)}, status_code=400)

    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/")
    def kiosk():  # public page: contains no owner UI or code
        return FileResponse(STATIC / "kiosk.html", headers={"Cache-Control": "no-cache"})

    @app.get(owner_path, include_in_schema=False)
    def owner():  # separate URL; the page itself still needs the admin token to do anything
        return FileResponse(STATIC / "owner.html", headers={
            "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})

    # --- kiosk (public) ---
    @app.get("/api/products")
    def products():
        return [{k: p[k] for k in ("sku", "name", "price_cents", "stock")}
                for p in store.list_products()]

    @app.post("/api/checkout")
    def checkout(body: CheckoutIn):
        return store.checkout([l.model_dump() for l in body.items], body.payment_method, body.customer)

    @app.post("/api/messages")
    def message(body: MessageIn, request: Request):
        key = request.client.host if request.client else "?"
        if chat_limit.blocked(key):
            raise HTTPException(429, "Please wait a minute before sending another message.")
        chat_limit.hit(key)
        mid = store.add_message(body.customer, body.body)
        try:
            reply = agent.reply_to_customer(mid, body.customer, body.body)
        except anthropic.APIError:
            reply = None  # message is saved; owner can follow up
        return {"id": mid, "reply": reply}

    @app.get("/api/chargers")
    def chargers():
        return {"price_cents_per_kwh": charging.price_cents_per_kwh(),
                "chargers": [{k: c[k] for k in ("id", "name", "location", "power_kw", "status")}
                             for c in charging.list_chargers()]}

    @app.post("/api/chargers/{charger_id}/start")
    def charge_start(charger_id: int, body: StartIn):
        s = charging.start(charger_id, body.customer)
        return {k: s[k] for k in ("id", "charger", "started_at", "price_cents_per_kwh")}

    # --- admin ---
    @app.get("/api/admin/ping", dependencies=[Depends(admin)])
    def ping():  # login check: one request per attempt, so a typo counts as one failure
        return {"ok": True}

    @app.get("/api/admin/summary", dependencies=[Depends(admin)])
    def summary():
        return store.summary()

    @app.get("/api/admin/products", dependencies=[Depends(admin)])
    def admin_products():
        return store.list_products(include_inactive=True)

    @app.post("/api/admin/products", dependencies=[Depends(admin)])
    def add_product(body: ProductIn):
        return store.add_product(**body.model_dump())

    @app.put("/api/admin/products/{sku}/price", dependencies=[Depends(admin)])
    def set_price(sku: str, body: PriceIn):
        return store.set_price(sku, body.price_cents, actor="staff")

    @app.get("/api/admin/sales", dependencies=[Depends(admin)])
    def sales():
        return store.recent_sales()

    @app.post("/api/admin/sales/{sale_id}/refund", dependencies=[Depends(admin)])
    def refund(sale_id: int):
        return store.refund(sale_id)

    @app.get("/api/admin/restocks", dependencies=[Depends(admin)])
    def restocks():
        return store.list_restocks()

    @app.post("/api/admin/restocks", dependencies=[Depends(admin)])
    def order(body: RestockIn):
        return store.order_restock(body.sku, body.qty, body.unit_cost_cents)

    @app.post("/api/admin/restocks/{rid}/receive", dependencies=[Depends(admin)])
    def receive(rid: int, to: str | None = None):
        return store.receive_restock(rid, to=to)

    @app.post("/api/admin/deposit", dependencies=[Depends(admin)])
    def deposit(body: DepositIn):
        return {"cash_cents": store.deposit(body.amount_cents, body.memo)}

    @app.post("/api/admin/agent/run", dependencies=[Depends(admin)])
    def agent_run(body: TaskIn):
        try:
            return {"report": agent.run_manager(body.task)}
        except anthropic.APIError as e:
            raise HTTPException(502, f"Agent unavailable: {e}")

    @app.get("/api/admin/chargers", dependencies=[Depends(admin)])
    def admin_chargers():
        return {"price_cents_per_kwh": charging.price_cents_per_kwh(),
                "energy_cost_cents_per_kwh": charging.energy_cost_cents_per_kwh(),
                "chargers": charging.list_chargers()}

    @app.post("/api/admin/chargers", dependencies=[Depends(admin)])
    def add_charger(body: ChargerIn):
        return charging.add_charger(body.name, body.power_kw, body.location)

    @app.put("/api/admin/chargers/{charger_id}/service", dependencies=[Depends(admin)])
    def charger_service(charger_id: int, in_service: bool):
        charging.set_in_service(charger_id, in_service)
        return {"ok": True}

    @app.put("/api/admin/charging/price", dependencies=[Depends(admin)])
    def charge_price(body: ChargePriceIn):
        return {"price_cents_per_kwh": charging.set_price(body.cents_per_kwh)}

    @app.get("/api/admin/charging/sessions", dependencies=[Depends(admin)])
    def charge_sessions():
        return charging.recent_sessions()

    @app.post("/api/admin/charging/sessions/{sid}/stop", dependencies=[Depends(admin)])
    def charge_stop(sid: int, body: StopIn):
        return charging.stop(sid, body.kwh)

    @app.get("/api/admin/charging/trends", dependencies=[Depends(admin)])
    def charge_trends(days: int = 30):
        return charging.trends(max(1, min(days, 365)))

    @app.get("/api/admin/stockroom", dependencies=[Depends(admin)])
    def stockroom_state():
        return {"stock": room.list_stock(), "pending": room.list_transfers("pending"),
                "recent": room.list_transfers(limit=15)}

    @app.put("/api/admin/stockroom/shelf-rules/{sku}", dependencies=[Depends(admin)])
    def shelf_rule(sku: str, body: ShelfRuleIn):
        return room.set_shelf_rule(sku, body.par_level, body.enabled)

    @app.post("/api/admin/stockroom/count", dependencies=[Depends(admin)])
    def stock_count(body: CountIn):
        return room.set_count(body.sku, body.location, body.qty, body.reason)

    @app.post("/api/admin/stockroom/transfers", dependencies=[Depends(admin)])
    def transfer_create(body: TransferIn):
        return room.create_transfer(body.sku, body.qty)

    @app.post("/api/admin/stockroom/transfers/complete-all", dependencies=[Depends(admin)])
    def transfer_complete_all():
        done, errors = [], []
        for t in reversed(room.list_transfers("pending")):
            try:
                done.append(room.complete_transfer(t["id"])["id"])
            except StoreError as e:
                errors.append({"id": t["id"], "error": str(e)})
        return {"completed": done, "errors": errors}

    @app.post("/api/admin/stockroom/transfers/{tid}/complete", dependencies=[Depends(admin)])
    def transfer_complete(tid: int):
        return room.complete_transfer(tid)

    @app.post("/api/admin/stockroom/transfers/{tid}/cancel", dependencies=[Depends(admin)])
    def transfer_cancel(tid: int):
        return room.cancel_transfer(tid)

    @app.get("/api/admin/autoorder", dependencies=[Depends(admin)])
    def autoorder_state():
        return {"mode": auto.mode(), "plan": auto.plan(), "rules": auto.list_rules(),
                "products": [{"sku": p["sku"], "name": p["name"]} for p in store.list_products()]}

    @app.put("/api/admin/autoorder/mode", dependencies=[Depends(admin)])
    def autoorder_mode(body: ModeIn):
        return {"mode": auto.set_mode(body.mode)}

    @app.put("/api/admin/autoorder/rules/{sku}", dependencies=[Depends(admin)])
    def autoorder_rule(sku: str, body: RuleIn):
        return auto.set_rule(sku, **body.model_dump())

    @app.post("/api/admin/autoorder/run", dependencies=[Depends(admin)])
    def autoorder_run():
        return auto.run(actor="staff-approved")

    @app.get("/api/admin/notes", dependencies=[Depends(admin)])
    def notes():
        return store.list_notes()

    @app.get("/api/admin/messages", dependencies=[Depends(admin)])
    def messages():
        return store.list_messages()

    @app.get("/api/admin/audit", dependencies=[Depends(admin)])
    def audit():
        return store.list_audit()

    return app
