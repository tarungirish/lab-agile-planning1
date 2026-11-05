"""FastAPI app: kiosk (public) + admin (token-protected when ADMIN_TOKEN is set)."""
from __future__ import annotations

import os
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Shopkeeper
from .db import connect
from .store import Store, StoreError

STATIC = Path(__file__).parent / "static"


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


class TaskIn(BaseModel):
    task: str | None = Field(None, max_length=2000)


def create_app(store: Store | None = None, shopkeeper: Shopkeeper | None = None) -> FastAPI:
    store = store or Store(connect())
    agent = shopkeeper or Shopkeeper(store)
    charging = agent.charging
    app = FastAPI(title="Micromarket POS")

    def admin(x_admin_token: str | None = Header(None)):
        expected = os.environ.get("ADMIN_TOKEN")
        if expected and x_admin_token != expected:
            raise HTTPException(401, "Admin token required")

    @app.exception_handler(StoreError)
    async def _store_error(_, exc: StoreError):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    # --- kiosk (public) ---
    @app.get("/api/products")
    def products():
        return [{k: p[k] for k in ("sku", "name", "price_cents", "stock")}
                for p in store.list_products()]

    @app.post("/api/checkout")
    def checkout(body: CheckoutIn):
        return store.checkout([l.model_dump() for l in body.items], body.payment_method, body.customer)

    @app.post("/api/messages")
    def message(body: MessageIn):
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
    def receive(rid: int):
        return store.receive_restock(rid)

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
