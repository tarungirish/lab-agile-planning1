"""Seed demo data: python -m micromarket.seed"""
from .db import connect
from .autoorder import AutoOrder
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
        ao = AutoOrder(s)
        # sku: (shelf par, stockroom on hand, purchase par for shelf+stockroom combined, pack size)
        for sku, (shelf_par, room, par, pack) in {
                "COKE": (24, 48, 96, 24), "WATER": (24, 48, 96, 24), "CHIPS": (12, 24, 48, 12),
                "BAR": (16, 32, 64, 16), "NUTS": (8, 12, 24, 12), "COFFEE": (8, 16, 24, 6)}.items():
            ao.stockroom.set_shelf_rule(sku, shelf_par)
            ao.stockroom.set_count(sku, "stockroom", room, "opening count")
            ao.set_rule(sku, par, pack, lead_time_days=2, supplier="Metro Wholesale")
        c = Charging(s)
        c.add_charger("Lot A1", 7.4, "lot"); c.add_charger("Lot A2", 11, "lot")
        c.add_charger("Lounge 1", 22, "lounge"); c.add_charger("Lounge 2", 22, "lounge")
        print("Seeded", len(ITEMS), "products and $200.00 starting cash")
    else:
        print("Database already has products; skipping")
