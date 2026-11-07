"""DLQ (Dead Letter Queue) for Odoo mirror writes (Epic 2 Task C3).

Persists failed mirror writes in a SQLite table so no source item is lost
permanently. A later recovery pass can retry them.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .db import connect_db

logger = logging.getLogger(__name__)


class DLQ:
    def __init__(self, data_dir: str | Path) -> None:
        self._db = Path(data_dir) / "odoo_dlq.sqlite3"

    def _con(self) -> sqlite3.Connection:
        con = connect_db(self._db)
        con.execute(
            "CREATE TABLE IF NOT EXISTS odoo_dlq ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " source_url TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " error TEXT,"
            " attempts INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        return con

    def enqueue(self, source_url: str, payload: dict, error: str) -> None:
        try:
            with self._con() as con:
                con.execute(
                    "INSERT INTO odoo_dlq (source_url, payload, error) VALUES (?,?,?)",
                    (source_url, json.dumps(payload, ensure_ascii=False), str(error)[:500]),
                )
        except sqlite3.Error:
            logger.warning("DLQ enqueue failed", exc_info=True)

    def pending(self, limit: int = 20) -> list[dict]:
        try:
            with self._con() as con:
                rows = con.execute(
                    "SELECT id, source_url, payload, error, attempts FROM odoo_dlq "
                    "ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()
            out = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"])
                except Exception:
                    payload = {"raw": r["payload"]}
                out.append({
                    "id": r["id"],
                    "source_url": r["source_url"],
                    "payload": payload,
                    "error": r["error"],
                    "attempts": r["attempts"],
                })
            return out
        except sqlite3.Error:
            return []

    def mark_failed(self, row_id: int, error: str) -> None:
        try:
            with self._con() as con:
                con.execute(
                    "UPDATE odoo_dlq SET attempts = attempts + 1, error = ? WHERE id = ?",
                    (str(error)[:500], row_id),
                )
        except sqlite3.Error:
            logger.warning("DLQ mark_failed failed", exc_info=True)

    def mark_success(self, row_id: int) -> None:
        try:
            with self._con() as con:
                con.execute("DELETE FROM odoo_dlq WHERE id = ?", (row_id,))
        except sqlite3.Error:
            logger.warning("DLQ mark_success failed", exc_info=True)

    def count(self) -> int:
        try:
            with self._con() as con:
                return con.execute("SELECT COUNT(*) FROM odoo_dlq").fetchone()[0]
        except sqlite3.Error:
            return 0
