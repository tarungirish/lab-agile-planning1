"""Seed base data: python -m micromarket.seed   (the demo launcher adds history on top)"""
from .autoorder import AutoOrder
from .charging import Charging
from .db import connect
from .facilities import Facilities
from .store import Store

ITEMS = [  # sku, name, price, cost, stock, reorder
    ("COKE", "Cola can", 150, 80, 24, 6), ("WATER", "Sparkling water", 120, 60, 30, 8),
    ("CHIPS", "Potato chips", 175, 100, 15, 5), ("BAR", "Granola bar", 125, 70, 20, 6),
    ("NUTS", "Trail mix", 250, 150, 8, 4), ("COFFEE", "Cold brew", 350, 200, 10, 4),
]
# sku: (shelf par, stockroom on hand, purchase par for shelf+stockroom combined, pack size)
RULES = {"COKE": (24, 48, 96, 24), "WATER": (24, 48, 96, 24), "CHIPS": (12, 24, 48, 12),
         "BAR": (16, 32, 64, 16), "NUTS": (8, 12, 24, 12), "COFFEE": (8, 16, 24, 6)}
CLEANING = [("Wipe shelves and fridge handles", 24, "market"), ("Empty bins", 12, "market"),
            ("Wipe kiosk screen", 8, "market"), ("Clean lounge seating and tables", 24, "lounge"),
            ("Wipe charger handles and cables", 72, "lounge"), ("Deep clean fridge interior", 168, "market")]
ASSETS = [("Fridge 1", "fridge", "market"), ("Fridge 2", "fridge", "market"),
          ("Kiosk iPad", "kiosk", "market"), ("Lounge seating", "furniture", "lounge")]


def seed(s: Store, created_at: str | None = None) -> bool:
    """Create products, rules, chargers, cleaning tasks and equipment. Returns False if already seeded.
    `created_at` back-dates the cleaning tasks (used by the demo so compliance has history)."""
    if s.list_products(include_inactive=True):
        return False
    for it in ITEMS:
        s.add_product(*it)
    s.deposit(20_000, "starting cash")
    ao = AutoOrder(s)
    for sku, (shelf_par, room, par, pack) in RULES.items():
        ao.stockroom.set_shelf_rule(sku, shelf_par)
        ao.stockroom.set_count(sku, "stockroom", room, "opening count")
        ao.set_rule(sku, par, pack, lead_time_days=2, supplier="Metro Wholesale")
    c = Charging(s)
    for name, kw, loc in [("Lot A1", 7.4, "lot"), ("Lot A2", 11, "lot"), ("Lounge 1", 22, "lounge"), ("Lounge 2", 22, "lounge")]:
        c.add_charger(name, kw, loc)
    f = Facilities(s)
    for name, hours, area in CLEANING:
        f.add_task(name, hours, area, now=created_at)
    for name, kind, loc in ASSETS:
        f.add_asset(name, kind, loc)
    for ch in c.list_chargers():
        f.add_asset(f"{ch['name']} charger", "charger", ch["location"], charger_id=ch["id"])
    return True


if __name__ == "__main__":
    print("Seeded base data" if seed(Store(connect())) else "Database already has products; skipping")
