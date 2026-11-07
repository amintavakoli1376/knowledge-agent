"""Odoo 19 storage mirror for the Knowledge Agent (Epic 2).

Writes knowledge items into a self-hosted Odoo's ``knowledge.article``
model via JSON-RPC 2.0. Mirrors the pattern of ``outline.py``: a
``save(content, analysis) -> str`` (returns the article URL), dedup via a
SQLite mirror registry, and a get-or-create parent per platform.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ..config import settings
from ..models import AIAnalysis, ExtractedContent
from .db import connect_db
from .dlq import DLQ
from .odoo_client import OdooClient

logger = logging.getLogger(__name__)


class OdooStorage:
    """Writes knowledge items into Odoo's knowledge.article model."""

    def __init__(self, client=None, cfg=settings, data_dir=None, dlq=None) -> None:
        self._cfg = cfg
        self._client = client or OdooClient(cfg)
        base = Path(data_dir) if data_dir else Path(cfg.data_dir)
        self._mirror_db = base / "odoo_articles.sqlite3"
        self._dlq = dlq if dlq is not None else DLQ(base)

    # -- internals -------------------------------------------------------

    def _mirror_con(self) -> sqlite3.Connection:
        con = connect_db(self._mirror_db)
        con.execute(
            "CREATE TABLE IF NOT EXISTS odoo_articles ("
            " source_url TEXT PRIMARY KEY,"
            " odoo_id INTEGER NOT NULL,"
            " model TEXT NOT NULL,"
            " title TEXT,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        return con

    def _get_mirror(self, source_url: str) -> tuple[int, str] | None:
        try:
            with self._mirror_con() as con:
                row = con.execute(
                    "SELECT odoo_id, model FROM odoo_articles WHERE source_url = ?",
                    (source_url,),
                ).fetchone()
                return (row[0], row[1]) if row else None
        except sqlite3.Error:
            logger.warning("odoo mirror read failed", exc_info=True)
            return None

    def _register_mirror(
        self, source_url: str, odoo_id: int, model: str, title: str
    ) -> None:
        try:
            with self._mirror_con() as con:
                con.execute(
                    "INSERT INTO odoo_articles (source_url, odoo_id, model, title) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(source_url) DO UPDATE SET "
                    " odoo_id=excluded.odoo_id, model=excluded.model, title=excluded.title",
                    (source_url, odoo_id, model, title),
                )
        except sqlite3.Error:
            logger.warning("odoo mirror write failed", exc_info=True)

    def _get_or_create_parent(self, platform: str) -> int:
        """Find the platform parent article, creating it if missing."""
        rows = self._client.execute_kw(
            "knowledge.article",
            "search_read",
            [[["name", "=", platform]]],
            {"fields": ["id"], "limit": 1},
        )
        if rows:
            return int(rows[0]["id"])
        return int(
            self._client.execute_kw(
                "knowledge.article", "create", [{"name": platform}]
            )
        )

    def _article_url(self, odoo_id: int) -> str:
        base = self._cfg.odoo_url.rstrip("/")
        return f"{base}/web#id={odoo_id}&model=knowledge.article"

    def _markdown(self, content: ExtractedContent, analysis: AIAnalysis) -> str:
        title = content.title or "بدون عنوان"
        parts = [f"# {title}", ""]
        summary = analysis.summary_fa or analysis.summary_en or ""
        if summary:
            parts += [summary, ""]
        parts.append(content.full_text or "")
        return "\n".join(parts)

    # -- public ----------------------------------------------------------

    def save(self, content: ExtractedContent, analysis: AIAnalysis) -> str | None:
        """Create/return the Odoo mirror article for an item. Returns URL str.

        Returns None when Odoo is disabled or the item has no source URL.
        """
        if not self._cfg.odoo_enabled:
            return None
        url = (content.url or "").strip()
        if not url:
            return None

        mirror = self._get_mirror(url)
        if mirror:
            return self._article_url(mirror[0])

        try:
            parent_id = self._get_or_create_parent(content.platform or "website")
            vals = {
                "name": (content.title or "Untitled")[:255],
                "body": self._markdown(content, analysis),
                "article_url": url,
                "parent_id": parent_id,
            }
            odoo_id = int(
                self._client.execute_kw("knowledge.article", "create", [vals])
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Odoo write failed; enqueueing DLQ: %s", exc)
            self._dlq.enqueue(url, {"source_url": url}, str(exc))
            return None
        self._register_mirror(url, odoo_id, "knowledge.article", content.title)
        logger.info("Odoo article created id=%s url=%s", odoo_id, url)
        return self._article_url(odoo_id)
