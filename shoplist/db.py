"""SQLite storage. One connection per request; the schema is created on startup."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS lists (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT    NOT NULL,
    completed_at TEXT,
    completed_by INTEGER REFERENCES users(id)
);

-- Only one list can be open (not yet completed) at a time.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_list ON lists((completed_at IS NULL))
    WHERE completed_at IS NULL;

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,
    added_by   INTEGER REFERENCES users(id),
    added_at   TEXT    NOT NULL,
    checked    INTEGER NOT NULL DEFAULT 0,
    checked_by INTEGER REFERENCES users(id),
    checked_at TEXT
);

CREATE INDEX IF NOT EXISTS items_by_list ON items(list_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return os.environ.get("SHOPLIST_DB", "data/shoplist.db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db() -> None:
    path = db_path()
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        ensure_open_list(conn)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE so concurrent writers queue up instead of racing."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def ensure_open_list(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM lists WHERE completed_at IS NULL").fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO lists (created_at) VALUES (?)", (now(),)).lastrowid
