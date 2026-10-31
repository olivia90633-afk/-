
import sqlite3
from config import DATABASE_PATH


def connect():
    return sqlite3.connect(DATABASE_PATH)


def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_blocked INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()


def save_user(telegram_id, username, first_name, last_name):
    with connect() as conn:
        conn.execute("""
            INSERT INTO users (
                telegram_id, username, first_name, last_name
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen = CURRENT_TIMESTAMP
        """, (telegram_id, username, first_name, last_name))
        conn.commit()


def get_all_user_ids():
    with connect() as conn:
        rows = conn.execute(
            "SELECT telegram_id FROM users WHERE is_blocked = 0"
        ).fetchall()

    return [row[0] for row in rows]


def count_users():
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()

    return row[0]
  
