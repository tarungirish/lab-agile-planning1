"""The app shares one SQLite connection across request threads; every access must be serialized.

Regression: the owner dashboard fires ~10 requests at once. With only writes locked, concurrent
reads raised sqlite3.InterfaceError and COALESCE(SUM()) queries returned None (HTTP 500s).
"""
import threading

from micromarket.autoorder import AutoOrder
from micromarket.charging import Charging
from micromarket.demo import build_demo_store
from micromarket.facilities import Facilities
from micromarket.store import Store, StoreError


def hammer(fns, threads=12, iterations=60):
    errors, barrier = [], threading.Barrier(threads)

    def worker(i):
        barrier.wait()
        for n in range(iterations):
            try:
                fns[(i + n) % len(fns)]()
            except StoreError:
                pass                                   # business rejections (e.g. sold out) are fine
            except Exception as e:                     # anything else is the bug under test
                errors.append(repr(e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return errors


def test_concurrent_reads_and_writes_do_not_corrupt_each_other(tmp_path):
    store = build_demo_store(str(tmp_path / "c.db"))
    ao, ch, fac = AutoOrder(store), Charging(store), Facilities(store)
    before = sum(p["stock"] for p in store.list_products())
    sold = []
    lock = threading.Lock()

    def buy():
        store.checkout([{"sku": "WATER", "qty": 1}])
        with lock:
            sold.append(1)

    errors = hammer([store.summary, store.list_products, ao.plan, ao.stockroom.list_stock,
                     lambda: ch.trends(30), fac.summary, lambda: store.recent_sales(10), buy])
    assert errors == []
    after = sum(p["stock"] for p in store.list_products())
    assert before - after == len(sold)                  # no lost or doubled stock updates
