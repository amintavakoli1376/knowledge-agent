"""Concept registry — LLM-Wiki phase 1b.

پایگاه مفاهیم: هر سند تحلیل‌شده مفاهیم فارسی‌اش را اینجا ثبت می‌کند؛
مفاهیمی که در ۲+ سند بیایند «مشترک» محسوب می‌شوند و در سند Outline
به‌صورت [[ویکی‌لینک]] نمایش داده می‌شوند (فاز ۲ صفحهٔ مفهومی می‌سازد).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = "/root/knowledge-agent-repo/data/knowledge.db"

# نرمال‌سازی مفهوم برای دی‌داپ: حذف نیم‌فاصله/فاصله اضافی/اعراب
_ZWNJ = "\u200c"


def _norm(name: str) -> str:
    s = (name or "").strip().lower()
    s = s.replace("،", " ").replace("؛", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.replace(" ", _ZWNJ)  # «حافظه عامل‌ها» == «حافظهٔ عامل‌ها» بعد از نرمال؟ نه — فقط فاصله→نیم‌فاصله برای یکدستی
    return s[:120]


def init_concepts() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS concepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                norm TEXT NOT NULL UNIQUE,
                display TEXT NOT NULL,
                doc_count INTEGER DEFAULT 1,
                doc_ids TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _resolve_doc_key(source_url: str, doc_id: str) -> int:
    """کلید پایدار برای جدول concepts — هش URL (منفی، جدا از rowidهای content)."""
    import hashlib

    if source_url:
        h = int(hashlib.sha1(source_url.encode()).hexdigest()[:8], 16)
        return -(h % 1_000_000_000) - 1  # منفی — تداخل با content.id ندارد
    return -(abs(hash(doc_id)) % 1_000_000_000) - 1


def register_doc_concepts(doc_id: int, concepts: list[str]) -> list[str]:
    """ثبت مفاهیم یک سند؛ خروجی: مفاهیمِ «مشترک» (۲+ سند) بعد از این ثبت.

    idempotent: اگر همین doc قبلاً همین مفهوم را داشت، count دوباره بالا نمی‌رود.
    F4 (2026-09-17): guard — سندِ ناموجود در content هرگز مفهوم ثبت نمی‌کند
    (جلوگیری از تکرار فاجعهٔ ردیف‌های تستی id 1-3 که doc_ids وهمی داشتند).
    """
    if not doc_id or not concepts:
        return []
    init_concepts()
    conn = sqlite3.connect(DB_PATH)
    try:
        exists = conn.execute(
            "SELECT 1 FROM content WHERE id = ?", (doc_id,)
        ).fetchone()
        if not exists:
            import logging

            logging.getLogger(__name__).warning(
                "register_doc_concepts: doc_id %s not in content — concepts rejected (F4 guard)", doc_id
            )
            return []
    finally:
        conn.close()
    shared: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    try:
        for raw in concepts[:10]:
            display = (raw or "").strip()[:120]
            if not display:
                continue
            norm = _norm(display)
            if not norm:
                continue
            row = conn.execute(
                "SELECT id, doc_count, doc_ids FROM concepts WHERE norm = ?", (norm,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO concepts (norm, display, doc_count, doc_ids) VALUES (?, ?, 1, ?)",
                    (norm, display, str(doc_id)),
                )
            else:
                cid, cnt, ids = row
                id_list = [x for x in (ids or "").split(",") if x]
                if str(doc_id) in id_list:
                    continue  # همین سند قبلاً ثبت — دی‌داپ
                id_list.append(str(doc_id))
                new_cnt = cnt + 1
                # نمایش: هر بار معتبرترین (کوتاه‌ترین) نمایش را نگه دار
                best = display if len(display) < len(json.loads(json.dumps(id_list)) or "") else None
                conn.execute(
                    "UPDATE concepts SET doc_count=?, doc_ids=?, display=CASE WHEN ?<length(display) THEN ? ELSE display END WHERE id=?",
                    (new_cnt, ",".join(id_list[:50]), len(display), display, cid),
                )
                if new_cnt >= 2:
                    shared.append(display)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — registry is best-effort
        logger.warning(f"concept register failed (non-fatal): {e}")
    finally:
        conn.close()
    return shared


def shared_concepts_for_preview(candidates: list[str]) -> list[str]:
    """کدام یک از کاندیدهای مفهومیِ سندِ جدید، هم‌اکنون در ۲+ سندِ دیگر هستند؟
    (برای ویکی‌لینک‌گذاری در md — قبل از ثبت نهایی سند)
    """
    if not candidates:
        return []
    init_concepts()
    out: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    try:
        for raw in candidates[:10]:
            norm = _norm(raw)
            if not norm:
                continue
            row = conn.execute(
                "SELECT display, doc_ids FROM concepts WHERE norm = ? AND doc_count >= 2",
                (norm,),
            ).fetchone()
            if row and row[0] not in out:
                out.append(row[0])
    finally:
        conn.close()
    return out


def shared_concepts_for_doc(doc_id: int) -> list[str]:
    """مفاهیم مشترکی که این سند دارد (برای ویکی‌لینک‌گذاری)."""
    init_concepts()
    out: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    try:
        for (ids, display,) in conn.execute(
            "SELECT doc_ids, display FROM concepts WHERE doc_count >= 2"
        ).fetchall():
            if str(doc_id) in [x for x in (ids or "").split(",") if x]:
                out.append(display)
    finally:
        conn.close()
    return out


def all_shared_concepts(limit: int = 200) -> list[tuple[str, int, list[int]]]:
    """همهٔ مفاهیم مشترک (display, doc_count, doc_ids) — برای صفحهٔ مفهومی فاز ۲."""
    init_concepts()
    out: list[tuple[str, int, list[int]]] = []
    conn = sqlite3.connect(DB_PATH)
    try:
        for norm, display, cnt, ids in conn.execute(
            "SELECT norm, display, doc_count, doc_ids FROM concepts WHERE doc_count >= 2 ORDER BY doc_count DESC LIMIT ?",
            (limit,),
        ).fetchall():
            id_list = [int(x) for x in (ids or "").split(",") if x.strip().isdigit()]
            out.append((display, cnt, id_list))
    finally:
        conn.close()
    return out
