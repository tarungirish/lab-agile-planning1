"""Self-contained prototype launcher:   python -m micromarket.demo

* Builds a fresh demo database with ~2 weeks of realistic history (sales, charging, cleaning, tickets).
* Generates random owner/staff tokens for this run and binds to localhost only.
* Uses the real Claude model if ANTHROPIC_API_KEY is set; otherwise a clearly-labelled *scripted*
  stand-in drives the real tools (and real guardrails), so every screen works with no API key.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from .agent import Shopkeeper
from .charging import FMT, Charging
from .db import connect
from .facilities import Facilities
from .seed import seed
from .stockroom import Stockroom
from .store import Store

NAMES = ["Amy", "Ben", "Chen", "Dana", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jo"]
POPULARITY = {"COKE": 30, "WATER": 20, "CHIPS": 15, "BAR": 15, "COFFEE": 12, "NUTS": 8}
FINAL_SHELF = {"COKE": 4, "WATER": 20, "CHIPS": 3, "BAR": 14, "NUTS": 2, "COFFEE": 5}
FINAL_ROOM = {"COKE": 40, "WATER": 44, "CHIPS": 20, "BAR": 30, "NUTS": 3, "COFFEE": 0}
LAST_CLEANED_H_AGO = {"Wipe shelves and fridge handles": 20, "Empty bins": 3, "Wipe kiosk screen": 6,
                      "Clean lounge seating and tables": 30, "Wipe charger handles and cables": 56,
                      "Deep clean fridge interior": 200}
BANNER = "Demo mode: scripted shopkeeper (no AI model connected)"


def _ts(t: datetime) -> str:
    return t.strftime(FMT)


def build_demo_store(path: str, days: int = 14, rng_seed: int = 7) -> Store:
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    s = Store(connect(path))
    seed(s, created_at=_ts(now - timedelta(days=days)))
    stockroom, charging, fac = Stockroom(s), Charging(s), Facilities(s)
    skus = list(POPULARITY)
    for sku in skus:                                   # plenty of stock while generating history
        stockroom.set_count(sku, "shelf", 500, "demo history")

    # --- sales, re-stamped into the past ---
    for d in range(days, -1, -1):
        day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0)
        for _ in range(rng.randint(10, 22) if day.weekday() < 5 else rng.randint(4, 9)):
            when = day + timedelta(hours=rng.choices(range(8, 19), [3, 4, 5, 6, 5, 4, 4, 5, 4, 3, 2])[0],
                                   minutes=rng.randint(0, 59))
            if when > now - timedelta(minutes=5):
                continue
            items = [{"sku": rng.choices(skus, [POPULARITY[k] for k in skus])[0], "qty": rng.choice([1, 1, 1, 2])}
                     for _ in range(rng.choice([1, 1, 2, 3]))]
            sale = s.checkout(items, rng.choice(["card", "card", "cash"]), rng.choice(NAMES + [None] * 6))
            s.db.execute("UPDATE sales SET ts=? WHERE id=?", (_ts(when), sale["id"]))
    for sku in skus:                                   # now set the real, current levels
        stockroom.set_count(sku, "shelf", FINAL_SHELF[sku], "demo current")
        stockroom.set_count(sku, "stockroom", FINAL_ROOM[sku], "demo current")

    # --- EV charging history (lounge is busier) ---
    chargers = charging.list_chargers()
    busy: dict[int, list[tuple[datetime, datetime]]] = {c["id"]: [] for c in chargers}
    for d in range(days, 0, -1):
        day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0)
        for _ in range(rng.randint(3, 7)):
            c = rng.choices(chargers, [1, 1, 3, 3])[0]
            st = day + timedelta(hours=rng.choices(range(7, 21), [4, 6, 5, 3, 3, 4, 6, 5, 3, 3, 4, 3, 2, 1])[0],
                                 minutes=rng.randint(0, 50))
            en = st + timedelta(minutes=rng.randint(30, 150))
            if any(st < e and en > b for b, e in busy[c["id"]]):
                continue
            busy[c["id"]].append((st, en))
            hours = (en - st).total_seconds() / 3600
            sess = charging.start(c["id"], rng.choice(NAMES), now=_ts(st))
            charging.stop(sess["id"], round(min(c["power_kw"] * hours * rng.uniform(0.35, 0.8), 60), 1), now=_ts(en))
    lounge1 = next(c for c in chargers if c["name"] == "Lounge 1")
    charging.start(lounge1["id"], "Amy", now=_ts(now - timedelta(minutes=40)))

    # --- cleaning logs: each task back from its last clean, ~10% skipped ---
    for t in fac.list_cleaning():
        at = now - timedelta(hours=LAST_CLEANED_H_AGO[t["name"]])
        while at > now - timedelta(days=days):
            if at == now - timedelta(hours=LAST_CLEANED_H_AGO[t["name"]]) or rng.random() > 0.1:
                fac.log_cleaning(t["id"], rng.choice(["Sam", "Maria"]), now=_ts(at))
            at -= timedelta(hours=t["frequency_hours"] * rng.uniform(0.9, 1.15))

    # --- tickets ---
    assets = {a["name"]: a["id"] for a in fac.list_assets()}
    old = fac.report_issue("Kiosk screen has a cracked corner", None, "low", assets["Kiosk iPad"],
                           "Maria", "staff", now=_ts(now - timedelta(days=3)))
    fac.update_issue(old["id"], "resolved", "Fitted a screen protector", now=_ts(now - timedelta(days=2)))
    fac.report_issue("Fridge 2 is running warm", "Drinks on the top shelf feel lukewarm", "high",
                     assets["Fridge 2"], "Maria", "staff", now=_ts(now - timedelta(hours=5)))
    fac.report_issue("Someone spilled juice by the lounge seating", None, "normal", assets["Lounge seating"],
                     "Amy", "kiosk", now=_ts(now - timedelta(hours=1)))
    return s


# ---------------------------------------------------------------------------------------------
class ScriptedClient:
    """Offline stand-in for anthropic.Anthropic, used only when no API key is configured.

    It is NOT an AI model: it follows a fixed script, but every tool call goes through the real
    agent loop, tool permissions and guardrails, so the screens and data flow are the real thing.
    """

    def __init__(self):
        self.messages = NS(create=self._create)

    @staticmethod
    def _tool(name, **args):
        return NS(type="tool_use", id=f"demo_{name}", name=name, input=args)

    @staticmethod
    def _results(messages) -> dict:
        out = {}
        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                for r in m["content"]:
                    if isinstance(r, dict) and r.get("type") == "tool_result" and not r.get("is_error"):
                        out[r["tool_use_id"].removeprefix("demo_")] = json.loads(r["content"])
        return out

    def _create(self, **kw):
        names = {t["name"] for t in kw["tools"]}
        res = self._results(kw["messages"])
        return self._manager(res) if "set_price" in names else self._customer(kw["messages"][0]["content"], res)

    def _manager(self, res):
        if not res:
            return NS(stop_reason="tool_use", content=[self._tool("sales_report"), self._tool("auto_order_plan"),
                                                       self._tool("facilities_report")])
        s, plan, fac = res["sales_report"], res["auto_order_plan"]["plan"], res["facilities_report"]
        moves = [f"{t['name']} ×{t['qty']}" for t in plan["transfers"] if t["status"] == "move"]
        buys = [f"{l['name']} ×{l['qty']} (${l['cost_cents'] / 100:.2f})" for l in plan["lines"] if l["status"] == "order"]
        overdue = [c["name"] for c in fac["cleaning"] if c["status"] == "overdue"]
        worst = [f"{i['title']} [{i['severity']}]" for i in fac["issues"][:3]]
        text = (f"[{BANNER}]\n\nMorning review\n"
                f"• Cash ${s['cash_cents'] / 100:.2f}; ${s['revenue_cents'] / 100:.2f} revenue across {s['sales_count']} sales, "
                f"gross profit ${s['gross_profit_cents'] / 100:.2f}.\n"
                f"• Shelves to restock from the stockroom: {', '.join(moves) or 'none'}.\n"
                f"• To buy for the stockroom: {', '.join(buys) or 'nothing'}.\n"
                f"• Cleaning overdue: {', '.join(overdue) or 'none'}.\n"
                f"• Open issues: {'; '.join(worst) or 'none'}.\n\n"
                "Suggested next step: open the Auto-order tab and run the plan, then the Stockroom tab for the pick list.")
        if "add_note" not in res:
            return NS(stop_reason="tool_use", content=[self._tool("add_note", body=text.split("\n\n", 1)[1][:600])])
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)])

    def _customer(self, prompt, res):
        msg = (re.search(r"<customer_message>(.*?)</customer_message>", prompt, re.S) or [None, ""])[1].lower()
        if not res:
            return NS(stop_reason="tool_use", content=[self._tool("charger_status" if "charg" in msg else "list_inventory")])
        if "charger_status" in res:
            cs = res["charger_status"]["chargers"]
            free = [c["name"] for c in cs if c["status"] == "available"]
            text = (f"{len(free)} of {len(cs)} chargers are free right now ({', '.join(free) or 'none'}). "
                    f"Charging is ${res['charger_status']['price_cents_per_kwh'] / 100:.2f} per kWh.")
        else:
            words = {"cola": "COKE", "coke": "COKE", "water": "WATER", "chip": "CHIPS", "granola": "BAR", "bar": "BAR",
                     "trail": "NUTS", "nut": "NUTS", "brew": "COFFEE", "coffee": "COFFEE"}
            sku = next((v for k, v in words.items() if k in msg), None)
            p = next((p for p in res["list_inventory"] if p["sku"] == sku), None)
            text = (f"{p['name']} is ${p['price_cents'] / 100:.2f}, and " + (f"we have {p['stock']} on the shelf." if p["stock"]
                    else "it's sold out right now. I've let the owner know.") if p else
                    "Thanks for the note! I've passed it to the owner.")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text + " (demo reply)")])


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Run the micromarket prototype with demo data.")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default localhost only)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default="demo.db")
    ap.add_argument("--keep", action="store_true", help="reuse the existing demo database instead of rebuilding")
    ap.add_argument("--scripted", action="store_true", help="force the scripted shopkeeper even if an API key is set")
    a = ap.parse_args()

    os.environ["MICROMARKET_DB"] = a.db
    if not (a.keep and os.path.exists(a.db)):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(a.db + suffix):
                os.remove(a.db + suffix)
        build_demo_store(a.db).db.close()
    os.environ.setdefault("ADMIN_TOKEN", secrets.token_urlsafe(9))
    os.environ.setdefault("STAFF_TOKEN", secrets.token_urlsafe(9))

    from .app import create_app
    store = Store(connect(a.db))
    live = bool(os.environ.get("ANTHROPIC_API_KEY")) and not a.scripted
    app = create_app(store, Shopkeeper(store, client=None if live else ScriptedClient()))

    base = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}"
    print(f"""
  Micromarket prototype  ({'live Claude model' if live else BANNER})

    Kiosk (public) : {base}/
    Staff page     : {base}/staff     code: {os.environ['STAFF_TOKEN']}
    Owner          : {base}/owner     code: {os.environ['ADMIN_TOKEN']}

  Demo data only. Codes are random for this run. Ctrl+C to stop.
""")
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
