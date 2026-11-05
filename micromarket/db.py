"""SQLite schema and connection helper. All money is stored as integer cents."""
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  sku TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK (price_cents > 0),
  cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
  stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
  reorder_point INTEGER NOT NULL DEFAULT 3,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  customer TEXT,
  payment_method TEXT NOT NULL,
  total_cents INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'completed'  -- completed | refunded
);
CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY,
  sale_id INTEGER NOT NULL REFERENCES sales(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK (qty > 0),
  unit_price_cents INTEGER NOT NULL,
  unit_cost_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS restocks (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK (qty > 0),
  unit_cost_cents INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | received | cancelled
  ordered_by TEXT NOT NULL DEFAULT 'staff' -- staff (owner, manual) | agent | auto | staff-approved (plan run)
);
-- Cash ledger: positive = money in, negative = money out.
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  memo TEXT
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  body TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent'     -- agent | customer (customer notes are untrusted)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  customer TEXT NOT NULL,
  body TEXT NOT NULL,
  reply TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chargers (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  location TEXT NOT NULL DEFAULT 'lot',   -- e.g. lot, garage, lounge
  power_kw REAL NOT NULL CHECK (power_kw > 0),
  in_service INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS charging_sessions (
  id INTEGER PRIMARY KEY,
  charger_id INTEGER NOT NULL REFERENCES chargers(id),
  customer TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  kwh REAL,
  price_cents_per_kwh INTEGER NOT NULL,   -- locked in at start
  cost_cents INTEGER,
  status TEXT NOT NULL DEFAULT 'active'   -- active | completed
);
-- At most one active session per charger, enforced by the database.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session
  ON charging_sessions(charger_id) WHERE status='active';
CREATE TABLE IF NOT EXISTS reorder_rules (
  product_id INTEGER PRIMARY KEY REFERENCES products(id),
  par_level INTEGER NOT NULL CHECK (par_level > 0),   -- stock level to order back up to
  pack_size INTEGER NOT NULL DEFAULT 1 CHECK (pack_size > 0),
  lead_time_days REAL NOT NULL DEFAULT 2 CHECK (lead_time_days >= 0),
  supplier TEXT,
  enabled INTEGER NOT NULL DEFAULT 1
);
-- Stockroom (back-of-house) inventory. products.stock is always the *shelf* quantity the kiosk sells.
CREATE TABLE IF NOT EXISTS warehouse_stock (
  product_id INTEGER PRIMARY KEY REFERENCES products(id),
  qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0)
);
-- Shelf rule: how full to keep the shelf. Products with one are restocked from the stockroom.
CREATE TABLE IF NOT EXISTS shelf_rules (
  product_id INTEGER PRIMARY KEY REFERENCES products(id),
  par_level INTEGER NOT NULL CHECK (par_level > 0),
  enabled INTEGER NOT NULL DEFAULT 1
);
-- Stockroom -> shelf moves. 'pending' = on the pick list (qty is reserved in the stockroom).
CREATE TABLE IF NOT EXISTS transfers (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK (qty > 0),
  status TEXT NOT NULL DEFAULT 'pending'  -- pending | completed | cancelled
);
-- Cleaning tracker
CREATE TABLE IF NOT EXISTS cleaning_tasks (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  area TEXT NOT NULL DEFAULT 'general',
  frequency_hours REAL NOT NULL CHECK (frequency_hours > 0),
  instructions TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cleaning_logs (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES cleaning_tasks(id),
  ts TEXT NOT NULL,
  done_by TEXT NOT NULL,
  notes TEXT
);
-- Facilities tracker: equipment register + issue tickets
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL DEFAULT 'equipment',
  location TEXT NOT NULL DEFAULT 'market',
  status TEXT NOT NULL DEFAULT 'ok',      -- ok | needs_attention | out_of_service
  charger_id INTEGER REFERENCES chargers(id),
  notes TEXT
);
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES assets(id),
  title TEXT NOT NULL,
  description TEXT,
  severity TEXT NOT NULL DEFAULT 'normal',  -- low | normal | high | critical
  status TEXT NOT NULL DEFAULT 'open',      -- open | in_progress | resolved
  reported_by TEXT,
  source TEXT NOT NULL DEFAULT 'owner',     -- kiosk | owner | staff | agent | customer-chat
  ts TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT,
  triage_note TEXT
);
-- Cooler / shelf cameras: photos in, shelf counts out (vision), reconciled against the system.
CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  asset_id INTEGER REFERENCES assets(id),   -- e.g. the fridge it looks into
  skus TEXT NOT NULL,                       -- comma-separated SKUs that belong in view
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS camera_snapshots (
  id INTEGER PRIMARY KEY,
  camera_id INTEGER NOT NULL REFERENCES cameras(id),
  ts TEXT NOT NULL,
  image_path TEXT,                          -- NULL once pruned by retention
  status TEXT NOT NULL DEFAULT 'stored',    -- stored | counted | unusable | error
  note TEXT                                 -- why not counted, or the vision model's remark (untrusted)
);
CREATE TABLE IF NOT EXISTS camera_counts (
  id INTEGER PRIMARY KEY,
  snapshot_id INTEGER NOT NULL REFERENCES camera_snapshots(id),
  camera_id INTEGER NOT NULL REFERENCES cameras(id),
  ts TEXT NOT NULL,
  sku TEXT NOT NULL,
  counted INTEGER NOT NULL,
  system INTEGER NOT NULL,                  -- shelf count the system had at that moment
  confidence TEXT NOT NULL,                 -- low | medium | high
  action TEXT NOT NULL                      -- match | pending | flagged | applied | accepted | low_confidence
);
-- Audit trail of everything the agent (or staff) changed.
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);
"""


class _Rows:
    """Fully-fetched result of one statement (so no cursor is ever shared between threads)."""

    def __init__(self, cur: sqlite3.Cursor):
        self._rows = cur.fetchall() if cur.description else []
        self.lastrowid, self.rowcount, self._i = cur.lastrowid, cur.rowcount, 0

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self):
        rest, self._i = self._rows[self._i:], len(self._rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class Database:
    """One sqlite3 connection shared by all request threads and the scheduler, fully serialized.

    sqlite3 connections are not safe for concurrent use: overlapping statements raised
    InterfaceError and returned bogus rows. Every statement runs under `lock`, and Store._tx holds
    the same (re-entrant) lock for the whole transaction, so other threads never observe or join a
    half-finished transaction.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.lock = threading.RLock()

    def execute(self, sql: str, params=()) -> _Rows:
        with self.lock:
            return _Rows(self._conn.execute(sql, params))

    def close(self):
        with self.lock:
            self._conn.close()


# (table, column, DDL for ALTER TABLE ADD COLUMN, optional backfill SQL)
MIGRATIONS = [
    ("notes", "source", "source TEXT NOT NULL DEFAULT 'agent'", None),
    ("issues", "triage_note", "triage_note TEXT", None),
    ("restocks", "ordered_by", "ordered_by TEXT NOT NULL DEFAULT 'staff'", None),
    ("cleaning_tasks", "created_at", "created_at TEXT NOT NULL DEFAULT '1970-01-01 00:00:00'",
     "UPDATE cleaning_tasks SET created_at=datetime('now') WHERE created_at='1970-01-01 00:00:00'"),
]


def _migrate(conn: sqlite3.Connection):
    for table, column, ddl, backfill in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # names are constants above
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            if backfill:
                conn.execute(backfill)


def connect(path: str | None = None) -> Database:
    path = path or os.environ.get("MICROMARKET_DB", "micromarket.db")
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return Database(conn)
