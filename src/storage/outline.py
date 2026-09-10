"""Outline (self-hosted) storage for the Knowledge Agent.

Pure REST backend path (API key auth) — no browser needed. Creates a
published doc (markdown) in the KA collection and returns its public URL.
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urljoin

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
    def _markdown(content: ExtractedContent, analysis: AIAnalysis) -> str:
        title = analysis.title or content.title or "بدون عنوان"
        parts = [f"# {title}", ""]
        summary = getattr(analysis, "summary_fa", "") or analysis.summary or ""
        if summary:
            parts += [summary, ""]
        points = getattr(analysis, "key_points", None) or []
        if points:
            parts.append("## نکات کلیدی")
            for p in points:
                parts.append(f"- {p}")
            parts.append("")
        if content.url:
            parts += [f"**منبع:** [لینک اصلی]({content.url})", ""]
        tags = getattr(analysis, "tags", None) or getattr(content, "tags", None) or []
        if tags:
            parts.append("**برچسبها:** " + "، ".join(f"`{t}`" for t in tags))
        return "\n".join(parts)

    # -- index table -----------------------------------------------------
    def _append_index_row(self, title: str, category: str, url: str, tags: list[str]) -> None:
        """Best-effort: add a row to the Knowledge Index table doc."""
        doc_id = getattr(settings, "outline_index_doc_id", "") or ""
        if not doc_id:
            return
        try:
            info = self._api("documents.info", {"id": doc_id})
            text = (info.get("data") or {}).get("text") or ""
            if "| تاریخ |" not in text:
                return
            import datetime

            row = "| {} | [{}]({}) | {} | [لینک]({}) | {} |".format(
                datetime.date.today().isoformat(),
                title.replace("|", "،"),
                url,
                category or "-",
                url,
                "، ".join(tags) if tags else "-",
            )
            lines = text.split("\n")
            out = []
            inserted = False
            for ln in lines:
                out.append(ln)
                if not inserted and ln.strip().startswith("|-------"):
                    out.append(row)
                    inserted = True
            if inserted:
                text2 = "\n".join([l for l in out if "هنوز سندی" not in l])
                self._api("documents.update", {"id": doc_id, "text": text2})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Outline index row append failed (non-fatal): {e}")

    # -- public ----------------------------------------------------------
    def save(self, content: ExtractedContent, analysis: AIAnalysis) -> str:
        """Create a published Outline doc; returns its public URL."""
        if not settings.outline_enabled:
            raise RuntimeError("Outline not enabled")
        title = analysis.title or content.title or "بدون عنوان"
        md = self._markdown(content, analysis)
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
        self._append_index_row(title, getattr(analysis, "category", "") or "", url, tags)
        return url
