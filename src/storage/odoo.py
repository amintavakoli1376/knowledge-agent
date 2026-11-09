"""Odoo 19 storage mirror for the Knowledge Agent (Epic 2).

Writes knowledge items into a self-hosted Odoo's ``knowledge.article``
model via JSON-RPC 2.0. Mirrors the pattern of ``outline.py``: a
``save(content, analysis) -> str`` (returns the article URL), dedup via a
SQLite mirror registry, and a get-or-create parent per platform.
"""
from __future__ import annotations

import base64
import re
import sqlite3
from pathlib import Path

from markdown_it import MarkdownIt

from ..config import settings
from ..models import AIAnalysis, ExtractedContent
from .db import connect_db
from .dlq import DLQ
from .logging_util import get_structured_logger
from .odoo_client import OdooClient

logger = get_structured_logger(__name__)

# Markdown → HTML (FR-003): commonmark, HTML خام غیرفعال (امن).
_MD = MarkdownIt("commonmark", {"html": False})

# کاراکترهای فارسی/عربی برای تشخیص RTL (FR-008).
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")

# نگاشت platform → icon (FR-015): طبق جدول بخش ۷ PRD.
PLATFORM_ICON = {
    "arxiv": "📄",
    "youtube": "▶️",
    "website": "🌐",
    "instagram": "📷",
    "linkedin": "💼",
    "telegram": "✈️",
    "twitter": "🐦",
    "pdf": "📄",
    "note": "📝",
}
DEFAULT_ICON = "📄"


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

    def _markdown_to_html(
        self, content: ExtractedContent, analysis: AIAnalysis
    ) -> str:
        """Markdown → HTML معتبر (FR-003) با RTL per-paragraph (FR-008).

        « _markdown() قبلی (که فقط رشته markdown خام می‌ساخت) را جایگزین می‌کند.
        - HTML خام ورودی غیرفعال است (امن، XSS).
        - هر پاراگراف فارسی `dir="rtl"` می‌گیرد؛ غیرفارسی `dir="ltr"`.
        """
        title = content.title or "بدون عنوان"
        parts = [f"# {title}", ""]
        summary = analysis.summary_fa or analysis.summary_en or ""
        if summary:
            parts += [summary, ""]
        parts.append(content.full_text or "")
        md = "\n".join(parts)

        html = _MD.render(md or "")

        # FR-008: RTL per-paragraph — هر پاراگراف/بلوک را بر اساس حضور
        # کاراکتر فارسی جهت‌دهی می‌کنیم.
        # markdown-it پاراگراف‌ها را به‌صورت <p>...</p> (یا <li> و...)
        # رندر می‌کند؛ برای قطعات بدون تگ، wrap با <div dir=...>.
        def _dir(block: str) -> str:
            return "rtl" if _PERSIAN_RE.search(block) else "ltr"

        # فقط بلوک‌های متن (بدون تگ ساختاری) را جهت‌دهی کن.
        # ساده‌ترین و امن‌ترین رویکرد: هر <p> را دوباره با dir wrap کن.
        def _wrap_p(m: re.Match) -> str:
            inner = m.group(1)
            return f'<p dir="{_dir(inner)}">{inner}</p>'

        html = re.sub(r"<p>(.*?)</p>", _wrap_p, html, flags=re.S)
        return html

    def _build_body(
        self, content: ExtractedContent, analysis: AIAnalysis
    ) -> tuple[str, dict | None]:
        """بدنهٔ HTML نهایی + (اختیاری) payload attachment برای محتوای بسیار بزرگ.

        اگر حجم HTML از `odoo_body_size_limit` بگذرد (بخش ۱۰)، بدنهٔ کامل
        به‌صورت `ir.attachment` (base64) ساخته می‌شود و بدنهٔ مقاله فقط
        خلاصه + لینک «مشاهدهٔ کامل» می‌شود.

        Returns (body_html, attachment_payload | None). payload شامل `datas`
        (base64) و `mimetype` است و بعد از ساخت مقاله با `res_id` کامل می‌شود.
        """
        html = self._markdown_to_html(content, analysis)
        limit = getattr(self._cfg, "odoo_body_size_limit", 0)
        if not limit or len(html) <= limit:
            return html, None

        title = content.title or "بدون عنوان"
        summary = analysis.summary_fa or analysis.summary_en or ""
        short_md = f"# {title}\n\n{summary}" if summary else f"# {title}"
        short = _MD.render(short_md)
        # لینک بعد از ساخت مقاله با `res_id` واقعی ساخته می‌شود؛ اینجا یک
        # placeholder کوتاه می‌گذاریم که بعداً replace می‌شود.
        short += "\n<p><a href=\"__FULL_LINK__\">مشاهدهٔ محتوای کامل</a></p>\n"

        payload = {
            "name": f"full-content-{content.url[:40]}",
            "type": "binary",
            "mimetype": "text/html",
            "datas": base64.b64encode(html.encode("utf-8")).decode(),
        }
        return short, payload

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
            body, attachment = self._build_body(content, analysis)
            vals = {
                "name": (content.title or "Untitled")[:255],
                "body": body,
                "article_url": url,
                "parent_id": parent_id,
                "icon": PLATFORM_ICON.get(content.platform, DEFAULT_ICON),
            }
            odoo_id = int(
                self._client.execute_kw("knowledge.article", "create", [vals])
            )

            if attachment is not None:
                try:
                    attachment["res_id"] = odoo_id
                    attachment["res_model"] = "knowledge.article"
                    att_id = int(
                        self._client.execute_kw(
                            "ir.attachment", "create", [attachment]
                        )
                    )
                    full_link = (
                        f"{self._cfg.odoo_url.rstrip('/')}"
                        f"/web/content/{att_id}?download=true"
                    )
                    # بدنه را با لینک واقعی به‌روز کن.
                    self._client.execute_kw(
                        "knowledge.article",
                        "write",
                        [[odoo_id], {"body": body.replace("__FULL_LINK__", full_link)}],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Odoo attachment add failed (non-fatal): %s", exc
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Odoo write failed; enqueueing DLQ: %s", exc)
            self._dlq.enqueue(url, {"source_url": url}, str(exc))
            return None
        self._register_mirror(url, odoo_id, "knowledge.article", content.title)
        logger.info("Odoo article created id=%s url=%s", odoo_id, url)
        return self._article_url(odoo_id)
