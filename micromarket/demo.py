"""Self-contained prototype launcher:   python -m micromarket.demo

* Builds a fresh demo database with ~2 weeks of realistic history (sales, charging, cleaning, tickets).
* Generates random owner/staff tokens for this run and binds to localhost only.
* Uses the real Claude model if ANTHROPIC_API_KEY is set; otherwise a clearly-labelled *scripted*
  stand-in drives the real tools (and real guardrails), so every screen works with no API key.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import secrets
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from PIL import Image, ImageDraw

from .agent import Shopkeeper
from .charging import FMT, Charging
from .db import connect
from .facilities import Facilities
from .seed import seed
from .stockroom import Stockroom
from .store import Store
from .vision import Cameras

NAMES = ["Amy", "Ben", "Chen", "Dana", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jo"]
POPULARITY = {"COKE": 30, "WATER": 20, "CHIPS": 15, "BAR": 15, "COFFEE": 12, "NUTS": 8}
FINAL_SHELF = {"COKE": 4, "WATER": 20, "CHIPS": 3, "BAR": 14, "NUTS": 2, "COFFEE": 5}
FINAL_ROOM = {"COKE": 40, "WATER": 44, "CHIPS": 20, "BAR": 30, "NUTS": 3, "COFFEE": 0}
LAST_CLEANED_H_AGO = {"Wipe shelves and fridge handles": 20, "Empty bins": 3, "Wipe kiosk screen": 6,
                      "Clean lounge seating and tables": 30, "Wipe charger handles and cables": 56,
                      "Deep clean fridge interior": 200}
BANNER = "Demo mode: scripted shopkeeper (no AI model connected)"
# Demo cameras: (name, SKUs in view, equipment it looks into)
DEMO_CAMERAS = [("Fridge 1 camera", ["COKE", "WATER"], "Fridge 1"), ("Fridge 2 camera", ["COFFEE"], "Fridge 2"),
                ("Snack shelf camera", ["CHIPS", "BAR", "NUTS"], None)]
SHRINK = {"COKE": 2}       # two colas walked out unpaid: the cameras should catch it
COLORS = {"COKE": (196, 30, 45), "WATER": (40, 120, 200), "COFFEE": (110, 70, 40), "CHIPS": (230, 190, 40),
          "BAR": (220, 120, 40), "NUTS": (70, 140, 70)}


def physical_count(store: Store, sku: str) -> int:
    """What is really on the demo shelf: the system count minus unrecorded shrink, until a camera
    correction for that product has been applied or accepted."""
    fixed = store.db.execute("SELECT 1 FROM camera_counts WHERE sku=? AND action IN ('applied','accepted')",
                             (sku,)).fetchone()
    return max(0, store._product(sku)["stock"] - (0 if fixed else SHRINK.get(sku, 0)))


def render_shelf(store: Store, skus: list[str]) -> bytes:
    """A simple drawn 'photo' of a cooler shelf: one row per product, one block per unit on the shelf."""
    img = Image.new("RGB", (800, 600), (28, 32, 36))
    d = ImageDraw.Draw(img)
    row_h = 600 // max(len(skus), 1)
    for r, sku in enumerate(skus):
        y = r * row_h
        d.rectangle([0, y + row_h - 8, 800, y + row_h], fill=(150, 150, 150))          # the shelf
        d.text((12, y + 8), store._product(sku)["name"], fill=(235, 235, 235))
        n = physical_count(store, sku)
        w = min(44, (800 - 24) // max(n, 1) - 6)
        for i in range(n):
            x = 12 + i * (w + 6)
            d.rounded_rectangle([x, y + 30, x + w, y + row_h - 14], radius=6, fill=COLORS.get(sku, (180, 180, 180)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


class SimulatedVision:
    """Demo stand-in for VisionCounter (no API key): reports the simulated physical count.
    Not computer vision; it exists so the camera -> reconcile -> reorder pipeline can be tried."""

    def __init__(self, store: Store):
        self.store = store

    def count(self, jpeg: bytes, products: list[dict]) -> dict:
        return {"view_ok": True, "note": "Simulated count (demo camera)",
                "counts": {p["sku"]: (physical_count(self.store, p["sku"]), "high") for p in products}}


def _ts(t: datetime) -> str:
    return t.strftime(FMT)


def build_demo_store(path: str, days: int = 14, rng_seed: int = 7, image_dir: str | None = None) -> Store:
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

    # --- cameras: two photos each, ten minutes apart, so a confirmed difference is already flagged ---
    cams = Cameras(s, counter=SimulatedVision(s), image_dir=image_dir or tempfile.mkdtemp(prefix="demo_cams_"))
    for name, skus, asset in DEMO_CAMERAS:
        cam = cams.add_camera(name, skus, assets.get(asset))
        for minutes in (12, 2):
            cams.ingest(cam["id"], render_shelf(s, skus), now=_ts(now - timedelta(minutes=minutes)))
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
        if not res:                                          # 1. read everything
            return NS(stop_reason="tool_use", content=[self._tool("sales_report"), self._tool("auto_order_plan"),
                                                       self._tool("facilities_report"), self._tool("camera_report")])
        if "run_auto_order_plan" not in res:                 # 2. act within the owner's mode and the guardrails
            acts = [self._tool("run_auto_order_plan"), self._tool("remind_overdue_cleaning")]
            todo = [i for i in res["facilities_report"]["issues"] if i["severity"] != "critical"
                    and not i.get("triage_note") and i["source"] != "agent"]
            if todo:
                i = todo[0]
                note = ("Check today; if it keeps getting worse, ask the owner to take it out of service."
                        if i["severity"] == "high" else "Staff can handle this on their next round.")
                acts.append(self._tool("triage_issue", issue_id=i["id"], note=note))
            return NS(stop_reason="tool_use", content=acts)
        s, fac, run = res["sales_report"], res["facilities_report"], res["run_auto_order_plan"]
        title = lambda i: i.get("title") or f"{i['untrusted_public_text']['title']} (public report)"
        name = {r["sku"]: r["name"] for r in res["auto_order_plan"]["rules"]}.get
        moved = [f"{name(t['sku'], t['sku'])} ×{t['qty']}" for t in run["transfers"]]
        bought = [f"{name(p['sku'], p['sku'])} ×{p['qty']}" for p in run["placed"]]
        waiting = [f"{name(w['sku'], w['sku'])} ×{w['qty']} (${w['cost_cents'] / 100:.2f})" for w in run["awaiting_approval"]]
        rem = res["remind_overdue_cleaning"]
        triaged = res.get("triage_issue")
        owner = [title(i) for i in fac["issues"] if i["severity"] in ("high", "critical")]
        camera_flags = [f"{x['name']}: camera {x['counted']}, system {x['system']}"
                        for c in res.get("camera_report", {}).get("cameras", []) for x in c["counts"] if x["action"] == "flagged"]
        text = (f"[{BANNER}]\n\nMorning review\n"
                f"• Cash ${s['cash_cents'] / 100:.2f}; ${s['revenue_cents'] / 100:.2f} revenue across {s['sales_count']} sales, "
                f"gross profit ${s['gross_profit_cents'] / 100:.2f}.\n"
                f"• Ordering ({run['mode']} mode): pick list created for {', '.join(moved) or 'nothing'}; "
                f"purchased {', '.join(bought) or 'nothing'}"
                + (f"; awaiting your approval: {', '.join(waiting)}" if waiting else "") + ".\n"
                f"• Cleaning: reminders filed for {', '.join(rem['created']) or 'nothing new'}"
                + (f" (already open: {', '.join(rem['already_open'])})" if rem["already_open"] else "") + ".\n"
                + (f"• Triaged: {title(triaged)}: {triaged['triage_note'].split('] ', 1)[-1]}\n" if triaged else "")
                + (f"• Cameras flagged a count difference (possible shrink): {'; '.join(camera_flags)}. "
                   "Check the shelf or accept the camera count on the Stockroom tab.\n" if camera_flags else "")
                + f"• Needs your attention: {'; '.join(owner) or 'nothing urgent'}.")
        if "add_note" not in res:                            # 3. remember what was done
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
    ap.add_argument("--camera-every", type=int, default=300,
                    help="seconds between simulated camera photos (0 = off)")
    a = ap.parse_args()

    os.environ["MICROMARKET_DB"] = a.db
    image_dir = os.path.join(os.path.dirname(os.path.abspath(a.db)), "demo_camera_images")
    if not (a.keep and os.path.exists(a.db)):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(a.db + suffix):
                os.remove(a.db + suffix)
        if os.path.isdir(image_dir):
            for f in os.listdir(image_dir):
                if f.endswith(".jpg"):
                    os.remove(os.path.join(image_dir, f))
        build_demo_store(a.db, image_dir=image_dir).db.close()
    generated = set()
    for k in ("ADMIN_TOKEN", "STAFF_TOKEN", "CAMERA_TOKEN"):
        if not os.environ.get(k):
            os.environ[k] = secrets.token_urlsafe(9)
            generated.add(k)
    # Only print codes this run made up; codes set by the host (e.g. Render) stay out of the logs.
    shown = lambda k: os.environ[k] if k in generated else f"(set in the {k} environment variable)"

    from .app import create_app
    store = Store(connect(a.db))
    live = bool(os.environ.get("ANTHROPIC_API_KEY")) and not a.scripted
    app = create_app(store, Shopkeeper(store, client=None if live else ScriptedClient(),
                                       vision=None if live else SimulatedVision(store), image_dir=image_dir))

    base = os.environ.get("RENDER_EXTERNAL_URL") or f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}"
    print(f"""
  Micromarket prototype  ({'live Claude model' if live else BANNER})

    Kiosk (public) : {base}/
    Staff page     : {base}/staff     code: {shown('STAFF_TOKEN')}
    Owner          : {base}/owner     code: {shown('ADMIN_TOKEN')}
    Cameras        : {'simulated, a photo every ' + str(a.camera_every) + ' s' if a.camera_every else 'off'}{' (counted by ' + ('Claude vision' if live else 'a simulated counter') + ')' if a.camera_every else ''}

  Demo data only. Ctrl+C to stop.
""", flush=True)   # hosts capture stdout through a pipe; don't let the banner sit in a buffer
    if a.camera_every > 0:
        threading.Thread(target=_camera_loop, args=(store, a.port, a.camera_every), daemon=True).start()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


def _camera_loop(store: Store, port: int, every: int):
    """Plays the part of the cooler cameras: POSTs a photo per camera through the real upload API."""
    cams = [(r["id"], r["skus"].split(",")) for r in store.db.execute("SELECT id, skus FROM cameras WHERE active=1")]
    while True:
        time.sleep(every)
        for cid, skus in cams:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/cameras/{cid}/snapshot",
                                         data=render_shelf(store, skus), method="POST",
                                         headers={"Content-Type": "image/jpeg", "X-Camera-Token": os.environ["CAMERA_TOKEN"]})
            try:
                urllib.request.urlopen(req, timeout=60).read()
            except Exception:
                pass   # a missed photo is fine; the next one will be counted


if __name__ == "__main__":
    main()
