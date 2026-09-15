"""Outline (self-hosted) storage for the Knowledge Agent.

Pure REST backend path (API key auth) — no browser needed. Creates a
published doc (markdown) in the KA collection and returns its public URL.
"""
from __future__ import annotations

import json
import logging
import ssl
import datetime
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from ..config import settings
from ..models import AIAnalysis, ExtractedContent

logger = logging.getLogger(__name__)


class OutlineStorage:
    """Writes knowledge items into a self-hosted Outline instance."""

    def __init__(self) -> None:
        self._ctx = ssl._create_unverified_context()  # self-signed test cert

    # -- internals -------------------------------------------------------
    def _api(self, path: str, payload: dict) -> dict:
        url = settings.outline_url.rstrip("/") + "/api/" + path
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + settings.outline_api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Outline API {path} failed ({e.code}): {e.read().decode()[:200]}") from e

    @staticmethod
    def _markdown(content: ExtractedContent, analysis: AIAnalysis, shared_concepts: list[str] | None = None) -> str:
        title = getattr(analysis, "title", None) or content.title or "بدون عنوان"
        parts = [f"# {title}", ""]
        summary = getattr(analysis, "summary_fa", "") or getattr(analysis, "summary_en", "") or ""
        if summary:
            parts += [summary, ""]
        points = getattr(analysis, "key_points", None) or []
        if points:
            parts.append("## نکات کلیدی")
            # هر نکته در خط جدید — با \n\n جدا شود تا Outline بولت واقعی رندر کند
            parts.append("\n".join(f"- {p}" for p in points))
            parts.append("")
        # LLM-Wiki 1b: مفاهیم مشترک (۲+ سند) → [[ویکی‌لینک]] — در Outline به جست‌وجوی مفهوم لینک می‌شود
        if shared_concepts:
            links = "، ".join(f"[[{c}]]" for c in shared_concepts[:6])
            parts += [f"**مفاهیم مرتبط:** {links}", ""]
        # R-fix (خواست کاربر 2026-09-15): خطوط «منبع/برچسبها» در md ننویسیم —
        # این‌ها در UI کاغذ (properties) بالای سند نمایش داده می‌شوند؛ تکرار در بدنه = UI زشت.
        # لینک اصلی هم داخل سند Outline لازم نیست (سند خودش از منبع ساخته شده).
        return "\n".join(parts)

    # -- index table -----------------------------------------------------
    # فرمت ۱۰ ستونی — Summary (EN) در جدول نیست (خواست کاربر)؛ دیتابیس/Notion کامل می‌ماند:
    # تاریخ | تیتر | دسته | پلتفرم | وضعیت | نویسنده | برچسب‌ها | URL | خلاصه (فا) | سند
    @staticmethod
    def _index_header() -> str:
        return (
            "# 📊 شاخص دانش — Knowledge Index\n\n"
            "جدول اصلی همهٔ اسناد ذخیره‌شده توسط Knowledge Agent. "
            "هر سند جدید خودکار یک ردیف اضافه می‌کند.\n\n"
            "| تاریخ | تیتر | دسته | پلتفرم | وضعیت | نویسنده | برچسب‌ها | URL | خلاصه (فا) | سند |\n"
            "|-------|-------|------|---------|--------|---------|----------|-----|------------|------|\n"
        )

    @staticmethod
    def _index_row(dsaved: str, title: str, category: str, platform: str,
                   status: str, author: str, tags: list[str], url: str,
                   sfa: str, doc_url: str) -> str:
        def cell(s, limit=80):
            s = (s or "").replace("|", "،").replace("\n", " ").strip()
            if len(s) > limit:
                s = s[: limit - 1] + "…"
            return s or "-"

        host = ""
        if url:
            try:
                host = urlparse(url).netloc or ""
            except ValueError:
                host = ""
        doc_link = f"[📄 سند]({doc_url})" if doc_url else "-"
        url_cell = f"[{host}]({url})" if (url and host) else "-"
        return "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            cell(dsaved, 12),
            f"[{cell(title, 80)}]({doc_url or url or ''})",
            cell(category, 20),
            cell(platform, 14),
            cell(status, 12),
            cell(author, 24),
            cell(tags and "، ".join(tags) or "-", 40),
            url_cell,
            cell(sfa, 80),
            doc_link,
        )

    def _append_index_row(self, content: ExtractedContent, analysis: AIAnalysis, doc_url: str) -> None:
        """Best-effort: add a row to the Knowledge Index table doc (10-column format)."""
        doc_id = getattr(settings, "outline_index_doc_id", "") or ""
        if not doc_id:
            return
        try:
            info = self._api("documents.info", {"id": doc_id})
            text = (info.get("data") or {}).get("text") or ""
            if "| خلاصه (فا) |" not in text:
                # جدول هنوز فرمت جدید (بدون Summary EN) ندارد → بازسازی دستی لازم است
                logger.info("Index doc not in 10-col format; skip row (rebuild needed)")
                return
            # دی‌داپ: اگر URL این محتوا از قبل در جدول هست، ردیف نزنیم
            url = content.url or ""
            if url and f"]({url})" in text:
                logger.info("Index row exists for URL; skip duplicate")
                return
            row = self._index_row(
                datetime.date.today().isoformat(),
                getattr(analysis, "title", None) or content.title or "بدون عنوان",
                getattr(analysis, "category", "") or "",
                content.platform or "",
                "Unread",
                content.author or "",
                getattr(analysis, "tags", None) or [],
                url,
                getattr(analysis, "summary_fa", "") or "",
                doc_url,
            )
            lines = text.split("\n")
            out = []
            inserted = False
            for ln in lines:
                out.append(ln)
                if not inserted and ln.strip().startswith("|-------"):
                    # ردیف جدید اول جدول (نزولی: جدیدترین اول) — بعد از سطر جداکننده
                    out.append(row)
                    inserted = True
            if inserted:
                self._api("documents.update", {"id": doc_id, "text": "\n".join(out)})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Outline index row append failed (non-fatal): {e}")

    # -- public ----------------------------------------------------------
    def save(self, content: ExtractedContent, analysis: AIAnalysis) -> str:
        """Create a published Outline doc; returns its public URL.

        دی‌داپ: اگر URL منبع از قبل در رجیستری هست، سند جدید نساز —
        URL موجود برگردد (idempotent).
        """
        if not settings.outline_enabled:
            raise RuntimeError("Outline not enabled")
        title = getattr(analysis, "title", None) or content.title or "بدون عنوان"
        try:
            from src.render import registry as _registry

            existing_id = _registry.get_doc_id(content.url or "")
            if existing_id:
                info = self._api("documents.info", {"id": existing_id})
                existing_url = (info.get("data") or {}).get("url") or ""
                if existing_url:
                    logger.info("Outline doc exists for URL; reuse (dedup)")
                    return urljoin(
                        settings.outline_url.rstrip("/") + "/", existing_url.lstrip("/")
                    )
        except Exception:  # noqa: BLE001 — dedup is best-effort
            logger.debug("kaghaz dedup check skipped", exc_info=True)
        # LLM-Wiki 1b: مفاهیم مشترکِ موجود (قبل از create) — برای ویکی‌لینک در md
        shared: list[str] = []
        try:
            from . import concepts as _concepts

            shared = _concepts.shared_concepts_for_preview(
                list(getattr(analysis, "concepts", []) or [])
            )
        except Exception:  # noqa: BLE001
            logger.debug("concept preview skipped", exc_info=True)
        md = self._markdown(content, analysis, shared_concepts=shared)
        result = self._api(
            "documents.create",
            {
                "collectionId": settings.outline_collection_id,
                "title": title,
                "text": md,
                "publish": True,
            },
        )
        if not result.get("ok"):
            raise RuntimeError(f"Outline create rejected: {json.dumps(result, ensure_ascii=False)[:200]}")
        url = urljoin(settings.outline_url.rstrip("/") + "/", result["data"]["url"].lstrip("/"))
        tags = getattr(analysis, "tags", None) or getattr(content, "tags", None) or []
        self._append_index_row(content, analysis, url)
        # ثبت doc_id برای لینک /view در کاغذ (best-effort)
        real_doc_id = result["data"].get("id", "")
        try:
            from src.render import registry as _registry

            _registry.register(content.url or "", real_doc_id, title)
        except Exception:  # noqa: BLE001
            logger.debug("kaghaz registry register skipped", exc_info=True)
        # LLM-Wiki 1b: ثبت نهایی مفاهیم با doc_id واقعی — ویکی‌لینک‌ها از قبل در md هستند
        try:
            from . import concepts as _concepts

            _concepts.register_doc_concepts(
                doc_id=_concepts._resolve_doc_key(content.url or "", real_doc_id),
                concepts=list(getattr(analysis, "concepts", []) or []),
            )
        except Exception:  # noqa: BLE001
            logger.debug("concept final register skipped", exc_info=True)
        return url
