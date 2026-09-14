"""Kaghaz JSON API — فاز U1 (ستون فقرات API).

منبع دادهٔ فهرست/جزئیات/درخت/آمار: جدول content در knowledge.db (هستهٔ واقعی)
به‌علاوهٔ registry (نقشهٔ url→doc_id اووت‌لاین برای view_url).

قواعد:
- فقط خواندن از دیتابیس؛ هیچ endpoint مستقیم به Outline نمی‌زند (ریسک rate-limit).
- دیتابیس دست‌نخورده؛ خروجی صرفاً JSON.
- doc_id خالی یعنی سند هنوز رجیستری نشده → view_url خالی (فرانت لینک نمی‌دهد).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request

from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_KB_PATH = Path(settings.data_dir) / "knowledge.db"
_REG_PATH = Path(settings.data_dir) / "outline_docs.sqlite3"

_WPM = 200  # تخمین مطالعهٔ طرح: کلمات / ۲۰۰

_STATUS_RE = re.compile(r"^(Unread|Read|Starred|Archived)$", re.IGNORECASE)


def _norm_url(u: str) -> tuple[str, str]:
    """کلید جوینِ نرم: (host، path بدون / انتهایی و بدون query) — lnkd.in و utm ها را می‌بخشد."""
    try:
        p = urlparse((u or "").strip())
        return (p.netloc.lower(), p.path.rstrip("/"))
    except Exception:
        return ("", u or "")


def _con(path: _KB_PATH | _REG_PATH) -> sqlite3.Connection:  # type: ignore[valid-type]
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _registry_doc_ids() -> dict[tuple[str, str], str]:
    """{کلید نرمال URL منبع: doc_id اووت‌لاین}."""
    try:
        with _con(_REG_PATH) as con:
            rows = con.execute("SELECT source_url, doc_id FROM outline_docs").fetchall()
    except sqlite3.Error:
        return {}
    return {_norm_url(r["source_url"]): r["doc_id"] for r in rows}


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(t).strip() for t in val if str(t).strip()] if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _fix_persian(text: str) -> str:
    """همان پاکسازی مشترک: حروف عربی → فارسی (بدون دست‌زدن به محتوا در DB)."""
    if not text:
        return text
    return text.replace("ي", "ی").replace("ك", "ک").replace("ﻻ", "لا")


def _doc_row(r: sqlite3.Row, doc_ids: dict[tuple[str, str], str]) -> dict[str, Any]:
    url = r["url"] or ""
    doc_id = doc_ids.get(_norm_url(url), "")
    words = len((r["full_text"] or "").split())
    return {
        "doc_id": doc_id,
        "view_url": f"/view/{doc_id}" if doc_id else "",
        "title": _fix_persian(r["title"] or url[:80]),
        "date": (r["saved_at"] or "")[:10],
        "saved_at": r["saved_at"] or "",
        "category": r["category"] or "",
        "tags": _parse_tags(r["tags"]),
        "platform": r["platform"] or "",
        "status": r["status"] or "Unread",
        "author": r["author"] or "",
        "summary_fa": _fix_persian(r["summary_fa"] or ""),
        "source_url": url,
        "source_host": urlparse(url).netloc if url.startswith("http") else "",
        "reading_min": max(1, round(words / _WPM)) if words else 0,
    }


_SORTS = {
    "newest": "saved_at DESC",
    "oldest": "saved_at ASC",
    "title": "title COLLATE NOCASE ASC",
}

# ستون‌های مجازِ status (مطابق طرح؛ فعلاً همه Unread هستند ولی فیلتر آینده‌نگر است)


@router.get("/docs")
async def api_docs(
    q: str = "",
    tag: str = "",
    category: str = "",
    platform: str = "",
    status: str = "",
    sort: str = "newest",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """U1a — فهرست اسناد با فیلتر/مرتب‌سازی/صفحه‌بندی."""
    where: list[str] = []
    params: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        where.append("(title LIKE ? OR summary_fa LIKE ? OR full_text LIKE ?)")
        params += [like, like, like]
    if tag:
        where.append("tags LIKE ?")
        params.append(f'%"{tag.strip()}"%')
    if category:
        where.append("category = ?")
        params.append(category.strip())
    if platform:
        where.append("platform = ?")
        params.append(platform.strip())
    if status:
        if not _STATUS_RE.match(status):
            raise HTTPException(400, "status must be Unread|Read|Starred|Archived")
        where.append("status = ?")
        params.append(status.capitalize())
    order = _SORTS.get(sort, _SORTS["newest"])

    with _con(_KB_PATH) as con:
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        total = con.execute(f"SELECT COUNT(*) FROM content{where_sql}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT url, title, platform, category, tags, summary_fa, author, status, saved_at, full_text"
            f" FROM content{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, (page - 1) * limit],
        ).fetchall()

    doc_ids = _registry_doc_ids()
    items = [_doc_row(r, doc_ids) for r in rows]
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }


@router.get("/docs/{doc_id}")
async def api_doc_detail(doc_id: str) -> dict[str, Any]:
    """U1b — جزئیات سند: markdown کامل + متادیتا (هدر طرح: برچسب/آخرین ویرایش/زمان مطالعه)."""
    # doc_id از registry: اول نقشهٔ معکوس doc_id→url، بعد ردیف content
    with _con(_REG_PATH) as reg_con:
        row = reg_con.execute(
            "SELECT source_url, title FROM outline_docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown doc_id")

    with _con(_KB_PATH) as con:
        r = con.execute(
            "SELECT url, title, platform, category, tags, summary_fa, author, status,"
            " saved_at, full_text, key_points, metadata"
            " FROM content WHERE url = ? OR url = ?",
            (row["source_url"], row["source_url"].rstrip("/")),
        ).fetchone()
    if not r:
        # سند در Outline هست ولی ردیف content ندارد (۱۶ ردیف قدیمی) — از خود ردیف registry تغذیه می‌شود
        raise HTTPException(404, "Doc has no content row in knowledge.db")

    words = len((r["full_text"] or "").split())
    doc_ids = _registry_doc_ids()
    base = _doc_row(r, doc_ids)
    kp = []
    try:
        kp = [str(x) for x in json.loads(r["key_points"] or "[]")]
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        meta = json.loads(r["metadata"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return {
        **base,
        "key_points": kp,
        "extract_method": meta.get("method", ""),
        "outline_doc_id": doc_id,
        "markdown": r["full_text"] or "",
        "word_count": words,
        "created_at": (r["saved_at"] or "")[:19].replace("T", " "),
        "updated_at": (r["saved_at"] or "")[:19].replace("T", " "),
    }


@router.get("/tree")
async def api_tree() -> dict[str, Any]:
    """U1c — درخت اسناد: سطح ۱ category، سطح ۲ اسناد (گروه‌بندی خودکار)."""
    with _con(_KB_PATH) as con:
        rows = con.execute(
            "SELECT url, title, category, saved_at FROM content ORDER BY saved_at DESC"
        ).fetchall()
    doc_ids = _registry_doc_ids()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        cat = (r["category"] or "بدون دسته").strip()
        doc_id = doc_ids.get(_norm_url(r["url"]), "")
        groups.setdefault(cat, []).append(
            {
                "doc_id": doc_id,
                "view_url": f"/view/{doc_id}" if doc_id else "",
                "title": _fix_persian(r["title"] or ""),
                "date": (r["saved_at"] or "")[:10],
            }
        )
    tree = [
        {"category": cat, "count": len(docs), "docs": docs}
        for cat, docs in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]
    return {"tree": tree, "total_docs": len(rows), "total_categories": len(tree)}


@router.get("/stats")
async def api_stats() -> dict[str, Any]:
    """U1d — کارت‌های KPI: کوئری سبک، بدون LLM."""
    with _con(_KB_PATH) as con:
        total = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        last7 = con.execute(
            "SELECT COUNT(*) FROM content WHERE saved_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        by_platform = dict(
            con.execute(
                "SELECT COALESCE(platform,'unknown'), COUNT(*) FROM content GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        )
        by_category = dict(
            con.execute(
                "SELECT COALESCE(NULLIF(category,''),'بدون دسته'), COUNT(*) FROM content GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        )
        tag_rows = con.execute("SELECT tags FROM content WHERE tags IS NOT NULL AND tags != ''").fetchall()
    counter: Counter[str] = Counter()
    for r in tag_rows:
        counter.update(_parse_tags(r["tags"]))
    with _con(_REG_PATH) as reg_con:
        viewable = reg_con.execute("SELECT COUNT(*) FROM outline_docs").fetchone()[0]
    return {
        "total_docs": total,
        "docs_last_7d": last7,
        "viewable_docs": viewable,
        "by_platform": by_platform,
        "by_category": by_category,
        "top_tags": [{"tag": t, "count": n} for t, n in counter.most_common(15)],
    }


# ---------------- U2 — جست‌وجو ⌘K و وضعیت‌ها ----------------

_FTS_COLUMNS = ("url", "title", "full_text")


def _fts_safe_query(q: str) -> str:
    """پرس‌وجوی خام کاربر → FTS5 MATCH امن.

    توکن‌ها با OR + پیشوند (*) ترکیب می‌شوند (فراخوانی بهتر برای فارسیِ چسبیده
    مثل «مدلdeepseek»)؛ عبارت کامل هم به‌صورت phrase اضافه می‌شود تا اسنادِ
    دارندهٔ دقیق عبارت، بالاتر از bm25 بیایند.
    """
    q = (q or "").strip()
    if not q:
        return ""
    # کاراکترهای خاص FTS (" را نمی‌شود داخل "..." آورد) → توکن‌ها را جدا و کوتو می‌کنیم
    tokens = [m[0] or m[1] for m in re.findall(r"\"([^\"]*)\"|(\S+)", q.replace('"', ""))]
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    parts = [f'"{ " ".join(tokens) }"'] if len(tokens) > 1 else []
    parts += [f'"{t}"*' for t in tokens]
    return " OR ".join(dict.fromkeys(parts))


@router.get("/search")
async def api_search(q: str = "", limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    """U2a — جست‌وجوی ⌘K: FTS5 + اسنیپت هایلایت؛ کوتاه‌تر از ۳ حرف → LIKE.

    مفاهیم منطبق از جدول concepts (LLM-Wiki 1b) هم برمی‌گردد.
    """
    q = (q or "").strip()
    if not q:
        return {"query": "", "items": [], "concepts": [], "total": 0, "mode": "empty"}

    # نرمال‌سازی ی/ک عربی → فارسی (متن ذخیره‌شده فارسی استاندارد دارد)
    q_fa = _fix_persian(q)

    con = _con(_KB_PATH)
    try:
        mode = "fts"
        fts_q = _fts_safe_query(q_fa)
        rows: list = []
        if len(q_fa) >= 3 and fts_q:
            try:
                marks = "«", "»"
                rows = con.execute(
                    "SELECT c.id, c.url, c.title, c.platform, c.category, c.tags, c.summary_fa,"
                    " c.status, c.saved_at, c.author, c.full_text AS full_text,"
                    f" snippet(content_fts, 2, '{marks[0]}', '{marks[1]}', '…', 16) AS snippet,"
                    f" highlight(content_fts, 1, '{marks[0]}', '{marks[1]}') AS title_hl,"
                    " bm25(content_fts) AS rank"
                    " FROM content_fts JOIN content c ON c.id = content_fts.rowid"
                    " WHERE content_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []  # سینتکس بد → fallback
        if not rows:
            mode = "like"
            like = f"%{q_fa}%"
            rows = con.execute(
                "SELECT id, url, title, platform, category, tags, summary_fa, status, saved_at,"
                " author, full_text, '' AS snippet, title AS title_hl, 0 AS rank"
                " FROM content WHERE title LIKE ? OR summary_fa LIKE ? OR full_text LIKE ?"
                " ORDER BY saved_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()

        doc_ids = _registry_doc_ids()
        items = []
        for r in rows:
            d = _doc_row(r, doc_ids)
            d["snippet"] = _fix_persian(r["snippet"] or "")
            d["title_hl"] = _fix_persian(r["title_hl"] or d["title"])
            d["rank"] = r["rank"]
            items.append(d)

        # مفاهیم منطبق: norm/display حاوی عبارت (جدول concepts از LLM-Wiki 1b)
        concepts = []
        like_c = f"%{q_fa.replace(' ', '')}%"  # norm بدون فاصله (ZWNJ join)
        try:
            crows = con.execute(
                "SELECT norm, display, doc_count, doc_ids FROM concepts"
                " WHERE display LIKE ? OR norm LIKE ? ORDER BY doc_count DESC LIMIT 5",
                (f"%{q_fa}%", like_c),
            ).fetchall()
            for cr in crows:
                ids = [i.strip() for i in (cr["doc_ids"] or "").split(",") if i.strip()]
                concepts.append(
                    {
                        "norm": cr["norm"],
                        "display": _fix_persian(cr["display"]),
                        "doc_count": cr["doc_count"],
                        "doc_ids": ids,
                    }
                )
        except sqlite3.Error:
            pass

        return {"query": q, "mode": mode, "total": len(items), "items": items, "concepts": concepts}
    finally:
        con.close()


def _content_url_for(doc_id: str) -> str:
    """doc_id اووت‌لاین → URL منبع در registry (نرمال‌نشده)."""
    with _con(_REG_PATH) as reg_con:
        row = reg_con.execute(
            "SELECT source_url FROM outline_docs WHERE doc_id = ?", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown doc_id")
    return row["source_url"]


@router.patch("/docs/{doc_id}/status")
async def api_set_status(doc_id: str, request: Request) -> dict[str, Any]:
    """U2b — وضعیت سند: Unread | Read | Starred | Archived (فقط SQLite؛ Outline دست نمی‌خورد)."""
    body = await request.json()
    status = str(body.get("status", "")).strip()
    if not _STATUS_RE.match(status):
        raise HTTPException(400, "status must be Unread|Read|Starred|Archived")
    status = status.capitalize()

    source_url = _content_url_for(doc_id)
    now = datetime.now(timezone.utc).isoformat()
    with _con(_KB_PATH) as con:
        cur = con.execute(
            "UPDATE content SET status = ?,"
            " starred_at = CASE WHEN ? = 'Starred' THEN ? ELSE NULL END"
            " WHERE url IN (?, ?)",
            (status, status, now, source_url, source_url.rstrip("/")),
        )
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Doc has no content row in knowledge.db")
    return {"doc_id": doc_id, "status": status, "starred_at": now if status == "Starred" else ""}


@router.post("/docs/{doc_id}/tags")
async def api_set_tags(doc_id: str, request: Request) -> dict[str, Any]:
    """U2c — افزودن/حذف برچسب: {add: [...]} یا {remove: [...]} یا {tags: [...]} (جایگزینی کامل).

    فقط ستون tags در content؛ Outline دست نمی‌خورد.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")
    add = [str(t).strip() for t in body.get("add", []) if str(t).strip()]
    remove = [str(t).strip() for t in body.get("remove", []) if str(t).strip()]
    replace = body.get("tags") if isinstance(body.get("tags"), list) else None

    source_url = _content_url_for(doc_id)
    with _con(_KB_PATH) as con:
        row = con.execute(
            "SELECT tags FROM content WHERE url IN (?, ?)",
            (source_url, source_url.rstrip("/")),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Doc has no content row in knowledge.db")
        current = _parse_tags(row["tags"])
        if replace is not None:
            tags = [str(t).strip() for t in replace if str(t).strip()]
        else:
            tags = [t for t in current if t not in remove]
            for t in add:
                if t not in tags:
                    tags.append(t)
        con.execute(
            "UPDATE content SET tags = ? WHERE url IN (?, ?)",
            (json.dumps(tags, ensure_ascii=False), source_url, source_url.rstrip("/")),
        )
        con.commit()
    return {"doc_id": doc_id, "tags": tags}


@router.get("/concepts")
async def api_concepts(min_docs: int = Query(1, ge=1, le=50)) -> dict[str, Any]:
    """U3a — فهرست مفاهیم مشترک (doc_count >= min_docs) + اسناد حامل."""
    with _con(_KB_PATH) as con:
        rows = con.execute(
            "SELECT norm, display, doc_count, doc_ids FROM concepts WHERE doc_count >= ?"
            " ORDER BY doc_count DESC",
            (min_docs,),
        ).fetchall()
        id_url = dict(con.execute("SELECT id, url FROM content").fetchall())
    doc_ids = _registry_doc_ids()
    out = []
    for r in rows:
        docs = []
        for i in [x.strip() for x in (r["doc_ids"] or "").split(",") if x.strip()]:
            if not i.isdigit():
                continue
            url = id_url.get(int(i), "")
            if not url:
                continue
            did = doc_ids.get(_norm_url(url), "")
            docs.append({"doc_id": did, "view_url": f"/view/{did}" if did else "", "url": url})
        out.append(
            {
                "norm": r["norm"],
                "display": _fix_persian(r["display"]),
                "doc_count": r["doc_count"],
                "docs": docs,
            }
        )
    return {"concepts": out, "total": len(out)}
