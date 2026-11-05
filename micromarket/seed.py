"""Seed demo data: python -m micromarket.seed"""
from .db import connect
from .charging import Charging
from .store import Store

ITEMS = [  # sku, name, price, cost, stock, reorder
    ("COKE", "Cola can", 150, 80, 24, 6), ("WATER", "Sparkling water", 120, 60, 30, 8),
    ("CHIPS", "Potato chips", 175, 100, 15, 5), ("BAR", "Granola bar", 125, 70, 20, 6),
    ("NUTS", "Trail mix", 250, 150, 8, 4), ("COFFEE", "Cold brew", 350, 200, 10, 4),
]

if __name__ == "__main__":
    s = Store(connect())
    if not s.list_products(include_inactive=True):
        for it in ITEMS:
            s.add_product(*it)
        s.deposit(20_000, "starting cash")
        c = Charging(s)
        c.add_charger("Lot A1", 7.4, "lot"); c.add_charger("Lot A2", 11, "lot")
        c.add_charger("Lounge 1", 22, "lounge"); c.add_charger("Lounge 2", 22, "lounge")
        print("Seeded", len(ITEMS), "products and $200.00 starting cash")
    else:
        print("Database already has products; skipping")
