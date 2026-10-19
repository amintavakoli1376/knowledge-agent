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
from src.render.api_views import (
    _KB_PATH,
    _REG_PATH,
    _con,
    _doc_row,
    _registry_doc_ids,
    _SORTS as _API_SORTS,
    _STATUS_RE as _API_STATUS_RE,
)
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


_slug_cache: dict = {"at": 0.0, "map": {}, "title_map": {}}


def _slug_doc_ids() -> dict[str, str]:
    """{slug لینک اووت‌لاین: doc_id} — برای ردیف‌هایی که فقط لینک «📄 سند» دارند (PDF ها).

    یک call به documents.list (کش ۵ دقیقه‌ای) — مقاوم به خطا.
    """
    import time as _t

    if settings.outline_enabled and _t.time() - _slug_cache["at"] > 300:
        try:
            res = registry._api("documents.list", {"limit": 100, "sort": "updatedAt"})
            docs = res.get("data") if isinstance(res.get("data"), list) else (res.get("data") or {}).get("documents") or []
            m = {}
            tmap: dict[str, str] = {}
            for d in docs:
                u = d.get("url") or ""
                m[u.rstrip("/").split("/")[-1]] = d.get("id") or ""
                tmap[(d.get("title") or "").strip()] = d.get("id") or ""
            _slug_cache["at"] = _t.time()
            _slug_cache["map"] = m
            _slug_cache["title_map"] = tmap
        except Exception as e:  # noqa: BLE001 — نمایش شاخص نباید به Outline وابسته بماند
            logger.warning(f"slug map fetch failed: {e}")
    return _slug_cache["map"]


def _title_doc_ids() -> dict[str, str]:
    """{عنوان سند اووت‌لاین: doc_id} — fallback برای اسناد قدیمیِ بدون رجیستری."""
    _slug_doc_ids()  # تضمین کش
    return _slug_cache["title_map"]


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


def _sparkline_data() -> list[int]:
    """روند ۷ روزهٔ ذخیرهٔ اسناد (برای sparkline کارت KPI) — یک کوئری گروه‌بندی، بدون LLM."""
    try:
        with _con(_KB_PATH) as kb:
            rows = kb.execute(
                "SELECT date(saved_at), COUNT(*) FROM content"
                " WHERE saved_at >= datetime('now', '-7 days') GROUP BY date(saved_at)"
            ).fetchall()
        by_day = {r[0]: r[1] for r in rows}
        from datetime import date, timedelta as _td

        today = date.today()
        return [by_day.get((today - _td(days=6 - i)).isoformat(), 0) for i in range(7)]
    except Exception:  # noqa: BLE001 — کارت KPI نباید به خطا حساس باشد
        return [0] * 7


@router.get("/index", response_class=HTMLResponse)
async def kaghaz_index(request: Request, q: str = "", status: str = "", category: str = "", platform: str = "", sort: str = "newest"):
    docs = registry.list_collection_docs(limit=200) if settings.outline_enabled else []
    index_doc = next((d for d in docs if "شاخص" in d.get("title", "")), None)
    # بدون Outline (نصب تازه/آفلاین) صفحهٔ شاخص باید از خودِ knowledge.db رندر شود،
    # نه ۵۰۳ بعد از ۳۰ ثانیه تایم‌اوت.

    # U-C1/C2: جدول شاخص از content (همان منبع /api/docs) + فیلترهای سرور-رندر
    reg = {u.rstrip("/"): did for u, did, _t in registry.all_docs()}
    slug_map = _slug_doc_ids()  # PDF ها: از لینک «📄 سند» → doc_id
    title_map = _title_doc_ids()  # اسناد قدیمی بدون رجیستری → با عنوان
    where: list[str] = []
    params: list = []
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(title LIKE ? OR summary_fa LIKE ? OR full_text LIKE ?)")
        params += [like, like, like]
    if status and _API_STATUS_RE.match(status):
        where.append("status = ?")
        params.append(status.capitalize())
    if category.strip():
        where.append("category = ?")
        params.append(category.strip())
    if platform.strip():
        where.append("platform = ?")
        params.append(platform.strip())
    order = _API_SORTS.get(sort, _API_SORTS["newest"])
    with _con(_KB_PATH) as kb:
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        crows = kb.execute(
            f"SELECT id, url, title, platform, category, tags, summary_fa, author, status,"
            f" saved_at, full_text FROM content{where_sql} ORDER BY {order}",
            params,
        ).fetchall()
    doc_ids = _registry_doc_ids()
    rows = []
    for r in crows:
        item = _doc_row(r, doc_ids)
        if not item["view_url"]:
            # fallback: عنوان کامل سند در Outline (اسناد قدیمیِ بدون رجیستری)
            did_t = title_map.get((r["title"] or "").strip(), "")
            if did_t:
                item["view_url"] = f"/view/{did_t}"
        if not item["view_url"] and r["id"] is not None:
            # بدون Outline هم سند باید باز شود — صفحهٔ سند از خودِ knowledge.db
            item["view_url"] = f"/doc/{r['id']}"
        if item["source_url"].startswith(("file://", "telegram-file://")):
            item["source_host"] = "PDF"
        rows.append(
            {
                "date": item["date"],
                "title": item["title"],
                "category": category_badge(item["category"]),
                "platform": item["platform"] or "—",
                "status": status_badge(item["status"]),
                "author": item["author"] or "—",
                "tags": "، ".join(item["tags"]),
                "source_url": item["source_url"],
                "source_host": item["source_host"] or "—",
                "summary_fa": item["summary_fa"],
                "summary_en": "",
                "doc_url": "",
                "view_url": item["view_url"],
            }
        )
    headers = ["تاریخ", "تیتر", "دسته", "پلتفرم", "وضعیت", "نویسنده", "برچسب‌ها", "URL"]

    # R2: کارت‌های KPI + sparkline (از همان کوئری‌های content — سبک)
    with _con(_KB_PATH) as kb:
        total = kb.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        last7 = kb.execute(
            "SELECT COUNT(*) FROM content WHERE saved_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        starred = kb.execute("SELECT COUNT(*) FROM content WHERE status = 'Starred'").fetchone()[0]
    trend = _sparkline_data()
    max_t = max(trend) or 1
    pts = " ".join(f"{i * (100 / 6):.0f},{18 - (v / max_t) * 14:.0f}" for i, v in enumerate(trend))
    kpis = [
        {"label": "کل اسناد", "value": str(total), "sub": f"+{last7} در ۷ روز", "spark": True},
        {"label": "ستاره‌دار", "value": str(starred), "sub": "بخوانی‌شدهٔ منتخب", "spark": False},
        {"label": "ذخیرهٔ ۷ روز", "value": str(last7), "sub": "میانگین روزانه ~" + str(round(last7 / 7, 1)), "spark": False},
    ]

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
            "kpis": kpis,
            "spark_pts": pts,
            "outline_edit_url": (settings.outline_url.rstrip("/") + (index_doc or {}).get("url", "")) if index_doc else "",
            "copy_js": COPY_JS,
        },
    )


@router.get("/concept/{norm}", response_class=HTMLResponse)
async def kaghaz_concept(request: Request, norm: str):
    """2a — صفحهٔ مفهومی خودکار: همهٔ اسناد حامل مفهوم + مفاهیم هم‌جوار (بدون LLM)."""
    import urllib.parse as _up

    norm_dec = _up.unquote(norm)
    data = None
    try:
        # استفاده از همان منطق API (resolve ZWNJ + اسناد + هم‌جوار)
        from src.render.api_views import api_concept_page  # noqa: F401 — فقط برای اطمینان از import

        data = await api_concept_page(norm_dec)
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(404, "Unknown concept")
        raise
    return templates.TemplateResponse(
        request,
        "kaghaz_concept.html",
        {
            "title": data["display"],
            "crumb": data["display"][:40],
            "doc_count": data["doc_count"],
            "docs": data["docs"],
            "related": data.get("related_concepts", []),
            "tree_docs": [],
            "active": "",
            "active_doc": "",
        },
    )


@router.get("/doc/{content_id}", response_class=HTMLResponse)
async def kaghaz_doc(request: Request, content_id: str):
    """صفحهٔ سند از SQLite — مستقل از Outline.

    چرا: در نصب‌های بدون Outline، `/view/{doc_id}` سند ندارد و تیترها در جدول
    شاخص لینک نمی‌شدند. این مسیر همان محتوای ذخیره‌شده (خلاصه/برچسب/متن) را
    نشان می‌دهد و شناسه‌اش `content.id` است.
    """
    import html as _html
    import json as _json

    with _con(_KB_PATH) as kb:
        r = kb.execute(
            "SELECT id, url, title, platform, category, priority, tags, summary_fa,"
            " summary_en, key_points, author, status, saved_at, full_text FROM content WHERE id = ?",
            (content_id,),
        ).fetchone()
        tree_rows = kb.execute(
            "SELECT id, title FROM content ORDER BY saved_at DESC LIMIT 40"
        ).fetchall()
    if not r:
        raise HTTPException(404, "سند پیدا نشد")

    title = (r["title"] or "بدون عنوان").strip()

    def _lst(v) -> list[str]:
        try:
            out = _json.loads(v or "[]")
            return [str(x) for x in out] if isinstance(out, list) else []
        except Exception:  # noqa: BLE001
            return []

    body: list[str] = []
    if r["summary_fa"]:
        body.append('<div class="kg-blocks"><h2>خلاصه</h2>')
        for para in str(r["summary_fa"]).split("\n"):
            para = para.strip()
            if para:
                body.append(f"<p>{_html.escape(para)}</p>")
        body.append("</div>")
    kp = _lst(r["key_points"])
    if kp:
        body.append('<div class="kg-blocks"><h2>نکته‌های کلیدی</h2><ul>')
        body += [f"<li>{_html.escape(k)}</li>" for k in kp]
        body.append("</ul></div>")
    tags = _lst(r["tags"])
    if tags:
        body.append('<div class="kg-blocks"><h2>برچسب‌ها</h2><p>' + "، ".join(_html.escape(t) for t in tags) + "</p></div>")
    src = (r["url"] or "")
    if src:
        body.append(
            '<div class="kg-blocks"><h2>منبع</h2><p><a href="'
            + _html.escape(src)
            + '" target="_blank" rel="noopener">'
            + _html.escape(_host(src))
            + "</a></p></div>"
        )
    if r["full_text"]:
        body.append(
            '<div class="kg-blocks"><details><summary>متن کامل</summary><pre style="white-space:pre-wrap">'
            + _html.escape(str(r["full_text"]))
            + "</pre></details></div>"
        )

    meta_render = {
        "date": (r["saved_at"] or "")[:10],
        "category": r["category"] or "",
        "platform": r["platform"] or "",
        "status": r["status"] or "",
        "author": r["author"] or "",
        "tags": "، ".join(tags),
        "source_url": src,
        "source_host": _host(src) if src else "",
    }
    tree = [{"id": t["id"], "title": t["title"] or "", "href": f"/doc/{t['id']}"} for t in tree_rows]
    return templates.TemplateResponse(
        request,
        "kaghaz_view.html",
        {
            "title": title,
            "crumb": title[:40],
            "doc_emoji": "",
            "doc_id": "",
            "properties": render_properties(meta_render),
            "body": "\n".join(body),
            "tree_docs": tree,
            "active": "",
            "active_doc": "",
            "outline_edit_url": "",
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

    # URL منبع و متادیتا: اول رجیستری (doc_id→source_url)، بعد md (اسناد قدیمی با «لینک اصلی»)
    source_url = ""
    real_url = ""
    try:
        with _con(_REG_PATH) as reg_con:
            rr = reg_con.execute(
                "SELECT source_url FROM outline_docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if rr and rr["source_url"]:
                real_url = rr["source_url"]
    except Exception:  # noqa: BLE001
        pass
    m = _source_re.search(text)
    if m:
        source_url = m.group(1)
    source_url = real_url or source_url
    meta_render: dict = {}
    if source_url:
        _, idx = _index_meta_by_url()
        row = idx.get(source_url.rstrip("/"))
        if not row:
            # join با کلید نرمال‌شده (utm/انتهای اسلش) — همان الگوی _registry_doc_ids
            from urllib.parse import urlsplit as _us

            def _nk(u: str) -> str:
                try:
                    p = _us(u)
                    return (p.netloc + p.path.rstrip("/")).lower()
                except Exception:  # noqa: BLE001
                    return u

            want = _nk(source_url)
            for u, cells in idx.items():
                if _nk(u) == want:
                    row = cells
                    break
        if row:
            mr = _cell_row(row)
            # نمایشی: URL واقعی منبع (رجیستری) — نه self-link سرور؛ جدول شاخص self-link را
            # در cells[1] دارد ولی cells[7] لینک واقعی است. real_url همیشه معتبرترین است.
            display_url = real_url or mr["source_url"]
            if display_url.startswith("file://") or display_url.startswith("telegram-file://"):
                display_url = ""  # PDF محلی — لینک بیرونی ندارد
            meta_render = {
                "date": mr["date"],
                "category": mr["category"],
                "platform": mr["platform"],
                "status": mr["status"],
                "author": mr["author"],
                "tags": mr["tags"],
                "source_url": display_url,
                "source_host": _host(display_url) if display_url else "",
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
            "doc_id": doc_id,
            "properties": render_properties(meta_render),
            "body": render_blocks(parse(text)),
            "tree_docs": tree,
            "active": "",
            "active_doc": doc_id,
            "outline_edit_url": settings.outline_url.rstrip("/") + doc.get("url", ""),
            "copy_js": COPY_JS,
        },
    )
