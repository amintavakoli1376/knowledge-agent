"""Dual-write mirror: SQLite writes also go to Postgres schema `ka`.

Epic 3 — during the dual-write phase the SQLite store stays the read
reference, but every save is also upserted into PG so the cutover is a
flip of the read path. Best-effort: PG failures are logged, never break
the main flow (same contract as the Odoo/Outline mirrors).

Contact points: main.py (api_save) and gateway/telegram_bot.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)


def _pg_enabled() -> bool:
    return getattr(settings, "pg_enabled", False)


def mirror_save(content, analysis=None) -> None:
    """Upsert a content row (+ analysis fields) into ka.content."""
    if not _pg_enabled():
        return
    try:
        from .db_pg import connect_pg

        a = analysis
        # derived columns — same shape as the SQLite save
        _tags = json.dumps(getattr(a, "tags", []) or [], ensure_ascii=False) if a else "[]"
        _key_points = json.dumps(getattr(a, "key_points", []) or [], ensure_ascii=False) if a else "[]"
        _meta = {}
        if a:
            _meta = {
                "entities": list(getattr(a, "entities", []) or []),
                "concepts": list(getattr(a, "concepts", []) or []),
                "connections": list(getattr(a, "connections", []) or []),
                "contradictions": list(getattr(a, "contradictions", []) or []),
            }
        _analysis_meta = json.dumps(_meta, ensure_ascii=False) if any(_meta.values()) else ""
        _status = getattr(a, "analysis_status", "") or "" if a else ""
        _error = getattr(a, "analysis_error", "") or "" if a else ""
        _attempts = int(getattr(a, "analysis_attempts", 0) or 0) if a else 0
        _saved = datetime.now(timezone.utc).isoformat()

        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ka.content
                        (url, title, platform, full_text, author, category, priority,
                         tags, summary_fa, summary_en, key_points, status, metadata,
                         saved_at, starred_at, analysis_meta, analysis_status,
                         analysis_error, analysis_attempts)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)
                    ON CONFLICT (url) DO UPDATE SET
                        title=EXCLUDED.title, platform=EXCLUDED.platform,
                        full_text=EXCLUDED.full_text, author=EXCLUDED.author,
                        category=EXCLUDED.category, priority=EXCLUDED.priority,
                        tags=EXCLUDED.tags, summary_fa=EXCLUDED.summary_fa,
                        summary_en=EXCLUDED.summary_en, key_points=EXCLUDED.key_points,
                        metadata=EXCLUDED.metadata, saved_at=EXCLUDED.saved_at,
                        analysis_meta=EXCLUDED.analysis_meta,
                        analysis_status=EXCLUDED.analysis_status,
                        analysis_error=EXCLUDED.analysis_error,
                        analysis_attempts=EXCLUDED.analysis_attempts
                    """,
                    (
                        content.url,
                        (getattr(content, "title", "") or "")[:200],
                        getattr(content, "platform", "") or "",
                        getattr(content, "full_text", "") or "",
                        getattr(content, "author", "") or "",
                        (getattr(a, "category", "") or "") if a else "",
                        (getattr(a, "priority", "") or "") if a else "",
                        _tags,
                        (getattr(a, "summary_fa", "") or "") if a else "",
                        (getattr(a, "summary_en", "") or "") if a else "",
                        _key_points,
                        "Unread",
                        json.dumps(getattr(content, "metadata", {}) or {}, ensure_ascii=False),
                        _saved,
                        _analysis_meta,
                        _status,
                        _error,
                        _attempts,
                    ),
                )
                conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("PG mirror save skipped (non-fatal): %s", e)


def mirror_analysis(url: str, analysis=None) -> None:
    """Update only the analysis fields of an existing ka.content row."""
    if not _pg_enabled():
        return
    try:
        from .db_pg import connect_pg

        a = analysis
        if a is None:
            return
        _tags = json.dumps(getattr(a, "tags", []) or [], ensure_ascii=False)
        _key_points = json.dumps(getattr(a, "key_points", []) or [], ensure_ascii=False)
        _meta = {
            "entities": list(getattr(a, "entities", []) or []),
            "concepts": list(getattr(a, "concepts", []) or []),
            "connections": list(getattr(a, "connections", []) or []),
            "contradictions": list(getattr(a, "contradictions", []) or []),
        }
        _analysis_meta = json.dumps(_meta, ensure_ascii=False) if any(_meta.values()) else ""
        _status = getattr(a, "analysis_status", "") or ""
        _error = getattr(a, "analysis_error", "") or ""
        _attempts = int(getattr(a, "analysis_attempts", 0) or 0)

        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ka.content SET
                        category=%s, priority=%s, tags=%s, summary_fa=%s,
                        summary_en=%s, key_points=%s, analysis_meta=%s,
                        analysis_status=%s, analysis_error=%s, analysis_attempts=%s
                    WHERE url=%s
                    """,
                    (
                        getattr(a, "category", "") or "",
                        getattr(a, "priority", "") or "",
                        _tags,
                        getattr(a, "summary_fa", "") or "",
                        getattr(a, "summary_en", "") or "",
                        _key_points,
                        _analysis_meta,
                        _status,
                        _error,
                        _attempts,
                        url,
                    ),
                )
                conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("PG mirror analysis skipped (non-fatal): %s", e)
