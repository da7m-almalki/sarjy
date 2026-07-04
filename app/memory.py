"""Cross-session memory in SQLite, keyed by the browser's device ID."""

import sqlite3

from app.config import settings

PROFILE_FIELDS = ("name", "phone", "preferred_barber", "last_service")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            device_id TEXT PRIMARY KEY,
            name TEXT, phone TEXT, preferred_barber TEXT, last_service TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
            device_id TEXT, fact TEXT,
            UNIQUE(device_id, fact)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY,
            device_id TEXT, barber TEXT, service TEXT,
            start_iso TEXT, event_id TEXT,
            status TEXT DEFAULT 'confirmed'
        )"""
    )
    return conn


def get_profile(device_id: str) -> dict[str, str]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE device_id = ?", (device_id,)).fetchone()
    if row is None:
        return {}
    return {k: row[k] for k in PROFILE_FIELDS if row[k]}


def update_profile(device_id: str, **fields: str) -> None:
    updates = {k: v for k, v in fields.items() if k in PROFILE_FIELDS and v}
    if not updates:
        return
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (device_id) VALUES (?)", (device_id,))
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE users SET {sets} WHERE device_id = ?", (*updates.values(), device_id))


def get_facts(device_id: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT fact FROM facts WHERE device_id = ?", (device_id,)).fetchall()
    return [r["fact"] for r in rows]


def add_facts(device_id: str, facts: list[str]) -> None:
    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO facts (device_id, fact) VALUES (?, ?)",
            [(device_id, f) for f in facts],
        )


def add_booking(device_id: str, barber: str, service: str, start_iso: str, event_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO bookings (device_id, barber, service, start_iso, event_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (device_id, barber, service, start_iso, event_id),
        )


def upcoming_bookings(device_id: str, now_iso: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE device_id = ? AND status = 'confirmed' "
            "AND start_iso >= ? ORDER BY start_iso",
            (device_id, now_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_booking(booking_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
