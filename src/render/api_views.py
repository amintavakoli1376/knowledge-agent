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
from src.storage.sqlite_store import _snapshot_if_exists

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
    """{کلید نرمال URL منبع: doc_id اووت‌لاین} + کش hash(url) منفی برای concepts.doc_ids."""
    try:
        with _con(_REG_PATH) as con:
            rows = con.execute("SELECT source_url, doc_id FROM outline_docs").fetchall()
    except sqlite3.Error:
        return {}
    global _HASH_TO_URL
    import hashlib

    mapping: dict[tuple[str, str], str] = {}
    hash_map: dict[str, str] = {}
    for r in rows:
        url = r["source_url"] or ""
        mapping[_norm_url(url)] = r["doc_id"]
        h = int(hashlib.sha1(url.encode()).hexdigest()[:8], 16)
        hash_map[str(-(h % 1_000_000_000) - 1)] = url
    _HASH_TO_URL = hash_map
    return mapping


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
    if not doc_id:
        # F2 (2026-09-17): fallback عنوانی مثل /index — اسناد PDF قدیمی که رجیستری
        # جاافتاده دارند، از طریق عنوان سند Outline قابل‌کلیک می‌شوند.
        try:
            from src.render.views import _title_doc_ids  # noqa: PLC0415 — import داخل‌تابعی برای جلوگیری از cycle

            doc_id = _title_doc_ids().get((r["title"] or "").strip(), "")
        except Exception:  # noqa: BLE001
            doc_id = ""
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

_HASH_TO_URL: dict[str, str] = {}  # کلید منفی concepts.doc_ids → URL منبع

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
    """U3a — فهرست مفاهیم مشترک (doc_count >= min_docs) + اسناد حامل.

    doc_ids در جدول concepts دوگونه است: مثبت = content.id (backfill 1b)،
    منفی = hash(url) (مسیر فعلی outline.py) — هر دو resolve می‌شوند.
    """
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
            url = ""
            if i.isdigit():
                url = id_url.get(int(i), "")
            elif i.lstrip("-").isdigit():  # کلید منفی = hash(url)
                url = _HASH_TO_URL.get(i, "")
            if not url:
                continue
            did = doc_ids.get(_norm_url(url), "")
            view = f"/view/{did}" if did else ""
            if not view and i.isdigit():
                # بدون Outline هم سند باید باز شود — صفحهٔ سند از SQLite
                view = f"/doc/{i}"
            docs.append({"doc_id": did or i, "view_url": view, "url": url})
        out.append(
            {
                "norm": r["norm"],
                "display": _fix_persian(r["display"]),
                "doc_count": r["doc_count"],
                "docs": docs,
            }
        )
    return {"concepts": out, "total": len(out)}


def _docs_for_concept(con_row: sqlite3.Row, id_url: dict[int, str], doc_ids: dict[tuple[str, str], str]) -> list[dict]:
    """ردیف concept → لیست اسناد حامل (resolve هر دو نوع id: مثبت/منفی)."""
    docs = []
    seen: set[str] = set()
    for i in [x.strip() for x in (con_row["doc_ids"] or "").split(",") if x.strip()]:
        url = ""
        if i.isdigit():
            url = id_url.get(int(i), "")
        elif i.lstrip("-").isdigit():
            url = _HASH_TO_URL.get(i, "")
        if not url or url in seen:
            continue
        seen.add(url)
        with _con(_KB_PATH) as con:
            r = con.execute(
                "SELECT url, title, platform, category, tags, summary_fa, author, status,"
                " saved_at, full_text FROM content WHERE url IN (?, ?)",
                (url, url.rstrip("/")),
            ).fetchone()
        if r:
            item = _doc_row(r, doc_ids)
            if not item.get("view_url") and i.isdigit():
                item["view_url"] = f"/doc/{i}"
            docs.append(item)
    return docs


@router.get("/concepts/{norm}")
async def api_concept_page(norm: str) -> dict[str, Any]:
    """U3b — صفحهٔ مفهوم: همهٔ اسناد حامل مفهوم + مفاهیم هم‌جوار (برای گراف کوچک)."""
    norm_decoded = norm
    with _con(_KB_PATH) as con:
        row = con.execute(
            "SELECT norm, display, doc_count, doc_ids FROM concepts WHERE norm = ?",
            (norm_decoded,),
        ).fetchone()
        if not row:
            # تلاش دوم: بدون حساس به ZWNJ — norm های موجود را می‌سنجیم
            cand = con.execute("SELECT norm, display, doc_count, doc_ids FROM concepts").fetchall()
            target = norm_decoded.replace(" ", "\u200c")
            row = next((r for r in cand if r["norm"] == target), None)
        if not row:
            raise HTTPException(404, "Unknown concept")

        id_url = dict(con.execute("SELECT id, url FROM content").fetchall())
    doc_ids = _registry_doc_ids()
    docs = _docs_for_concept(row, id_url, doc_ids)

    # مفاهیم هم‌جوار: مفاهیمی که در همین اسناد هم هستند (بدون خود مفهوم)
    related: list[dict] = []
    if docs:
        with _con(_KB_PATH) as con:
            id_list = [
                str(i)
                for i, u in id_url.items()
                if any(d["source_url"].rstrip("/") == (u or "").rstrip("/") for d in docs)
            ]
            if id_list:
                placeholders = ",".join("?" * len(id_list))
                cands = con.execute(
                    f"SELECT norm, display, doc_count, doc_ids FROM concepts"
                    f" WHERE doc_ids IN ({placeholders}) OR ({' OR '.join(['doc_ids LIKE ?'] * len(id_list))})"
                    " ORDER BY doc_count DESC LIMIT 20",
                    [*id_list, *[f"%{i}%" for i in id_list]],
                ).fetchall()
            else:
                cands = []
        seen_norm = {row["norm"]}
        for c in cands:
            if c["norm"] in seen_norm:
                continue
            seen_norm.add(c["norm"])
            related.append(
                {
                    "norm": c["norm"],
                    "display": _fix_persian(c["display"]),
                    "doc_count": c["doc_count"],
                }
            )

    return {
        "norm": row["norm"],
        "display": _fix_persian(row["display"]),
        "doc_count": row["doc_count"],
        "docs": docs,
        "related_concepts": related[:10],
    }


@router.get("/graph")
async def api_graph(min_docs: int = Query(1, ge=1, le=50)) -> dict[str, Any]:
    """U3c — گراف: گره‌ها = اسناد + مفاهیم؛ یال‌ها = هم‌مفهومی.

    خروجی سبک برای sigma.js/vis نمایشی — کوئری دیتابیسی، بدون LLM.
    """
    with _con(_KB_PATH) as con:
        crows = con.execute(
            "SELECT norm, display, doc_count, doc_ids FROM concepts WHERE doc_count >= ?"
            " ORDER BY doc_count DESC LIMIT 100",
            (min_docs,),
        ).fetchall()
        id_title = dict(con.execute("SELECT id, title FROM content").fetchall())
        id_url = dict(con.execute("SELECT id, url FROM content").fetchall())
    doc_ids = _registry_doc_ids()

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def _add_doc(url: str) -> str | None:
        did = doc_ids.get(_norm_url(url), "")
        key = f"doc:{did}" if did else f"url:{url}"
        if key not in nodes:
            with _con(_KB_PATH) as con:
                r = con.execute("SELECT title FROM content WHERE url IN (?, ?)", (url, url.rstrip("/"))).fetchone()
            nodes[key] = {
                "id": key,
                "type": "doc",
                "label": _fix_persian((r["title"] if r else url)[:50]),
                "doc_id": did,
                "view_url": f"/view/{did}" if did else "",
            }
        return key

    for c in crows:
        ckey = f"concept:{c['norm']}"
        nodes[ckey] = {
            "id": ckey,
            "type": "concept",
            "label": _fix_persian(c["display"]),
            "doc_count": c["doc_count"],
        }
        for i in [x.strip() for x in (c["doc_ids"] or "").split(",") if x.strip()]:
            url = ""
            if i.isdigit():
                url = id_url.get(int(i), "")
            elif i.lstrip("-").isdigit():
                url = _HASH_TO_URL.get(i, "")
            if not url:
                continue
            dkey = _add_doc(url)
            if dkey:
                edges.append({"source": ckey, "target": dkey})

    # حذف گره‌های سندِ یتیم (هیچ یال ندارند — نباید اتفاق بیفتد ولی محافظ)
    connected = {e["source"] for e in edges} | {e["target"] for e in edges}
    nodes = {k: v for k, v in nodes.items() if k in connected or v["type"] == "concept"}

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


# ---------------- U4-min — نسخه‌بندی ساده (snapshot قبل از رونویسی) ----------------


@router.get("/docs/{doc_id}/versions")
async def api_doc_versions(doc_id: str) -> dict[str, Any]:
    """فهرست نسخه‌های ثبت‌شدهٔ سند (بدون متن کامل — فقط متادیتا)."""
    source_url = _content_url_for(doc_id)
    with _con(_KB_PATH) as con:
        rows = con.execute(
            "SELECT id, title, summary_fa, category, saved_at, snapshotted_at,"
            " length(full_text) AS chars"
            " FROM doc_versions WHERE url IN (?, ?) ORDER BY id DESC",
            (source_url, source_url.rstrip("/")),
        ).fetchall()
    return {
        "doc_id": doc_id,
        "versions": [
            {
                "version_id": r["id"],
                "title": _fix_persian(r["title"] or ""),
                "summary_fa": _fix_persian(r["summary_fa"] or ""),
                "category": r["category"] or "",
                "saved_at": r["saved_at"] or "",
                "snapshotted_at": r["snapshotted_at"] or "",
                "chars": r["chars"] or 0,
            }
            for r in rows
        ],
        "total": len(rows),
        "max_kept": 5,
    }


@router.get("/docs/{doc_id}/versions/{version_id}")
async def api_doc_version_detail(doc_id: str, version_id: int) -> dict[str, Any]:
    """محتوای کامل یک نسخه (markdown + متادیتای ثبت‌شده هنگام snapshot)."""
    source_url = _content_url_for(doc_id)
    with _con(_KB_PATH) as con:
        r = con.execute(
            "SELECT id, url, full_text, title, summary_fa, tags, category, saved_at, snapshotted_at"
            " FROM doc_versions WHERE id = ? AND url IN (?, ?)",
            (version_id, source_url, source_url.rstrip("/")),
        ).fetchone()
    if not r:
        raise HTTPException(404, "Version not found")
    return {
        "doc_id": doc_id,
        "version_id": r["id"],
        "title": _fix_persian(r["title"] or ""),
        "markdown": r["full_text"] or "",
        "summary_fa": _fix_persian(r["summary_fa"] or ""),
        "tags": _parse_tags(r["tags"]),
        "category": r["category"] or "",
        "saved_at": r["saved_at"] or "",
        "snapshotted_at": r["snapshotted_at"] or "",
    }


@router.post("/docs/{doc_id}/versions/{version_id}/restore")
async def api_doc_version_restore(doc_id: str, version_id: int) -> dict[str, Any]:
    """بازگردانی نسخه: نسخهٔ فعلی اول snapshot می‌شود، بعد نسخهٔ انتخابی جایش می‌نشیند."""
    source_url = _content_url_for(doc_id)
    with _con(_KB_PATH) as con:
        v = con.execute(
            "SELECT full_text, title, summary_fa, tags, category FROM doc_versions"
            " WHERE id = ? AND url IN (?, ?)",
            (version_id, source_url, source_url.rstrip("/")),
        ).fetchone()
        if not v:
            raise HTTPException(404, "Version not found")
        # بازگردانی امن: قبل از رونویسی، وضعیت فعلی خودش snapshot می‌شود
        _snapshot_if_exists(con, source_url)
        con.execute(
            "UPDATE content SET full_text = ?, title = COALESCE(NULLIF(?, ''), title),"
            " summary_fa = COALESCE(NULLIF(?, ''), summary_fa),"
            " tags = COALESCE(NULLIF(?, ''), tags),"
            " category = COALESCE(NULLIF(?, ''), category)"
            " WHERE url IN (?, ?)",
            (
                v["full_text"],
                v["title"],
                v["summary_fa"],
                v["tags"],
                v["category"],
                source_url,
                source_url.rstrip("/"),
            ),
        )
        con.commit()
        restored_chars = len(v["full_text"] or "")
        # نسخهٔ بازگردانده‌شده از صف نسخه‌ها حذف می‌شود (دیگر «قبلی» نیست)
        con.execute("DELETE FROM doc_versions WHERE id = ?", (version_id,))
        con.commit()
    return {
        "doc_id": doc_id,
        "restored_version_id": version_id,
        "restored_chars": restored_chars,
        "note": "نسخهٔ فعلی قبل از بازگردانی snapshot شد",
    }
