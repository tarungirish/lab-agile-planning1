"""Automatic reordering.

plan()  - pure read: what should be ordered right now, and why (or why not).
run()   - places the plan's orders as pending restocks (actor 'auto', so the Policy
          spend cap and cash reserve are enforced by the Store, not just by the planner).

Modes (stored in settings): off | suggest | auto. The background scheduler only acts in 'auto'.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from .stockroom import Stockroom
from .store import Store, StoreError

FMT = "%Y-%m-%d %H:%M:%S"
MODES = ("off", "suggest", "auto")
VELOCITY_WINDOW_DAYS = 14
SAFETY_DAYS = 1.0
SHELF_COVER_DAYS = 1.0   # keep at least a day of sales on the shelf between restocking visits
MAX_PAR = 500


class AutoOrder:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.stockroom = Stockroom(store)

    # ---- mode / rules ----------------------------------------------------
    def mode(self) -> str:
        r = self.db.execute("SELECT value FROM settings WHERE key='auto_order_mode'").fetchone()
        return r["value"] if r else "suggest"

    def set_mode(self, mode: str, actor: str = "staff") -> str:
        if mode not in MODES:
            raise StoreError(f"Mode must be one of {', '.join(MODES)}")
        with self.store._tx():
            self.db.execute("INSERT INTO settings(key,value) VALUES('auto_order_mode',?)"
                            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))
            self.store._audit(actor, "auto_order_mode", mode)
        return mode

    def set_rule(self, sku: str, par_level: int, pack_size: int = 1, lead_time_days: float = 2,
                 supplier: str | None = None, enabled: bool = True, actor: str = "staff") -> dict:
        p = self.store._product(sku)
        if not (0 < par_level <= MAX_PAR) or pack_size <= 0 or lead_time_days < 0:
            raise StoreError(f"Invalid rule (par must be 1-{MAX_PAR}, pack size > 0, lead time >= 0)")
        old = self.db.execute("SELECT par_level FROM reorder_rules WHERE product_id=?", (p["id"],)).fetchone()
        if actor != "staff" and old and par_level > old["par_level"] * 2:
            raise StoreError(f"Refused: par level can at most double in one step (was {old['par_level']})")
        if actor != "staff" and not old and par_level > 100:
            raise StoreError("Refused: new rules from the agent are limited to par 100")
        with self.store._tx():
            self.db.execute(
                "INSERT INTO reorder_rules(product_id,par_level,pack_size,lead_time_days,supplier,enabled)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET par_level=excluded.par_level,"
                " pack_size=excluded.pack_size, lead_time_days=excluded.lead_time_days,"
                " supplier=excluded.supplier, enabled=excluded.enabled",
                (p["id"], par_level, pack_size, lead_time_days, supplier, int(enabled)))
            self.store._audit(actor, "set_reorder_rule",
                              f"{sku}: par {par_level}, pack {pack_size}, lead {lead_time_days}d, enabled={enabled}")
        return self.get_rule(sku)

    def get_rule(self, sku: str) -> dict:
        r = self.db.execute("SELECT r.*, p.sku FROM reorder_rules r JOIN products p ON p.id=r.product_id"
                            " WHERE p.sku=?", (sku,)).fetchone()
        if not r:
            raise StoreError(f"No reorder rule for {sku}")
        return dict(r)

    def list_rules(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT r.*, p.sku, p.name FROM reorder_rules r JOIN products p ON p.id=r.product_id ORDER BY p.name")]

    # ---- planning -----------------------------------------------------------
    def _plan_transfers(self, t: datetime) -> list[dict]:
        """Shelf -> stockroom pick list: top shelves back up to par from stockroom stock (free, no cash)."""
        out = []
        for r in self.db.execute(
                "SELECT s.*, p.sku, p.name, p.stock, p.reorder_point FROM shelf_rules s"
                " JOIN products p ON p.id=s.product_id WHERE s.enabled=1 AND p.active=1 ORDER BY p.name"):
            pending_in = self.stockroom.pending_out(r["product_id"])  # already on the pick list
            vel = self._velocity(r["product_id"], t)
            trigger = max(r["reorder_point"], math.ceil(vel * SHELF_COVER_DAYS))
            projected = r["stock"] + pending_in
            need = r["par_level"] - projected
            if projected > trigger or need <= 0:
                continue
            avail = self.stockroom.available(r["product_id"])
            qty = min(need, avail)
            out.append({"sku": r["sku"], "name": r["name"], "shelf": r["stock"], "pending_in": pending_in,
                        "stockroom": self.store.warehouse_qty(r["product_id"]), "available": avail,
                        "velocity_per_day": round(vel, 2), "trigger": trigger, "shelf_par": r["par_level"],
                        "qty": qty, "short_by": need - qty, "status": "move" if qty > 0 else "empty",
                        "reason": (f"Shelf has {r['stock']}" + (f" (+{pending_in} on pick list)" if pending_in else "")
                                   + f", at or below {trigger}; fill to {r['par_level']}"
                                   + ("" if qty == need else
                                      f". Stockroom only has {avail}, short by {need - qty}" if qty else
                                      ". Stockroom is empty for this item"))})
        out.sort(key=lambda l: (l["shelf"] / l["velocity_per_day"] if l["velocity_per_day"] else float("inf"), l["sku"]))
        return out

    def _velocity(self, product_id: int, now: datetime) -> float:
        since = (now - timedelta(days=VELOCITY_WINDOW_DAYS)).strftime(FMT)
        units = self.db.execute(
            "SELECT COALESCE(SUM(i.qty),0) FROM sale_items i JOIN sales s ON s.id=i.sale_id"
            " WHERE i.product_id=? AND s.status='completed' AND s.ts>=?", (product_id, since)).fetchone()[0]
        return units / VELOCITY_WINDOW_DAYS

    def plan(self, now: str | None = None) -> dict:
        t = datetime.strptime(now, FMT) if now else datetime.now(timezone.utc).replace(tzinfo=None)
        pol = self.store.policy
        budget = (self.store.cash_cents() - self.store._pending_commitments()
                  - pol.min_cash_reserve_cents)
        cands = []
        for r in self.db.execute(
                "SELECT r.*, p.sku, p.name, p.stock, p.cost_cents, p.reorder_point, p.active,"
                " COALESCE(w.qty,0) wh, COALESCE(sr.par_level,0) shelf_par FROM reorder_rules r"
                " JOIN products p ON p.id=r.product_id"
                " LEFT JOIN warehouse_stock w ON w.product_id=r.product_id"
                " LEFT JOIN shelf_rules sr ON sr.product_id=r.product_id AND sr.enabled=1"
                " WHERE r.enabled=1 AND p.active=1"):
            total = r["stock"] + r["wh"]  # everything we hold: shelf + stockroom
            on_order = self.db.execute(
                "SELECT COALESCE(SUM(qty),0) FROM restocks WHERE product_id=? AND status='pending'",
                (r["product_id"],)).fetchone()[0]
            vel = self._velocity(r["product_id"], t)
            # Also buy when everything we hold can't fill the shelf to its par: the shelf would stay short.
            trigger = max(r["reorder_point"], math.ceil(vel * (r["lead_time_days"] + SAFETY_DAYS)),
                          r["shelf_par"])
            projected = total + on_order
            if projected > trigger:
                continue
            need = r["par_level"] - projected
            line = {"sku": r["sku"], "name": r["name"], "stock": total, "shelf": r["stock"],
                    "stockroom": r["wh"], "on_order": on_order,
                    "velocity_per_day": round(vel, 2),
                    "days_of_cover": round(total / vel, 1) if vel else None,
                    "trigger": trigger, "par_level": r["par_level"], "unit_cost_cents": r["cost_cents"],
                    "supplier": r["supplier"], "qty": 0, "cost_cents": 0, "status": "skipped", "reason": ""}
            if need <= 0:
                line["reason"] = "Par level is at or below current stock plus on-order; raise par"
                cands.append((float("inf"), line)); continue
            line["_qty"], line["_pack"] = math.ceil(need / r["pack_size"]) * r["pack_size"], r["pack_size"]
            cands.append((line["days_of_cover"] if vel else float("inf"), line))

        # Most urgent first (fewest days of cover), so scarce cash goes to what runs out soonest.
        cands.sort(key=lambda c: (c[0], c[1]["sku"]))
        lines = []
        for _, ln in cands:
            qty, pack, unit = ln.pop("_qty", 0), ln.pop("_pack", 1), ln["unit_cost_cents"]
            if qty:
                note = []
                if unit > 0:
                    cap = pol.max_restock_spend_cents // unit // pack * pack
                    if qty > cap:
                        qty = cap
                        note.append("trimmed to per-order cap")
                    afford = max(budget, 0) // unit // pack * pack
                    if qty > afford:
                        qty = afford
                        note.append("trimmed to available cash")
                if qty <= 0:
                    ln["reason"] = "Skipped: not enough cash above the reserve / per-order cap"
                else:
                    ln.update(qty=qty, cost_cents=qty * unit, status="order",
                              reason=(f"{ln['stock']} in stock ({ln['shelf']} shelf + {ln['stockroom']} stockroom) + {ln['on_order']} on order is at or below "
                                      f"the trigger of {ln['trigger']}; ordering up to par {ln['par_level']}"
                                      + (f" ({'; '.join(note)})" if note else "")))
                    budget -= qty * unit
            lines.append(ln)
        orders = [l for l in lines if l["status"] == "order"]
        return {"mode": self.mode(), "transfers": self._plan_transfers(t), "lines": lines,
                "total_cost_cents": sum(l["cost_cents"] for l in orders),
                "budget_after_cents": budget}

    # ---- execution ----------------------------------------------------------
    def run(self, actor: str = "auto", now: str | None = None, purchases: bool = True) -> dict:
        """Create the plan's pick-list transfers and (if `purchases`) place its vendor orders.
        With purchases=False the order lines are returned under 'awaiting_approval' instead."""
        placed, errors, moved = [], [], []
        plan = self.plan(now)
        for tr in plan["transfers"]:
            if tr["status"] != "move":
                continue
            try:
                x = self.stockroom.create_transfer(tr["sku"], tr["qty"], actor=actor)
                moved.append({"transfer_id": x["id"], "sku": tr["sku"], "qty": tr["qty"]})
            except StoreError as e:
                errors.append({"sku": tr["sku"], "error": str(e)})
        awaiting = [] if purchases else [
            {"sku": l["sku"], "qty": l["qty"], "cost_cents": l["cost_cents"]} for l in plan["lines"] if l["status"] == "order"]
        for ln in plan["lines"] if purchases else []:
            if ln["status"] != "order":
                continue
            try:
                r = self.store.order_restock(ln["sku"], ln["qty"], actor=actor)
                placed.append({"restock_id": r["id"], "sku": ln["sku"], "qty": ln["qty"]})
            except StoreError as e:
                errors.append({"sku": ln["sku"], "error": str(e)})
        if placed or moved or errors:
            self.store._audit(actor, "auto_order_run",
                              f"purchases {len(placed)}, transfers {len(moved)}, errors {len(errors)}")
        return {"placed": placed, "transfers": moved, "errors": errors, "awaiting_approval": awaiting}

    def run_if_auto(self) -> dict | None:
        return self.run() if self.mode() == "auto" else None
