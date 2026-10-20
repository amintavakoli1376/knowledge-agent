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
from .db import connect_db

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "knowledge.db"


def _get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect_db(DB_PATH)
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
                category TEXT,
                priority TEXT,
                tags TEXT,
                summary_fa TEXT,
                summary_en TEXT,
                key_points TEXT,
                status TEXT DEFAULT 'Unread',
                metadata TEXT,
                saved_at TEXT NOT NULL
            )
            """
        )
        # Migration: add columns to older databases
        for col, ddl in [
            ("category", "TEXT"),
            ("priority", "TEXT"),
            ("tags", "TEXT"),
            ("summary_fa", "TEXT"),
            ("summary_en", "TEXT"),
            ("key_points", "TEXT"),
            ("status", "TEXT DEFAULT 'Unread'"),
            ("starred_at", "TEXT"),  # U2b: زمان ستاره‌دار کردن (حذف نرم = Archived)
            ("analysis_meta", "TEXT"),  # LLM-Wiki 1a: JSON تحلیل دومرحله‌ای
            # مقاوم‌سازی 429-quota (t_c270e218): وضعیت تحلیل + خطا + شمارنده تلاش
            ("analysis_status", "TEXT DEFAULT ''"),
            ("analysis_error", "TEXT"),
            ("analysis_attempts", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE content ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # column already exists

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

        # U4-min: نسخه‌بندی ساده — snapshot خودکار قبل از رونویسی هر سند موجود
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS doc_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    full_text TEXT NOT NULL,
                    title TEXT,
                    summary_fa TEXT,
                    tags TEXT,
                    category TEXT,
                    saved_at TEXT,
                    snapshotted_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_versions_url ON doc_versions(url, id DESC)"
            )
        except sqlite3.OperationalError:
            pass  # versioning best-effort — نباید save را بشکند
        conn.commit()
    finally:
        conn.close()


_MAX_VERSIONS_PER_DOC = 5


def _snapshot_if_exists(conn: sqlite3.Connection, url: str) -> None:
    """قبل از رونویسی: نسخهٔ فعلی سند را در doc_versions کپی می‌کند (U4-min).

    فقط وقتی ردیف موجود واقعاً متن دارد؛ قدیمی‌ترین نسخه‌ها حذف می‌شوند (آخرین ۵).
    best-effort — هیچ خطایی به بالا نمی‌رود.
    """
    try:
        row = conn.execute(
            "SELECT full_text, title, summary_fa, tags, category, saved_at"
            " FROM content WHERE url IN (?, ?)",
            (url, url.rstrip("/")),
        ).fetchone()
        if not row or not (row[0] or "").strip():
            return  # سند خالی/ناموجود → نسخه‌ای برای نگه‌داشتن نیست
        conn.execute(
            "INSERT INTO doc_versions (url, full_text, title, summary_fa, tags, category, saved_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, row[0], row[1], row[2], row[3], row[4], row[5]),
        )
        conn.execute(
            "DELETE FROM doc_versions WHERE url IN (?, ?) AND id NOT IN ("
            "  SELECT id FROM doc_versions WHERE url IN (?, ?) ORDER BY id DESC LIMIT ?"
            ")",
            (url, url.rstrip("/"), url, url.rstrip("/"), _MAX_VERSIONS_PER_DOC),
        )
    except Exception as e:  # noqa: BLE001 — snapshot نباید save را بشکند
        logging.getLogger(__name__).warning(f"doc snapshot skipped: {e}")


def _analysis_flag_values(analysis) -> tuple:
    """(status, error, attempts) از analysis — سازگار با آبجکت‌های قدیمی بدون flag."""
    return (
        getattr(analysis, "analysis_status", "") or "",
        getattr(analysis, "analysis_error", "") or "",
        int(getattr(analysis, "analysis_attempts", 0) or 0),
    )


def save_content(content: ExtractedContent, analysis=None) -> None:
    """Save full extracted content to SQLite. Never raises (best-effort).

    analysis: optional AIAnalysis — its fields (summary_fa, summary_en,
    key_points, category, tags, priority) are stored alongside the content.
    """
    try:
        init_db()
        conn = _get_connection()
        try:
            # U4-min: اگر ردیف موجود است، قبل از رونویسی snapshot بگیر
            _snapshot_if_exists(conn, content.url)
            a_status, a_error, a_attempts = _analysis_flag_values(analysis) if analysis else ("", "", 0)
            conn.execute(
                """
                INSERT INTO content
                    (url, title, platform, full_text, author,
                     category, priority, tags, summary_fa, summary_en,
                     key_points, status, metadata, saved_at,
                     analysis_status, analysis_error, analysis_attempts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, platform=excluded.platform,
                    full_text=excluded.full_text, author=excluded.author,
                    category=excluded.category, priority=excluded.priority,
                    tags=excluded.tags, summary_fa=excluded.summary_fa,
                    summary_en=excluded.summary_en, key_points=excluded.key_points,
                    -- status کاربر (U2b) با re-save ذخیرهٔ جدید نمی‌پرد
                    status=content.status, metadata=excluded.metadata,
                    saved_at=excluded.saved_at,
                    analysis_status=excluded.analysis_status,
                    analysis_error=excluded.analysis_error,
                    analysis_attempts=excluded.analysis_attempts
                """,
                (
                    content.url,
                    (content.title or "")[:200],
                    content.platform or "",
                    content.full_text or "",
                    getattr(content, "author", "") or "",
                    (analysis.category if analysis else "") or "",
                    (analysis.priority if analysis else "") or "",
                    json.dumps(analysis.tags if analysis else [], ensure_ascii=False),
                    (analysis.summary_fa if analysis else "") or "",
                    (analysis.summary_en if analysis else "") or "",
                    json.dumps(analysis.key_points if analysis else [], ensure_ascii=False),
                    "Unread",
                    json.dumps(content.metadata or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    a_status,
                    a_error,
                    a_attempts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"SQLite save skipped: {e}")


def _url_variants(url: str) -> list[str]:
    """variants of a URL that extractors may have normalized (scheme, slash, arxiv vN)."""
    import re as _re

    base = (url or "").strip()
    if not base:
        return []
    out = [base]
    no_slash = base.rstrip("/")
    out.append(no_slash)
    if no_slash.startswith("https://"):
        out.append("http://" + no_slash[len("https://"):])
    elif no_slash.startswith("http://"):
        out.append("https://" + no_slash[len("http://"):])
    stripped = _re.sub(r"v\d+$", "", no_slash)  # arxiv: .../abs/2402.09739v3 → .../2402.09739
    if stripped != no_slash:
        out.append(stripped)
        out.append(stripped.replace("https://", "http://") if stripped.startswith("https://") else stripped.replace("http://", "https://"))
    seen, uniq = set(), []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def save_analysis(url: str, analysis) -> None:
    """Update the AI-analysis fields of an existing row (by url).

    URL اکسترکتور ممکن است با URL ورودی فرق کند (http/https، اسلش انتهایی،
    نسخهٔ arxiv مثل v3). قبلاً همان URL خام در WHERE می‌رفت و اگر ایندکس را
    نمی‌زد، خلاصه در دیتابیس خالی می‌ماند بی‌صدا. حالا variants امتحان می‌شود
    و در صورت عدم تطابق، warning لاگ می‌شود.
    """
    try:
        init_db()
        conn = _get_connection()
        try:
            # U4-min: قبل از بازنویسی خلاصه/برچسب‌ها، نسخهٔ فعلی را نگه دار
            _snapshot_if_exists(conn, url)
            # LLM-Wiki 1a: تحلیل ساختارمند — اگر مدل پاس ۱ را نداد، خالی ذخیره می‌شود
            meta = {
                "entities": list(getattr(analysis, "entities", []) or []),
                "concepts": list(getattr(analysis, "concepts", []) or []),
                "connections": list(getattr(analysis, "connections", []) or []),
                "contradictions": list(getattr(analysis, "contradictions", []) or []),
            }
            a_status, a_error, a_attempts = _analysis_flag_values(analysis)
            sql = """
                UPDATE content SET
                    category=?, priority=?, tags=?, summary_fa=?,
                    summary_en=?, key_points=?, analysis_meta=?,
                    analysis_status=?, analysis_error=?, analysis_attempts=?
                WHERE url=?
                """
            params = (
                analysis.category or "",
                analysis.priority or "",
                json.dumps(analysis.tags or [], ensure_ascii=False),
                analysis.summary_fa or "",
                analysis.summary_en or "",
                json.dumps(analysis.key_points or [], ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False) if any(meta.values()) else "",
                a_status,
                a_error,
                a_attempts,
            )
            updated = 0
            matched = ""
            for cand in _url_variants(url):
                cur = conn.execute(sql, params + (cand,))
                updated += cur.rowcount or 0
                if updated:
                    matched = cand
                    break
            conn.commit()
            if not updated:
                logging.getLogger(__name__).warning(
                    "save_analysis: no content row matched %s (variants tried: %s) — analysis not persisted",
                    url,
                    _url_variants(url),
                )
            elif matched:
                # گراف مفاهیم مستقل از Outline: قبلاً فقط مسیر Outline مفهوم ثبت
                # می‌کرد، پس در نصب بدون Outline هیچ مفهوم مشترکی پیدا نمی‌شد.
                try:
                    row = conn.execute(
                        "SELECT id FROM content WHERE url = ?", (matched,)
                    ).fetchone()
                    concepts = list(getattr(analysis, "concepts", []) or [])
                    if row and row[0] and concepts:
                        from src.storage import concepts as _concepts

                        _concepts.register_doc_concepts(int(row[0]), concepts)
                except Exception as e:  # noqa: BLE001 — best-effort
                    logging.getLogger(__name__).warning(f"concept register skipped: {e}")
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"SQLite analysis save skipped: {e}")


def get_by_url(url: str) -> dict | None:
    """Return stored row (with summary) for a URL, or None if not saved."""
    try:
        init_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM content WHERE url = ? LIMIT 1",
                (url,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"SQLite get_by_url failed: {e}")
        return None


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


# ---------------------------------------------------------------------------
# مقاوم‌سازی 429-quota (t_c270e218) — pending_resummarize
# ---------------------------------------------------------------------------

def get_pending_resummarize(max_attempts: int = 5, limit: int = 10) -> list:
    """رکوردهایی که خلاصهٔ آن‌ها به‌خاطر سهمیهٔ AI ناقص ماند و هنوز قابل تلاش‌اند.

    خروجی: لیست dict مرتب‌شده بر اساس قدیمی‌ترین تلاش ناموفق (FIFO).
    """
    try:
        init_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, url, title, platform, full_text, author,
                       analysis_attempts
                FROM content
                WHERE analysis_status = 'pending_resummarize'
                  AND COALESCE(analysis_attempts, 0) < ?
                ORDER BY saved_at ASC
                LIMIT ?
                """,
                (max_attempts, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"get_pending_resummarize failed: {e}")
        return []


def mark_analysis_complete(url: str, analysis) -> None:
    """موفقیت ریکاوری: خلاصه‌های نهایی + پاک‌کردن flag pending."""
    save_analysis(url, analysis)


def mark_analysis_failed(url: str, error: str, attempts: int) -> None:
    """شکست ریکاوری: شمارنده تلاش + پیام خطا ثبت می‌شود، status pending می‌ماند."""
    try:
        init_db()
        conn = _get_connection()
        try:
            conn.execute(
                """
                UPDATE content SET
                    analysis_status='pending_resummarize',
                    analysis_error=?,
                    analysis_attempts=?
                WHERE url=?
                """,
                ((error or "")[:200], int(attempts), url),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"mark_analysis_failed skipped: {e}")


def clear_analysis_status(url: str) -> None:
    """پاک‌کردن کامل flag (وقتی سند Notion پیدا نشد و رها می‌شود)."""
    try:
        init_db()
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE content SET analysis_status='', analysis_error='' WHERE url=?",
                (url,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"clear_analysis_status skipped: {e}")