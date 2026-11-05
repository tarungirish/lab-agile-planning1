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
from typing import Literal

from pydantic import BaseModel, Field

from .agent import Shopkeeper
from .db import connect
from .facilities import SEVERITIES
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
    items: list[CartLine] = Field(min_length=1, max_length=50)
    payment_method: Literal["card", "cash"] = "card"
    customer: str | None = Field(None, max_length=80)


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


class PublicIssueIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str | None = Field(None, max_length=500)
    reporter: str | None = Field(None, max_length=80)
    asset_id: int | None = None


class IssueIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str | None = Field(None, max_length=500)
    severity: str = "normal"
    asset_id: int | None = None
    reported_by: str | None = Field(None, max_length=80)
    take_out_of_service: bool = False


class IssueUpdateIn(BaseModel):
    status: str
    resolution: str | None = Field(None, max_length=300)


class CleanDoneIn(BaseModel):
    done_by: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(None, max_length=300)


class CleanTaskIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    frequency_hours: float = Field(gt=0)
    area: str = "general"
    instructions: str | None = Field(None, max_length=300)


class AssetIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = "equipment"
    location: str = "market"
    charger_id: int | None = None
    notes: str | None = Field(None, max_length=300)


class TaskIn(BaseModel):
    task: str | None = Field(None, max_length=2000)


# Inline <script> blocks and onclick handlers need 'unsafe-inline'; everything else is same-origin only,
# so injected code still can't load external scripts or send data to another host.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}


def create_app(store: Store | None = None, shopkeeper: Shopkeeper | None = None,
               auto_order_interval_s: float | None = None,
               agent_review_interval_s: float | None = None) -> FastAPI:
    store = store or Store(connect())
    agent = shopkeeper or Shopkeeper(store)
    charging = agent.charging
    auto = agent.autoorder
    fac = agent.facilities
    room = auto.stockroom
    if auto_order_interval_s is None:
        auto_order_interval_s = float(os.environ.get("AUTO_ORDER_INTERVAL_MIN", "60")) * 60
    if agent_review_interval_s is None:  # off by default: every review is a model call that costs money
        agent_review_interval_s = float(os.environ.get("AGENT_REVIEW_INTERVAL_MIN", "0")) * 60

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async def every(seconds, fn):
            while True:
                await asyncio.sleep(seconds)
                try:  # never let one failure (e.g. the model API being down) kill the loop
                    await asyncio.to_thread(fn)
                except Exception:
                    pass
        tasks = []
        if auto_order_interval_s > 0:      # only acts when the owner set auto-ordering to 'auto'
            tasks.append(asyncio.create_task(every(auto_order_interval_s, auto.run_if_auto)))
        if agent_review_interval_s > 0:    # the shopkeeper's routine review on a schedule
            tasks.append(asyncio.create_task(every(agent_review_interval_s, agent.run_manager)))
        yield
        for t in tasks:
            t.cancel()

    # The kiosk is public, so don't publish the API schema (it lists every admin route).
    app = FastAPI(title="Micromarket POS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    owner_path = "/" + os.environ.get("OWNER_PATH", "owner").strip("/")
    staff_path = "/" + os.environ.get("STAFF_PATH", "staff").strip("/")
    issue_limit = RateLimiter(limit=3, window_s=60)       # public problem reports
    chat_limit = RateLimiter(limit=5, window_s=60)        # each message can cost model tokens
    login_fails = RateLimiter(limit=5, window_s=300)      # lock an IP out after 5 bad tokens

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    def _authenticate(request: Request, token: str | None, env_names: list[str]) -> str:
        """Returns the name of the env var whose token matched (e.g. 'ADMIN_TOKEN')."""
        expected = [(n, os.environ[n]) for n in env_names if os.environ.get(n)]
        if not expected:
            # Fail closed: a public kiosk must never expose private routes by accident.
            if os.environ.get("MICROMARKET_ALLOW_OPEN_ADMIN") == "1":
                return "ADMIN_TOKEN"  # explicit local-dev escape hatch
            raise HTTPException(503, "Access is disabled: set the ADMIN_TOKEN environment variable")
        ip = request.client.host if request.client else "?"
        if login_fails.blocked(ip):
            raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
        given = (token or "").encode()
        matched = [n for n, e in expected if secrets.compare_digest(given, e.encode())]  # check all: constant time
        if not matched:
            login_fails.hit(ip)
            raise HTTPException(401, "Access token required")
        return "ADMIN_TOKEN" if "ADMIN_TOKEN" in matched else matched[0]

    def admin(request: Request, x_admin_token: str | None = Header(None)):
        _authenticate(request, x_admin_token, ["ADMIN_TOKEN"])

    def staff(request: Request, x_admin_token: str | None = Header(None)) -> str:
        # Cleaners/floor staff: STAFF_TOKEN reaches only /api/staff/*; the owner token works there too.
        return "owner" if _authenticate(request, x_admin_token, ["STAFF_TOKEN", "ADMIN_TOKEN"]) == "ADMIN_TOKEN" else "staff"

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

    @app.get(staff_path, include_in_schema=False)
    def staff_page():  # cleaners' page: cleaning checklist + issues only
        return FileResponse(STATIC / "staff.html", headers={
            "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})

    @app.get("/api/assets")
    def public_assets():  # for the kiosk's "report a problem" picker; no status or notes
        return [{"id": a["id"], "name": a["name"], "location": a["location"]} for a in fac.list_assets()]

    @app.post("/api/issues")
    def report_problem(body: PublicIssueIn, request: Request):
        key = request.client.host if request.client else "?"
        if issue_limit.blocked(key):
            raise HTTPException(429, "Thanks! Please wait a minute before sending another report.")
        issue_limit.hit(key)
        # Public reports are always 'normal' and can never take equipment offline.
        i = fac.report_issue(body.title, body.description, "normal", body.asset_id,
                             body.reporter, source="kiosk", actor="kiosk")
        return {"id": i["id"]}

    @app.get("/api/chargers")
    def chargers():
        return {"price_cents_per_kwh": charging.price_cents_per_kwh(),
                "chargers": [{k: c[k] for k in ("id", "name", "location", "power_kw", "status")}
                             for c in charging.list_chargers()]}

    @app.post("/api/chargers/{charger_id}/start")
    def charge_start(charger_id: int, body: StartIn):
        s = charging.start(charger_id, body.customer)
        return {k: s[k] for k in ("id", "charger", "started_at", "price_cents_per_kwh")}

    # --- staff (cleaners / floor staff): cleaning + issues only ---
    @app.get("/api/staff/overview", dependencies=[Depends(staff)])
    def staff_overview():
        return {"cleaning": fac.list_cleaning(), "issues": fac.list_issues(),
                "assets": [{"id": a["id"], "name": a["name"]} for a in fac.list_assets()]}

    @app.post("/api/staff/cleaning/{task_id}/done", dependencies=[Depends(staff)])
    def staff_clean_done(task_id: int, body: CleanDoneIn):
        return fac.log_cleaning(task_id, body.done_by, body.notes)  # also closes the shopkeeper's reminder

    @app.post("/api/staff/issues", dependencies=[Depends(staff)])
    def staff_issue(body: IssueIn):
        if body.severity == "critical" or body.take_out_of_service:  # taking equipment offline is the owner's call
            raise HTTPException(403, "Ask the owner to mark an issue critical or take equipment out of service")
        return fac.report_issue(body.title, body.description, body.severity, body.asset_id,
                                body.reported_by, source="staff")

    @app.put("/api/staff/issues/{issue_id}")
    def staff_issue_update(issue_id: int, body: IssueUpdateIn, role: str = Depends(staff)):
        if role == "staff" and body.status == "resolved":
            iss = fac.get_issue(issue_id)
            down = iss["asset_id"] is not None and fac._asset(iss["asset_id"])["status"] == "out_of_service"
            if iss["severity"] == "critical" or down:
                # Closing it would put the equipment (e.g. an EV charger) back in service: owner only.
                raise HTTPException(403, "Only the owner can close critical issues or bring equipment back into service")
        return fac.update_issue(issue_id, body.status, body.resolution, actor=role)

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

    @app.get("/api/admin/facilities", dependencies=[Depends(admin)])
    def facilities_state():
        return {**fac.summary(), "history": fac.cleaning_history(),
                "chargers": [{"id": c["id"], "name": c["name"]} for c in auto.stockroom.store.db.execute(
                    "SELECT id, name FROM chargers ORDER BY name")]}

    @app.post("/api/admin/cleaning-tasks", dependencies=[Depends(admin)])
    def add_clean_task(body: CleanTaskIn):
        return fac.add_task(body.name, body.frequency_hours, body.area, body.instructions)

    @app.put("/api/admin/cleaning-tasks/{tid}/active", dependencies=[Depends(admin)])
    def clean_task_active(tid: int, active: bool):
        fac.set_task_active(tid, active)
        return {"ok": True}

    @app.post("/api/admin/assets", dependencies=[Depends(admin)])
    def add_asset(body: AssetIn):
        return fac.add_asset(body.name, body.kind, body.location, body.charger_id, body.notes)

    @app.post("/api/admin/issues", dependencies=[Depends(admin)])
    def admin_issue(body: IssueIn):
        return fac.report_issue(body.title, body.description, body.severity, body.asset_id,
                                body.reported_by, source="owner", take_out_of_service=body.take_out_of_service)

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
