"""Core POS business logic. Every rule that protects money or stock lives here."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass


class StoreError(Exception):
    """A business-rule violation (bad SKU, out of stock, guardrail, ...)."""


@dataclass(frozen=True)
class Policy:
    """Guardrails applied to the AI agent (staff may bypass with actor='staff').

    Project Vend's shopkeeper lost money by selling below cost and giving
    discounts away; these limits make that impossible by construction.
    """
    min_margin: float = 0.10          # price must be >= cost * (1 + min_margin)
    max_price_change: float = 0.50    # single change limited to +/-50%
    max_restock_spend_cents: int = 10_000  # per order
    min_cash_reserve_cents: int = 2_000    # never spend cash below this


class Store:
    def __init__(self, conn, policy: Policy | None = None):
        self.db = conn
        # The Database's re-entrant lock: held for a whole transaction, and by every single statement.
        self._lock = conn.lock
        self.policy = policy or Policy()

    # ---- helpers -------------------------------------------------------
    @contextmanager
    def _tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _audit(self, actor: str, action: str, detail: str = ""):
        self.db.execute("INSERT INTO audit(actor, action, detail) VALUES (?,?,?)",
                        (actor, action, detail))

    def _product(self, sku: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not row:
            raise StoreError(f"Unknown SKU: {sku}")
        return row

    # ---- catalog -------------------------------------------------------
    def add_product(self, sku: str, name: str, price_cents: int, cost_cents: int,
                    stock: int = 0, reorder_point: int = 3, actor: str = "staff") -> dict:
        if price_cents <= 0 or cost_cents < 0 or stock < 0:
            raise StoreError("Invalid price, cost or stock")
        try:
            with self._tx():
                self.db.execute(
                    "INSERT INTO products(sku,name,price_cents,cost_cents,stock,reorder_point)"
                    " VALUES (?,?,?,?,?,?)",
                    (sku, name, price_cents, cost_cents, stock, reorder_point))
                self._audit(actor, "add_product", f"{sku} {name}")
        except sqlite3.IntegrityError:
            raise StoreError(f"SKU already exists: {sku}")
        return dict(self._product(sku))

    def list_products(self, include_inactive: bool = False) -> list[dict]:
        q = "SELECT * FROM products" + ("" if include_inactive else " WHERE active=1") + " ORDER BY name"
        return [dict(r) for r in self.db.execute(q)]

    def set_price(self, sku: str, price_cents: int, actor: str = "staff") -> dict:
        p = self._product(sku)
        if price_cents <= 0:
            raise StoreError("Price must be positive")
        if actor != "staff":
            floor = int(p["cost_cents"] * (1 + self.policy.min_margin) + 0.999)
            if price_cents < floor:
                raise StoreError(
                    f"Refused: {price_cents}c is below the margin floor of {floor}c "
                    f"(cost {p['cost_cents']}c + {self.policy.min_margin:.0%})")
            change = abs(price_cents - p["price_cents"]) / p["price_cents"]
            if change > self.policy.max_price_change:
                raise StoreError(
                    f"Refused: change of {change:.0%} exceeds the "
                    f"{self.policy.max_price_change:.0%} limit; adjust in steps")
        with self._tx():
            self.db.execute("UPDATE products SET price_cents=? WHERE sku=?", (price_cents, sku))
            self._audit(actor, "set_price", f"{sku}: {p['price_cents']}c -> {price_cents}c")
        return dict(self._product(sku))

    # ---- sales ---------------------------------------------------------
    def checkout(self, items: list[dict], payment_method: str = "card",
                 customer: str | None = None) -> dict:
        """items: [{'sku': str, 'qty': int}]. Atomic: all lines succeed or none."""
        if not items:
            raise StoreError("Cart is empty")
        merged: dict[str, int] = {}
        for it in items:
            qty = int(it["qty"])
            if qty <= 0:
                raise StoreError("Quantity must be positive")
            merged[it["sku"]] = merged.get(it["sku"], 0) + qty
        with self._tx():
            lines, total = [], 0
            for sku, qty in merged.items():
                p = self._product(sku)
                if not p["active"]:
                    raise StoreError(f"{p['name']} is not for sale")
                # Conditional update makes overselling impossible even under races.
                cur = self.db.execute(
                    "UPDATE products SET stock = stock - ? WHERE id=? AND stock >= ?",
                    (qty, p["id"], qty))
                if cur.rowcount == 0:
                    raise StoreError(f"Insufficient stock for {p['name']} (have {p['stock']})")
                lines.append((p, qty))
                total += p["price_cents"] * qty
            sale_id = self.db.execute(
                "INSERT INTO sales(customer,payment_method,total_cents) VALUES (?,?,?)",
                (customer, payment_method, total)).lastrowid
            for p, qty in lines:
                self.db.execute(
                    "INSERT INTO sale_items(sale_id,product_id,qty,unit_price_cents,unit_cost_cents)"
                    " VALUES (?,?,?,?,?)",
                    (sale_id, p["id"], qty, p["price_cents"], p["cost_cents"]))
            self.db.execute("INSERT INTO ledger(kind,amount_cents,memo) VALUES ('sale',?,?)",
                            (total, f"sale #{sale_id}"))
        return self.get_sale(sale_id)

    def get_sale(self, sale_id: int) -> dict:
        s = self.db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not s:
            raise StoreError(f"Unknown sale: {sale_id}")
        items = self.db.execute(
            "SELECT p.sku, p.name, i.qty, i.unit_price_cents FROM sale_items i"
            " JOIN products p ON p.id=i.product_id WHERE i.sale_id=?", (sale_id,))
        return {**dict(s), "items": [dict(i) for i in items]}

    def refund(self, sale_id: int, actor: str = "staff") -> dict:
        with self._tx():
            s = self.db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
            if not s:
                raise StoreError(f"Unknown sale: {sale_id}")
            if s["status"] == "refunded":
                raise StoreError("Sale already refunded")
            for it in self.db.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)):
                self.db.execute("UPDATE products SET stock = stock + ? WHERE id=?",
                                (it["qty"], it["product_id"]))
            self.db.execute("UPDATE sales SET status='refunded' WHERE id=?", (sale_id,))
            self.db.execute("INSERT INTO ledger(kind,amount_cents,memo) VALUES ('refund',?,?)",
                            (-s["total_cents"], f"refund sale #{sale_id}"))
            self._audit(actor, "refund", f"sale #{sale_id}")
        return self.get_sale(sale_id)

    # ---- restocking ----------------------------------------------------
    def cash_cents(self) -> int:
        return self.db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM ledger").fetchone()[0]

    def deposit(self, amount_cents: int, memo: str = "owner deposit", actor: str = "staff"):
        if amount_cents == 0:
            raise StoreError("Amount must be non-zero")
        with self._tx():
            self.db.execute("INSERT INTO ledger(kind,amount_cents,memo) VALUES ('deposit',?,?)",
                            (amount_cents, memo))
            self._audit(actor, "deposit", f"{amount_cents}c {memo}")
        return self.cash_cents()

    def _pending_commitments(self) -> int:
        return self.db.execute(
            "SELECT COALESCE(SUM(qty*unit_cost_cents),0) FROM restocks WHERE status='pending'"
        ).fetchone()[0]

    def order_restock(self, sku: str, qty: int, unit_cost_cents: int | None = None,
                      actor: str = "staff") -> dict:
        p = self._product(sku)
        if qty <= 0:
            raise StoreError("Quantity must be positive")
        unit = p["cost_cents"] if unit_cost_cents is None else unit_cost_cents
        cost = unit * qty
        if actor != "staff":
            if cost > self.policy.max_restock_spend_cents:
                raise StoreError(f"Refused: order of {cost}c exceeds the "
                                 f"{self.policy.max_restock_spend_cents}c per-order limit")
            available = self.cash_cents() - self._pending_commitments() - cost
            if available < self.policy.min_cash_reserve_cents:
                raise StoreError("Refused: order would drop cash below the reserve")
        with self._tx():
            rid = self.db.execute(
                "INSERT INTO restocks(product_id,qty,unit_cost_cents) VALUES (?,?,?)",
                (p["id"], qty, unit)).lastrowid
            self._audit(actor, "order_restock", f"#{rid} {sku} x{qty} @ {unit}c")
        return dict(self.db.execute("SELECT * FROM restocks WHERE id=?", (rid,)).fetchone())

    def warehouse_qty(self, product_id: int) -> int:
        r = self.db.execute("SELECT qty FROM warehouse_stock WHERE product_id=?", (product_id,)).fetchone()
        return r["qty"] if r else 0

    def receive_restock(self, restock_id: int, actor: str = "staff", to: str | None = None) -> dict:
        """Mark a delivery received. `to` is 'stockroom' or 'shelf'. By default stock goes to the
        stockroom for products that have a shelf rule, otherwise straight to the shelf (legacy)."""
        if to not in (None, "stockroom", "shelf"):
            raise StoreError("Destination must be 'stockroom' or 'shelf'")
        with self._tx():
            r = self.db.execute("SELECT * FROM restocks WHERE id=?", (restock_id,)).fetchone()
            if not r:
                raise StoreError(f"Unknown restock: {restock_id}")
            if r["status"] != "pending":
                raise StoreError(f"Restock is already {r['status']}")
            if to is None:
                has_rule = self.db.execute("SELECT 1 FROM shelf_rules WHERE product_id=?",
                                           (r["product_id"],)).fetchone()
                to = "stockroom" if has_rule else "shelf"
            self.db.execute("UPDATE restocks SET status='received' WHERE id=?", (restock_id,))
            p = self.db.execute("SELECT * FROM products WHERE id=?", (r["product_id"],)).fetchone()
            on_hand = p["stock"] + self.warehouse_qty(p["id"])
            # Weighted-average cost over everything we hold (shelf + stockroom) keeps margin math honest.
            avg = round((on_hand * p["cost_cents"] + r["qty"] * r["unit_cost_cents"]) / (on_hand + r["qty"]))
            if to == "shelf":
                self.db.execute("UPDATE products SET stock=stock+?, cost_cents=? WHERE id=?",
                                (r["qty"], avg, p["id"]))
            else:
                self.db.execute("INSERT INTO warehouse_stock(product_id,qty) VALUES (?,?)"
                                " ON CONFLICT(product_id) DO UPDATE SET qty=qty+excluded.qty",
                                (p["id"], r["qty"]))
                self.db.execute("UPDATE products SET cost_cents=? WHERE id=?", (avg, p["id"]))
            self.db.execute("INSERT INTO ledger(kind,amount_cents,memo) VALUES ('restock',?,?)",
                            (-r["qty"] * r["unit_cost_cents"], f"restock #{restock_id}"))
            self._audit(actor, "receive_restock", f"#{restock_id} -> {to}")
        return dict(self.db.execute("SELECT * FROM restocks WHERE id=?", (restock_id,)).fetchone())

    def list_restocks(self, status: str | None = None) -> list[dict]:
        q = ("SELECT r.*, p.sku, p.name FROM restocks r JOIN products p ON p.id=r.product_id"
             + (" WHERE r.status=?" if status else "") + " ORDER BY r.id DESC")
        return [dict(r) for r in self.db.execute(q, (status,) if status else ())]

    # ---- reporting -----------------------------------------------------
    def summary(self) -> dict:
        row = self.db.execute(
            "SELECT COALESCE(SUM(i.qty*i.unit_price_cents),0) rev,"
            "       COALESCE(SUM(i.qty*i.unit_cost_cents),0) cogs,"
            "       COUNT(DISTINCT s.id) n"
            " FROM sales s JOIN sale_items i ON i.sale_id=s.id WHERE s.status='completed'").fetchone()
        top = self.db.execute(
            "SELECT p.sku, p.name, SUM(i.qty) units FROM sale_items i"
            " JOIN sales s ON s.id=i.sale_id AND s.status='completed'"
            " JOIN products p ON p.id=i.product_id GROUP BY p.id ORDER BY units DESC LIMIT 5")
        low = self.db.execute(
            "SELECT sku, name, stock, reorder_point FROM products"
            " WHERE active=1 AND stock <= reorder_point ORDER BY stock")
        return {
            "cash_cents": self.cash_cents(),
            "revenue_cents": row["rev"],
            "gross_profit_cents": row["rev"] - row["cogs"],
            "sales_count": row["n"],
            "top_sellers": [dict(r) for r in top],
            "low_stock": [dict(r) for r in low],
            "pending_restocks": len(self.list_restocks("pending")),
        }

    def recent_sales(self, limit: int = 20) -> list[dict]:
        ids = [r["id"] for r in self.db.execute(
            "SELECT id FROM sales ORDER BY id DESC LIMIT ?", (limit,))]
        return [self.get_sale(i) for i in ids]

    # ---- agent memory / comms -----------------------------------------
    def add_note(self, body: str) -> int:
        return self.db.execute("INSERT INTO notes(body) VALUES (?)", (body,)).lastrowid

    def list_notes(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))][::-1]

    def add_message(self, customer: str, body: str) -> int:
        return self.db.execute("INSERT INTO messages(customer,body) VALUES (?,?)",
                               (customer, body)).lastrowid

    def set_reply(self, message_id: int, reply: str):
        self.db.execute("UPDATE messages SET reply=? WHERE id=?", (reply, message_id))

    def list_messages(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))]

    def list_audit(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
