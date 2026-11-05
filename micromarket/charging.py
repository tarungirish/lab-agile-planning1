"""EV charging: chargers, sessions, pricing and usage trends.

Shares the Store's database, ledger and audit log, so charging revenue sits next to
market sales. No charger hardware protocol (OCPP) yet: the owner or a meter feed
supplies the kWh delivered when a session stops (see `stop`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .store import Store, StoreError

FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_PRICE = 35        # cents per kWh charged to the driver
DEFAULT_ENERGY_COST = 15  # cents per kWh we pay the utility


def _now() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


class Charging:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    # ---- settings ------------------------------------------------------
    def _get(self, key: str, default: int) -> int:
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return int(r["value"]) if r else default

    def price_cents_per_kwh(self) -> int:
        return self._get("charge_price", DEFAULT_PRICE)

    def energy_cost_cents_per_kwh(self) -> int:
        return self._get("energy_cost", DEFAULT_ENERGY_COST)

    def set_price(self, cents_per_kwh: int, actor: str = "staff") -> int:
        if cents_per_kwh <= 0:
            raise StoreError("Price must be positive")
        old, pol = self.price_cents_per_kwh(), self.store.policy
        if actor != "staff":
            floor = int(self.energy_cost_cents_per_kwh() * (1 + pol.min_margin) + 0.999)
            if cents_per_kwh < floor:
                raise StoreError(f"Refused: {cents_per_kwh}c/kWh is below the floor of {floor}c/kWh")
            if abs(cents_per_kwh - old) / old > pol.max_price_change:
                raise StoreError(f"Refused: change exceeds {pol.max_price_change:.0%}; adjust in steps")
        with self.store._tx():
            self.db.execute("INSERT INTO settings(key,value) VALUES('charge_price',?)"
                            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(cents_per_kwh),))
            self.store._audit(actor, "set_charging_price", f"{old}c -> {cents_per_kwh}c per kWh")
        return cents_per_kwh

    # ---- chargers ------------------------------------------------------
    def add_charger(self, name: str, power_kw: float, location: str = "lot", actor: str = "staff") -> dict:
        if power_kw <= 0:
            raise StoreError("Power must be positive")
        try:
            with self.store._tx():
                cid = self.db.execute("INSERT INTO chargers(name,location,power_kw) VALUES (?,?,?)",
                                      (name, location.strip().lower(), power_kw)).lastrowid
                self.store._audit(actor, "add_charger", f"{name} {power_kw}kW @ {location}")
        except Exception as e:  # sqlite3.IntegrityError
            if "UNIQUE" in str(e):
                raise StoreError(f"Charger already exists: {name}")
            raise
        return dict(self.db.execute("SELECT * FROM chargers WHERE id=?", (cid,)).fetchone())

    def set_in_service(self, charger_id: int, in_service: bool, actor: str = "staff"):
        with self.store._tx():
            cur = self.db.execute("UPDATE chargers SET in_service=? WHERE id=?",
                                  (int(in_service), charger_id))
            if cur.rowcount == 0:
                raise StoreError(f"Unknown charger: {charger_id}")
            self.store._audit(actor, "charger_service", f"#{charger_id} in_service={in_service}")

    def list_chargers(self) -> list[dict]:
        """Chargers with live status: available | charging | out_of_service."""
        out = []
        for c in self.db.execute("SELECT * FROM chargers ORDER BY name"):
            s = self.db.execute("SELECT id, customer, started_at FROM charging_sessions"
                                " WHERE charger_id=? AND status='active'", (c["id"],)).fetchone()
            status = "out_of_service" if not c["in_service"] else ("charging" if s else "available")
            out.append({**dict(c), "status": status, "active_session": dict(s) if s else None})
        return out

    # ---- sessions ------------------------------------------------------
    def start(self, charger_id: int, customer: str, now: str | None = None) -> dict:
        customer = customer.strip()
        if not customer:
            raise StoreError("Customer name is required")
        c = self.db.execute("SELECT * FROM chargers WHERE id=?", (charger_id,)).fetchone()
        if not c:
            raise StoreError(f"Unknown charger: {charger_id}")
        if not c["in_service"]:
            raise StoreError(f"{c['name']} is out of service")
        try:
            with self.store._tx():
                sid = self.db.execute(
                    "INSERT INTO charging_sessions(charger_id,customer,started_at,price_cents_per_kwh)"
                    " VALUES (?,?,?,?)",
                    (charger_id, customer, now or _now(), self.price_cents_per_kwh())).lastrowid
        except Exception as e:
            if "UNIQUE" in str(e):
                raise StoreError(f"{c['name']} is already in use")
            raise
        return self.get_session(sid)

    def stop(self, session_id: int, kwh: float, now: str | None = None) -> dict:
        """Close a session. `kwh` is the energy delivered (meter reading).

        Rejected if it exceeds what the charger could physically deliver in the elapsed
        time (+5%), which catches typos like 450 instead of 45.0.
        """
        if kwh < 0:
            raise StoreError("kWh cannot be negative")
        with self.store._tx():
            s = self.db.execute("SELECT s.*, c.power_kw FROM charging_sessions s"
                                " JOIN chargers c ON c.id=s.charger_id WHERE s.id=?", (session_id,)).fetchone()
            if not s:
                raise StoreError(f"Unknown session: {session_id}")
            if s["status"] != "active":
                raise StoreError("Session already ended")
            end = now or _now()
            hours = (datetime.strptime(end, FMT) - datetime.strptime(s["started_at"], FMT)).total_seconds() / 3600
            if hours < 0:
                raise StoreError("End time is before start time")
            if kwh > s["power_kw"] * hours * 1.05 + 0.05:
                raise StoreError(f"{kwh} kWh is more than a {s['power_kw']} kW charger can deliver in {hours:.2f} h")
            cost = round(kwh * s["price_cents_per_kwh"])
            self.db.execute("UPDATE charging_sessions SET ended_at=?, kwh=?, cost_cents=?, status='completed'"
                            " WHERE id=?", (end, kwh, cost, session_id))
            self.db.execute("INSERT INTO ledger(kind,amount_cents,memo) VALUES ('charging',?,?)",
                            (cost, f"charging session #{session_id}"))
        return self.get_session(session_id)

    def get_session(self, sid: int) -> dict:
        r = self.db.execute("SELECT s.*, c.name charger, c.location FROM charging_sessions s"
                            " JOIN chargers c ON c.id=s.charger_id WHERE s.id=?", (sid,)).fetchone()
        if not r:
            raise StoreError(f"Unknown session: {sid}")
        return dict(r)

    def recent_sessions(self, limit: int = 25) -> list[dict]:
        ids = [r["id"] for r in self.db.execute(
            "SELECT id FROM charging_sessions ORDER BY id DESC LIMIT ?", (limit,))]
        return [self.get_session(i) for i in ids]

    # ---- trends --------------------------------------------------------
    def trends(self, days: int = 30, today: str | None = None) -> dict:
        """Usage analytics over the last `days` days (UTC), based on completed sessions."""
        end = datetime.strptime(today, FMT) if today else datetime.now(timezone.utc).replace(tzinfo=None)
        since = (end.replace(microsecond=0) - timedelta(days=days)).strftime(FMT)
        rows = [dict(r) for r in self.db.execute(
            "SELECT s.*, c.location, c.name charger FROM charging_sessions s JOIN chargers c ON c.id=s.charger_id"
            " WHERE s.status='completed' AND s.started_at >= ?", (since,))]
        for r in rows:
            r["hours"] = (datetime.strptime(r["ended_at"], FMT) - datetime.strptime(r["started_at"], FMT)).total_seconds() / 3600

        by_hour = [{"hour": h, "sessions": 0, "kwh": 0.0} for h in range(24)]
        by_day: dict[str, dict] = {}
        by_loc: dict[str, dict] = {}
        for r in rows:
            h = int(r["started_at"][11:13])
            by_hour[h]["sessions"] += 1
            by_hour[h]["kwh"] += r["kwh"]
            d = by_day.setdefault(r["started_at"][:10], {"date": r["started_at"][:10], "sessions": 0, "kwh": 0.0, "revenue_cents": 0})
            L = by_loc.setdefault(r["location"], {"location": r["location"], "sessions": 0, "kwh": 0.0, "revenue_cents": 0, "hours": 0.0})
            for agg in (d, L):
                agg["sessions"] += 1; agg["kwh"] += r["kwh"]; agg["revenue_cents"] += r["cost_cents"]
            L["hours"] += r["hours"]
        n_by_loc: dict[str, int] = {}
        for c in self.db.execute("SELECT location, COUNT(*) n FROM chargers WHERE in_service=1 GROUP BY location"):
            n_by_loc[c["location"]] = c["n"]
        for loc, L in by_loc.items():
            capacity_h = n_by_loc.get(loc, 0) * days * 24
            L["utilization"] = round(L["hours"] / capacity_h, 4) if capacity_h else None
            L["kwh"] = round(L["kwh"], 2); L["hours"] = round(L["hours"], 2)

        total_kwh = sum(r["kwh"] for r in rows)
        revenue = sum(r["cost_cents"] for r in rows)
        energy_cost = round(total_kwh * self.energy_cost_cents_per_kwh())
        peak = max(by_hour, key=lambda x: x["sessions"]) if rows else None
        for b in by_hour:
            b["kwh"] = round(b["kwh"], 2)
        return {
            "days": days,
            "totals": {"sessions": len(rows), "kwh": round(total_kwh, 2), "revenue_cents": revenue,
                       "energy_cost_cents": energy_cost, "margin_cents": revenue - energy_cost,
                       "avg_session_minutes": round(60 * sum(r["hours"] for r in rows) / len(rows), 1) if rows else 0,
                       "peak_hour": peak["hour"] if peak and peak["sessions"] else None},
            "by_hour": by_hour,
            "by_day": [dict(v, kwh=round(v["kwh"], 2)) for _, v in sorted(by_day.items())],
            "by_location": sorted(by_loc.values(), key=lambda x: -x["sessions"]),
            "cross_sell": self._cross_sell(rows),
        }

    def _cross_sell(self, rows: list[dict]) -> dict:
        """Do drivers shop while they wait? Match charging customers to market sales
        on the same date by (case-insensitive) name. Names are self-reported, so this is
        directional, not exact."""
        if not rows:
            return {"drivers": 0, "bought_while_charging": 0, "avg_spend_cents": 0}
        spend: dict[tuple, int] = {}
        for r in self.db.execute("SELECT lower(customer) c, substr(ts,1,10) d, SUM(total_cents) t FROM sales"
                                 " WHERE status='completed' AND customer IS NOT NULL GROUP BY c, d"):
            spend[(r["c"], r["d"])] = r["t"]
        keys = {(r["customer"].lower(), r["started_at"][:10]) for r in rows}
        bought = [spend[k] for k in keys if k in spend]
        return {"drivers": len(keys), "bought_while_charging": len(bought),
                "avg_spend_cents": round(sum(bought) / len(bought)) if bought else 0}
