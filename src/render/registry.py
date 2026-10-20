"""Registry: نگاشت URL منبع → doc_id اووت‌لاین + کش فهرست اسناد.

- ثبت doc_id بعد از هر ذخیرهٔ جدید (برای لینک‌دادن /view در جدول شاخص)
- backfill یک‌بارهٔ اسناد موجود
- کش TTL کوتاه برای sidebar/index تا به rate-limit Outline نخوریم
"""

from __future__ import annotations

import json
import logging
import sqlite3
import ssl
import time
from pathlib import Path
from typing import Any

from src.config import settings
from ..storage.db import connect_db

logger = logging.getLogger(__name__)

_DB_PATH = Path(settings.data_dir) / "outline_docs.sqlite3"

_yesterday = 0.0
_cache: dict[str, Any] = {}


def _con() -> sqlite3.Connection:
    con = connect_db(_DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS outline_docs ("
        " source_url TEXT PRIMARY KEY,"
        " doc_id TEXT NOT NULL,"
        " title TEXT,"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return con


def register(source_url: str, doc_id: str, title: str = "") -> None:
    if not source_url or not doc_id:
        return
    try:
        with _con() as con:
            con.execute(
                "INSERT INTO outline_docs (source_url, doc_id, title) VALUES (?,?,?) "
                "ON CONFLICT(source_url) DO UPDATE SET doc_id=excluded.doc_id, title=excluded.title",
                (source_url, doc_id, title),
            )
    except sqlite3.Error:  # pragma: no cover
        logger.warning("outline_docs registry write failed", exc_info=True)


def get_doc_id(source_url: str) -> str | None:
    try:
        with _con() as con:
            row = con.execute(
                "SELECT doc_id FROM outline_docs WHERE source_url = ?", (source_url,)
            ).fetchone()
            return row[0] if row else None
    except sqlite3.Error:  # pragma: no cover
        return None


def all_docs() -> list[tuple[str, str, str]]:
    try:
        with _con() as con:
            return con.execute(
                "SELECT source_url, doc_id, title FROM outline_docs ORDER BY created_at DESC"
            ).fetchall()
    except sqlite3.Error:  # pragma: no cover
        return []


# ---------------- Outline API با کش ----------------

_ctx = ssl._create_unverified_context()


def _api(method: str, payload: dict) -> Any:
    import urllib.request

    req = urllib.request.Request(
        settings.outline_url.rstrip("/") + "/api/" + method,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {settings.outline_api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
        return json.load(r)


def _cached(key: str, ttl: float, fn):
    now = time.time()
    entry = _cache.get(key)
    if entry and now - entry[0] < ttl:
        return entry[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def list_collection_docs(limit: int = 100) -> list[dict]:
    """اسناد کالکشن Knowledge (با کش 60 ثانیهای)."""

    def _fetch() -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            res = _api(
                "documents.list",
                {
                    "collectionId": settings.outline_collection_id,
                    "limit": min(50, limit - len(out)) or 50,
                    "offset": offset,
                    "sort": "updatedAt",
                    "direction": "DESC",
                },
            )
            docs = res.get("data", [])
            out.extend(docs)
            if len(docs) < 50 or len(out) >= limit:
                break
            offset += len(docs)
        return out

    try:
        return _cached("collection_docs", 60.0, _fetch)
    except Exception:  # pragma: no cover
        logger.warning("documents.list failed", exc_info=True)
        return _cache.get("collection_docs", (0, []))[1]


def get_document(doc_id: str) -> dict | None:
    def _fetch() -> dict | None:
        res = _api("documents.info", {"id": doc_id})
        return res.get("data")

    try:
        return _cached(f"doc:{doc_id}", 60.0, _fetch)
    except Exception:  # pragma: no cover
        logger.warning("documents.info failed for %s", doc_id, exc_info=True)
        return None
