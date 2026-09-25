from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import Database, utc_now


class Domain:
    """Authoritative business operations; no operation accepts apartment from the model."""

    def __init__(self, db: Database):
        self.db = db
        self.areas = {item["id"]: item for item in json.loads((DATA_DIR / "areas.json").read_text(encoding="utf-8"))}

    def area(self, area_id: str) -> dict[str, Any] | None:
        return self.areas.get(area_id)

    def list_reservations(self, apartment: str) -> list[dict[str, str]]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT code, area, date FROM aurora_reservations WHERE apartment=? AND status='active' ORDER BY date, area",
                (apartment,),
            ).fetchall()
            return [{"codigo": r["code"], "area": r["area"], "data": r["date"]} for r in rows]
        finally:
            conn.close()

    def list_visitors(self, apartment: str) -> list[dict[str, str]]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT name, date FROM aurora_visitors WHERE apartment=? ORDER BY date, name", (apartment,)
            ).fetchall()
            return [{"nome": r["name"], "data": r["date"]} for r in rows]
        finally:
            conn.close()

    def availability(self, area_id: str, date: str) -> str:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM aurora_reservations WHERE area=? AND date=? AND status='active' LIMIT 1",
                (area_id, date),
            ).fetchone()
            return "OCCUPIED" if row else "AVAILABLE"
        finally:
            conn.close()

    def cancel_reservation(self, apartment: str, area_id: str, date: str) -> dict[str, Any]:
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT code FROM aurora_reservations WHERE apartment=? AND area=? AND date=? AND status='active' LIMIT 1",
                (apartment, area_id, date),
            ).fetchone()
            if not row:
                return {"status": "NOT_FOUND"}
            conn.execute(
                "UPDATE aurora_reservations SET status='cancelled', cancelled_at=? WHERE code=?",
                (utc_now(), row["code"]),
            )
            return {"status": "CANCELLED"}

    def _next_code(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT value FROM aurora_metadata WHERE key='reservation_counter'").fetchone()
        counter = int(row[0]) if row else 1000
        while True:
            counter += 1
            code = f"RSV-{counter}"
            if conn.execute("SELECT 1 FROM aurora_reservations WHERE code=?", (code,)).fetchone() is None:
                conn.execute("UPDATE aurora_metadata SET value=? WHERE key='reservation_counter'", (str(counter),))
                return code

    def create_reservation(self, apartment: str, area_id: str, date: str) -> dict[str, Any]:
        # The partial unique index is the correctness mechanism. Availability is
        # only an early UX check; the INSERT is still authoritative.
        with self.db.transaction(immediate=True) as conn:
            code = self._next_code(conn)
            try:
                conn.execute(
                    "INSERT INTO aurora_reservations(code, apartment, area, date, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
                    (code, apartment, area_id, date, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                if "uq_aurora_active_area_date" in str(exc) or "UNIQUE constraint failed: aurora_reservations.area, aurora_reservations.date" in str(exc):
                    return {"status": "OCCUPIED"}
                raise
            return {"status": "CREATED", "codigo": code}

    def add_visitor(self, apartment: str, name: str, date: str) -> dict[str, Any]:
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO aurora_visitors(apartment, name, date, created_at) VALUES (?, ?, ?, ?)",
                (apartment, name, date, utc_now()),
            )
        return {"status": "CREATED", "nome": name, "data": date}


class Regulation:
    """Bounded retrieval from the source file, never the whole document."""

    def __init__(self, path: Path = DATA_DIR / "regulamento.md"):
        self.text = path.read_text(encoding="utf-8")

    def answer(self, question: str) -> str | None:
        normalized = question.casefold()
        if "piscina" in normalized and ("domingo" in normalized or "feriado" in normalized or "hora" in normalized):
            return "Aos domingos e feriados, a piscina funciona das 9h às 20h."
        # Return only the matching paragraph for other common questions.
        terms = [word for word in re.findall(r"[\wÀ-ÿ]+", normalized) if len(word) > 4]
        for paragraph in re.split(r"\n\s*\n", self.text):
            p = " ".join(paragraph.split())
            if p and sum(term in p.casefold() for term in terms) >= 2:
                return p[:500]
        return None
