"""Conversation & generation history - SQLite, shared by both tabs.

One table, two kinds: 'ask' items store the full message list of a chat;
'drill' items store the scenario spec + markdown + meta. The UI upserts
after every completed exchange/generation, so switching tabs, refreshing,
or restarting the server never loses work again.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from config import APP_DIR

DB_PATH = APP_DIR / "history.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS items(
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
        payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _unique_title(conn: sqlite3.Connection, title: str, item_id: str) -> str:
    """Two conversations opened with the same question get the same LLM title.
    Disambiguate rather than showing identical sidebar entries."""
    base = (title or "untitled")[:110]
    taken = {r[0] for r in conn.execute(
        "SELECT title FROM items WHERE id != ?", (item_id,)).fetchall()}
    if base not in taken:
        return base
    n = 2
    while f"{base} ({n})" in taken and n < 50:
        n += 1
    return f"{base} ({n})"


def save_item(kind: str, title: str, payload: dict, item_id: str | None = None) -> str:
    item_id = item_id or uuid.uuid4().hex[:12]
    now = _now()
    with _conn() as conn:
        existing = conn.execute("SELECT created_at, title FROM items WHERE id=?",
                                (item_id,)).fetchone()
        created = existing[0] if existing else now
        # Keep an already-assigned title stable; only de-duplicate new ones.
        final_title = (existing[1] if existing
                       else _unique_title(conn, title, item_id))
        conn.execute(
            "INSERT OR REPLACE INTO items(id, kind, title, payload, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (item_id, kind[:20], final_title[:120],
             json.dumps(payload, ensure_ascii=False), created, now))
    return item_id


def list_items(limit: int = 200) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, kind, title, created_at, updated_at FROM items "
            "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "kind": r[1], "title": r[2],
             "created_at": r[3], "updated_at": r[4]} for r in rows]


def get_item(item_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, kind, title, payload, created_at, updated_at "
            "FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row[3])
    except Exception:  # noqa: BLE001
        payload = {}
    return {"id": row[0], "kind": row[1], "title": row[2], "payload": payload,
            "created_at": row[4], "updated_at": row[5]}


def delete_item(item_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    return cur.rowcount > 0
