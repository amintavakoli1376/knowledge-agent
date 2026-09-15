"""Kaghaz view routes: /index (جدول شاخص) و /view/{doc_id} (سند).

متادیتای هر سند (دسته/وضعیت/پلتفرم/برچسب‌ها) از همان جدول شاخص
Outline خوانده می‌شود — جوین روی URL منبع؛ بدون call اضافه به Notion.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import settings
from src.render import registry
from src.render.parser import parse
from src.render.renderer import (
    COPY_JS,
    category_badge,
    render_blocks,
    render_properties,
    status_badge,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent))

_emoji_re = re.compile(r"^([\U0001F300-\U0001FAFF\u2600-\u27BF])\s*")
_link_re = re.compile(r"^\\?\[([^\]]*)\]\(([^)]+)\)")  # \\[ لینک escape‌شده هم می‌گیرد (PDFها)
_source_re = re.compile(r"لینک اصلی\]\((https?://[^)]+)\)")


def _parse_link(md: str) -> tuple[str, str]:
    s = (md or "").strip()
    m = _link_re.match(s)
    if m:
        return m.group(1), m.group(2)
    return s, ""


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


_slug_cache: dict = {"at": 0.0, "map": {}}


def _slug_doc_ids() -> dict[str, str]:
    """{slug لینک اووت‌لاین: doc_id} — برای ردیف‌هایی که فقط لینک «📄 سند» دارند (PDF ها).

    یک call به documents.list (کش ۵ دقیقه‌ای) — مقاوم به خطا.
    """
    import time as _t

    if _t.time() - _slug_cache["at"] > 300:
        try:
            res = registry._api("documents.list", {"limit": 100, "sort": "updatedAt"})
            docs = res.get("data") if isinstance(res.get("data"), list) else (res.get("data") or {}).get("documents") or []
            m = {}
            for d in docs:
                u = d.get("url") or ""
                m[u.rstrip("/").split("/")[-1]] = d.get("id") or ""
            _slug_cache["at"] = _t.time()
            _slug_cache["map"] = m
        except Exception as e:  # noqa: BLE001 — نمایش شاخص نباید به Outline وابسته بماند
            logger.warning(f"slug map fetch failed: {e}")
    return _slug_cache["map"]


def _index_meta_by_url() -> tuple[list[str], dict[str, list[str]]]:
    """جدول شاخص Outline → {url منبع: [۱۱ ستون]} (با کش در registry)."""
    def _fetch() -> tuple[list[str], dict[str, list[str]]]:
        res = registry._api("documents.info", {"id": settings.outline_index_doc_id})
        blocks = parse((res.get("data") or {}).get("text", ""))
        table = next((b for b in blocks if b.type == "table"), None)
        if not table:
            return [], {}
        meta: dict[str, list[str]] = {}
        for raw in table.data["rows"]:
            cells = [c.strip() for c in raw]
            cells += [""] * (11 - len(cells))
            _t, url = _parse_link(cells[7])
            if url:
                meta[url.rstrip("/")] = cells
        return table.data["header"], meta

    try:
        return registry._cached("index_meta", 60.0, _fetch)
    except Exception:  # pragma: no cover
        logger.warning("index meta fetch failed", exc_info=True)
        return [], {}


def _cell_row(meta_row: list[str]) -> dict:
    """ردیف شاخص (۱۰ یا ۱۱ ستون) → دیکشنری متادیتای سند.

    فرمت فعلی ۱۰ ستونی است (Summary EN حذف شده)؛ «سند» ستون آخر است.
    فرمت قدیمی ۱۱ ستونی هم پذیرفته می‌شود.
    """
    row = list(meta_row)
    if len(row) < 10:
        row += [""] * (10 - len(row))
    doc_link = row[9] if len(meta_row) == 10 else (row[10] if len(row) > 10 else "")
    title, source_url = _parse_link(row[1])
    _tt, _dl = _parse_link(doc_link)
    return {
        "date": row[0][:10],
        "title": title,
        "category": row[2],
        "platform": row[3],
        "status": row[4],
        "author": row[5] if row[5] != "\\-" else "",
        "tags": [t.strip() for t in row[6].split("،") if t.strip() and t.strip() != "…"],
        "source_url": source_url,
        "doc_url": doc_link,
    }


@router.get("/", response_class=HTMLResponse)
async def kaghaz_root():
    return RedirectResponse("/index")


@router.get("/index", response_class=HTMLResponse)
async def kaghaz_index(request: Request):
    docs = registry.list_collection_docs(limit=200)
    index_doc = next((d for d in docs if "شاخص" in d.get("title", "")), None)
    if not index_doc:
        raise HTTPException(503, "Index document unavailable")

    reg = {u.rstrip("/"): did for u, did, _t in registry.all_docs()}
    slug_map = _slug_doc_ids()  # PDF ها: از لینک «📄 سند» → doc_id
    blocks = parse(index_doc.get("text", ""))
    table = next((b for b in blocks if b.type == "table"), None)
    headers: list[str] = []
    rows: list[dict] = []
    if table:
        ncols = len(table.data["header"])
        # فرمت فعلی ۱۰ ستونی (بدون Summary EN). «سند» (آخر) نمایش داده نمیشه —
        # اسم مقاله خودش لینک /view داره. فرمت قدیمی ۱۱ ستونی هم پشتیبانی می‌شود.
        last = 9 if ncols == 10 else 10          # ایندکس ستون «سند» (۰-based در cells خام)
        headers = table.data["header"][:-1]
        for raw in table.data["rows"]:
            cells = [c.strip() for c in raw]
            cells += [""] * ((last + 1) - len(cells))
            title, _title_link = _parse_link(cells[1])
            # U4-fix: لینک‌های escape‌شده (\\[file.pdf](file://…)) برای PDF ها هم استخراج می‌شوند؛
            # doc_id اول از URL منبع، بعد از لینک «📄 سند» (چون file:// در رجیستری هست ولی
            # برای اطمینان هر دو مسیر چک می‌شوند)
            src_from_url_col, src_doc_col = _parse_link(cells[7])[1], _parse_link(cells[last])[1]
            source_url = next(
                (u for u in (src_from_url_col, src_doc_col) if u and "176.100.37.230" not in u),
                "",
            )
            _, doc_link = _parse_link(cells[last])
            doc_id = reg.get((source_url or "").rstrip("/"), "")
            if not doc_id and doc_link:
                # PDF: لینک سندِ اووت‌لاین → doc_id مستقیم از خود لینک
                m_doc = re.search(r"/doc/([A-Za-z0-9-]+)", doc_link)
                if m_doc:
                    doc_id = slug_map.get(m_doc.group(1), "")
            m = _cell_row(cells)
            rows.append(
                {
                    "date": cells[0][:10],
                    "title": title or cells[1][:60],
                    "category": category_badge(m["category"]),
                    "platform": m["platform"] or "—",
                    "status": status_badge(m["status"]),
                    "author": m["author"] or "—",
                    "tags": cells[6],
                    "source_url": source_url,
                    "source_host": _host(source_url) if source_url else "—",
                    "summary_fa": cells[8],
                    "summary_en": "",
                    "doc_url": doc_link,
                    "view_url": f"/view/{doc_id}" if doc_id else "",
                }
            )

    tree = [
        {"id": d.get("id"), "title": d.get("title", "")}
        for d in docs[:40]
        if "شاخص" not in d.get("title", "")
    ]
    return templates.TemplateResponse(
        request,
        "kaghaz_index.html",
        {
            "title": "شاخص دانش",
            "crumb": "شاخص دانش",
            "index_headers": headers,
            "index_rows": rows,
            "tree_docs": tree,
            "active": "index",
            "active_doc": "",
            "properties": "",
            "outline_edit_url": settings.outline_url.rstrip("/") + index_doc.get("url", ""),
            "copy_js": COPY_JS,
        },
    )


@router.get("/view/{doc_id}", response_class=HTMLResponse)
async def kaghaz_view(request: Request, doc_id: str):
    doc = registry.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    text = doc.get("text", "")
    title = doc.get("title") or "بدون عنوان"
    title_clean = _emoji_re.sub("", title).strip() or title
    emoji_m = _emoji_re.match(title.strip())
    emoji = emoji_m.group(1) if emoji_m else "📄"

    # URL منبع و متادیتا از جدول شاخص (join با URL)
    source_url = ""
    m = _source_re.search(text)
    if m:
        source_url = m.group(1)
    meta_render: dict = {}
    if source_url:
        _, idx = _index_meta_by_url()
        row = idx.get(source_url.rstrip("/"))
        if row:
            mr = _cell_row(row)
            meta_render = {
                "date": mr["date"],
                "category": mr["category"],
                "platform": mr["platform"],
                "status": mr["status"],
                "author": mr["author"],
                "tags": mr["tags"],
                "source_url": mr["source_url"],
                "source_host": _host(mr["source_url"]) if mr["source_url"] else "",
            }

    tree_docs = registry.list_collection_docs(limit=40)
    tree = [
        {"id": d.get("id"), "title": d.get("title", "")}
        for d in tree_docs
        if "شاخص" not in d.get("title", "")
    ]
    return templates.TemplateResponse(
        request,
        "kaghaz_view.html",
        {
            "title": title_clean,
            "crumb": title_clean[:40],
            "doc_emoji": emoji,
            "properties": render_properties(meta_render),
            "body": render_blocks(parse(text)),
            "tree_docs": tree,
            "active": "",
            "active_doc": doc_id,
            "outline_edit_url": settings.outline_url.rstrip("/") + doc.get("url", ""),
            "copy_js": COPY_JS,
        },
    )
