"""Stockroom (back-of-house) inventory and stockroom -> shelf transfers.

We are our own supplier: deliveries from vendors land in the stockroom, and the shelves
are restocked from it. A transfer is a pick-list line; creating one *reserves* stockroom
quantity, completing it moves the units onto the shelf (where the kiosk sells them).
"""
from __future__ import annotations

from .store import Store, StoreError


class Stockroom:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    # ---- stock & rules -------------------------------------------------
    def pending_out(self, product_id: int) -> int:
        return self.db.execute("SELECT COALESCE(SUM(qty),0) FROM transfers WHERE product_id=? AND status='pending'",
                               (product_id,)).fetchone()[0]

    def available(self, product_id: int) -> int:
        """Stockroom units not already reserved by pending transfers."""
        return self.store.warehouse_qty(product_id) - self.pending_out(product_id)

    def list_stock(self) -> list[dict]:
        out = []
        for p in self.db.execute("SELECT p.*, r.par_level shelf_par, r.enabled shelf_enabled FROM products p"
                                 " LEFT JOIN shelf_rules r ON r.product_id=p.id WHERE p.active=1 ORDER BY p.name"):
            wh = self.store.warehouse_qty(p["id"])
            on_order = self.db.execute("SELECT COALESCE(SUM(qty),0) FROM restocks WHERE product_id=? AND status='pending'",
                                       (p["id"],)).fetchone()[0]
            out.append({"sku": p["sku"], "name": p["name"], "shelf": p["stock"], "stockroom": wh,
                        "reserved": self.pending_out(p["id"]), "on_order": on_order,
                        "reorder_point": p["reorder_point"], "shelf_par": p["shelf_par"],
                        "shelf_enabled": bool(p["shelf_enabled"]) if p["shelf_par"] is not None else None})
        return out

    def set_shelf_rule(self, sku: str, par_level: int, enabled: bool = True, actor: str = "staff") -> dict:
        p = self.store._product(sku)
        if par_level <= 0 or par_level > 500:
            raise StoreError("Shelf par must be between 1 and 500")
        with self.store._tx():
            self.db.execute("INSERT INTO shelf_rules(product_id,par_level,enabled) VALUES (?,?,?)"
                            " ON CONFLICT(product_id) DO UPDATE SET par_level=excluded.par_level, enabled=excluded.enabled",
                            (p["id"], par_level, int(enabled)))
            self.store._audit(actor, "set_shelf_rule", f"{sku}: shelf par {par_level}, enabled={enabled}")
        return {"sku": sku, "par_level": par_level, "enabled": enabled}

    def set_count(self, sku: str, location: str, qty: int, reason: str = "count", actor: str = "staff") -> dict:
        """Stock-take: set the real quantity for 'shelf' or 'stockroom' (shrink, miscounts, spoilage)."""
        if location not in ("shelf", "stockroom"):
            raise StoreError("Location must be 'shelf' or 'stockroom'")
        if qty < 0:
            raise StoreError("Count cannot be negative")
        p = self.store._product(sku)
        with self.store._tx():
            if location == "shelf":
                old = p["stock"]
                self.db.execute("UPDATE products SET stock=? WHERE id=?", (qty, p["id"]))
            else:
                old = self.store.warehouse_qty(p["id"])
                if qty < self.pending_out(p["id"]):
                    raise StoreError(f"{self.pending_out(p['id'])} units are reserved on the pick list; "
                                     "complete or cancel those transfers first")
                self.db.execute("INSERT INTO warehouse_stock(product_id,qty) VALUES (?,?)"
                                " ON CONFLICT(product_id) DO UPDATE SET qty=excluded.qty", (p["id"], qty))
            self.store._audit(actor, "stock_count", f"{sku} {location}: {old} -> {qty} ({reason})")
        return {"sku": sku, "location": location, "old": old, "new": qty}

    # ---- transfers (pick list) -------------------------------------------
    def create_transfer(self, sku: str, qty: int, actor: str = "staff") -> dict:
        p = self.store._product(sku)
        if qty <= 0:
            raise StoreError("Quantity must be positive")
        with self.store._tx():
            avail = self.available(p["id"])
            if qty > avail:
                raise StoreError(f"Only {avail} of {p['name']} available in the stockroom")
            tid = self.db.execute("INSERT INTO transfers(product_id,qty) VALUES (?,?)", (p["id"], qty)).lastrowid
            self.store._audit(actor, "create_transfer", f"#{tid} {sku} x{qty}")
        return self.get_transfer(tid)

    def complete_transfer(self, transfer_id: int, actor: str = "staff") -> dict:
        with self.store._tx():
            t = self.db.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
            if not t:
                raise StoreError(f"Unknown transfer: {transfer_id}")
            if t["status"] != "pending":
                raise StoreError(f"Transfer is already {t['status']}")
            # Conditional update: the stockroom can never go negative.
            cur = self.db.execute("UPDATE warehouse_stock SET qty=qty-? WHERE product_id=? AND qty>=?",
                                  (t["qty"], t["product_id"], t["qty"]))
            if cur.rowcount == 0:
                raise StoreError("Stockroom doesn't have enough units; recount and adjust first")
            self.db.execute("UPDATE products SET stock=stock+? WHERE id=?", (t["qty"], t["product_id"]))
            self.db.execute("UPDATE transfers SET status='completed' WHERE id=?", (transfer_id,))
            self.store._audit(actor, "complete_transfer", f"#{transfer_id}")
        return self.get_transfer(transfer_id)

    def cancel_transfer(self, transfer_id: int, actor: str = "staff") -> dict:
        with self.store._tx():
            cur = self.db.execute("UPDATE transfers SET status='cancelled' WHERE id=? AND status='pending'",
                                  (transfer_id,))
            if cur.rowcount == 0:
                raise StoreError("Only pending transfers can be cancelled")
            self.store._audit(actor, "cancel_transfer", f"#{transfer_id}")
        return self.get_transfer(transfer_id)

    def get_transfer(self, tid: int) -> dict:
        r = self.db.execute("SELECT t.*, p.sku, p.name FROM transfers t JOIN products p ON p.id=t.product_id"
                            " WHERE t.id=?", (tid,)).fetchone()
        if not r:
            raise StoreError(f"Unknown transfer: {tid}")
        return dict(r)

    def list_transfers(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = ("SELECT t.*, p.sku, p.name FROM transfers t JOIN products p ON p.id=t.product_id"
             + (" WHERE t.status=?" if status else "") + " ORDER BY t.id DESC LIMIT ?")
        return [dict(r) for r in self.db.execute(q, (status, limit) if status else (limit,))]
