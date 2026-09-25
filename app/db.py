from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR, DATABASE_PATH, RUNTIME_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Small SQLite boundary with explicit transactions and durable events."""

    def __init__(self, path: Path | str = DATABASE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        # SQLite serializes the short write critical section. The unique index below
        # remains the final authority even if callers arrive concurrently.
        with self._write_lock:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def initialize(self) -> None:
        with self.transaction(immediate=True) as conn:
            # Older local builds used generic table names that collide with the
            # ADK DatabaseSessionService schema. Preserve them under legacy names
            # so an existing developer database upgrades without data loss.
            old_sessions = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
            if old_sessions:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
                if "apartment" in columns and "app_name" not in columns:
                    for table in ("sessions", "session_events", "reservations", "visitors", "confirmations", "metadata"):
                        exists = conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone()
                        if exists:
                            conn.execute(f"ALTER TABLE {table} RENAME TO aurora_legacy_{table}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS aurora_sessions (
                    id TEXT PRIMARY KEY,
                    apartment TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aurora_session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES aurora_sessions(id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS aurora_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    apartment TEXT NOT NULL,
                    area TEXT NOT NULL,
                    date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_aurora_active_area_date
                    ON aurora_reservations(area, date) WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS aurora_visitors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    apartment TEXT NOT NULL,
                    name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(apartment, name, date)
                );
                CREATE TABLE IF NOT EXISTS aurora_confirmations (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES aurora_sessions(id),
                    kind TEXT NOT NULL,
                    details TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS aurora_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            if conn.execute("SELECT 1 FROM aurora_metadata WHERE key='reservation_counter'").fetchone() is None:
                conn.execute("INSERT INTO aurora_metadata(key, value) VALUES('reservation_counter', '1000')")

    def create_session(self, apartment: str) -> str:
        session_id = str(uuid.uuid4())
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO aurora_sessions(id, apartment, created_at) VALUES (?, ?, ?)",
                (session_id, apartment, utc_now()),
            )
        return session_id

    def session(self, session_id: str) -> sqlite3.Row | None:
        conn = self.connect()
        try:
            return conn.execute("SELECT * FROM aurora_sessions WHERE id=?", (session_id,)).fetchone()
        finally:
            conn.close()

    def add_event(self, session_id: str, event_type: str, author: str, content: Any) -> None:
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM aurora_session_events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            conn.execute(
                "INSERT INTO aurora_session_events(session_id, sequence, event_type, author, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, row[0], event_type, author, json.dumps(content, ensure_ascii=False, sort_keys=True), utc_now()),
            )

    def events(self, session_id: str) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT sequence, event_type, author, content, created_at FROM aurora_session_events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
            return [
                {
                    "sequence": row["sequence"],
                    "tipo": row["event_type"],
                    "autor": row["author"],
                    "conteudo": json.loads(row["content"]),
                    "criado_em": row["created_at"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def pending_confirmations(self, session_id: str) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT id, kind, details FROM aurora_confirmations WHERE session_id=? AND status='pending' ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            return [
                {"id": row["id"], "acao": row["kind"], "detalhes": json.loads(row["details"])}
                for row in rows
            ]
        finally:
            conn.close()


def restore_initial_data(path: Path | str = DATABASE_PATH, clear_sessions: bool = True) -> None:
    """Restore mutable domain data from the protected starter files."""
    db = Database(path)
    with db.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM aurora_reservations")
        conn.execute("DELETE FROM aurora_visitors")
        conn.execute("DELETE FROM aurora_confirmations")
        if clear_sessions:
            conn.execute("DELETE FROM aurora_session_events")
            conn.execute("DELETE FROM aurora_sessions")
        reservations = json.loads((DATA_DIR / "reservas.json").read_text(encoding="utf-8"))
        visitors = json.loads((DATA_DIR / "visitantes.json").read_text(encoding="utf-8"))
        now = utc_now()
        conn.executemany(
            "INSERT INTO aurora_reservations(code, apartment, area, date, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
            [(r["codigo"], r["apartamento"], r["area"], r["data"], now) for r in reservations],
        )
        conn.executemany(
            "INSERT INTO aurora_visitors(apartment, name, date, created_at) VALUES (?, ?, ?, ?)",
            [(v["apartamento"], v["nome"], v["data"], now) for v in visitors],
        )
        conn.execute("UPDATE aurora_metadata SET value='1000' WHERE key='reservation_counter'")
