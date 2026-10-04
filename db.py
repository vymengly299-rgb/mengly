"""SQLite helpers + schema bootstrap (thread-safe)."""
import os
import sqlite3
import threading
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
DB_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pair        TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry       REAL NOT NULL,
    take_profit REAL NOT NULL,
    stop_loss   REAL NOT NULL,
    timeframe   TEXT NOT NULL,
    confidence  REAL NOT NULL,
    strategy    TEXT NOT NULL,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    source      TEXT NOT NULL DEFAULT 'AI',
    created_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SETTINGS = {
    "ai_auto": "1",
    "max_active": "6",
    "risk": "balanced",
}


def db_execute(sql, args=()):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def db_query(sql, args=()):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()


def db_get(sql, args=()):
    rows = db_query(sql, args)
    return rows[0] if rows else None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    for key, value in DEFAULT_SETTINGS.items():
        if db_get("SELECT value FROM settings WHERE key = ?", (key,)) is None:
            db_execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_setting(key, default=None):
    row = db_get("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    db_execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
