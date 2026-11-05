"""SQLite schema and connection helper. All money is stored as integer cents."""
import os
import sqlite3

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
  status TEXT NOT NULL DEFAULT 'pending'  -- pending | received | cancelled
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
  body TEXT NOT NULL
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
-- Audit trail of everything the agent (or staff) changed.
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or os.environ.get("MICROMARKET_DB", "micromarket.db")
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
