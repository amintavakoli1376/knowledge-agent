"""SQLite storage for full extracted content.

Stores the complete text of every saved item — the raw material for
future RAG / vector search. Saving is best-effort: failures never block
the main Notion pipeline.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import ExtractedContent

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "knowledge.db"


def _get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables + FTS5 full-text index if missing."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT,
                platform TEXT,
                full_text TEXT,
                author TEXT,
                metadata TEXT,
                saved_at TEXT NOT NULL
            )
            """
        )
        # FTS5 full-text index (free search over the content)
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
                    url, title, full_text,
                    content='content', content_rowid='id'
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS content_ai AFTER INSERT ON content BEGIN
                    INSERT INTO content_fts(rowid, url, title, full_text)
                    VALUES (new.id, new.url, new.title, new.full_text);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS content_ad AFTER DELETE ON content BEGIN
                    INSERT INTO content_fts(content_fts, rowid, url, title, full_text)
                    VALUES ('delete', old.id, old.url, old.title, old.full_text);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS content_au AFTER UPDATE ON content BEGIN
                    INSERT INTO content_fts(content_fts, rowid, url, title, full_text)
                    VALUES ('delete', old.id, old.url, old.title, old.full_text);
                    INSERT INTO content_fts(rowid, url, title, full_text)
                    VALUES (new.id, new.url, new.title, new.full_text);
                END
                """
            )
        except sqlite3.OperationalError:
            # FTS5 unavailable (very old SQLite) — content table still works
            pass
        conn.commit()
    finally:
        conn.close()


def save_content(content: ExtractedContent) -> None:
    """Save full extracted content to SQLite. Never raises (best-effort)."""
    try:
        init_db()
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO content
                    (url, title, platform, full_text, author, metadata, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content.url,
                    (content.title or "")[:200],
                    content.platform or "",
                    content.full_text or "",
                    getattr(content, "author", "") or "",
                    json.dumps(content.metadata or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"SQLite save skipped: {e}")


def count_content() -> int:
    """Number of stored items (for verification/monitoring)."""
    try:
        init_db()
        conn = _get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM content").fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def search_text(query: str, limit: int = 10) -> list:
    """Full-text search over stored content (FTS5)."""
    try:
        init_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                """
                SELECT url, title, snippet(content_fts) AS snippet, saved_at
                FROM content_fts
                WHERE content_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []