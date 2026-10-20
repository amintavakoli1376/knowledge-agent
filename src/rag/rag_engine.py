"""RAG engine — hybrid retrieval (vector + FTS5) + LLM answer via settings model.

بازطراحی 2026-09-12 (ریشه‌یابی باگ «پاسخ اشتباه/پیدا نکردم»):
1. retrieval سندمحور است: بهترین chunk هر سند، امتیاز آن سند است (نه تک‌chunk) —
   سندهای چندchunkی قبلاً چند بار در top-K جمع می‌شدند و جای سندهای دیگر را می‌گرفتند.
2. context از متن کامل سندهای برنده ساخته می‌شود (نه chunk ۳۰۰-۱۲۰۰ کاراکتری) —
   پاسخ‌دهی واقعی به متن کاملِ محتوای ذخیره‌شده وابسته است.
3. فراخوانی LLM سه بار با backoff تکرار می‌شود (۴۲۹ سرویس رایگان سریال است) و
   در بدترین حالت پیام صادقانهٔ «سرویس شلوغ» برمی‌گردد، نه پاسخ گمراه‌کننده.
"""
import json
import logging
import re
import sqlite3

from openai import AsyncOpenAI

from ..config import settings
from ..storage.sqlite_store import _get_connection
from .embedder import get_embedder
from . import vector_store
from ..storage.db import connect_db

logger = logging.getLogger(__name__)

HYBRID_TOP_K = 6          # تعداد سندهای برنده در context (سندمحور)
FTS_TOP_K = 3
LLM_RETRIES = 3
LLM_BACKOFF = 20          # ثانیه — 429 سرویس رایگان reset کوتاه دارد

# 2b: نگاشت hash(url) منفی → url — یک بار در import ساخته می‌شود (content همه ردیف‌ها)
def _build_hash_map() -> dict[str, str]:
    import hashlib

    m: dict[str, str] = {}
    try:
        conn = _get_connection()
        try:
            for r in conn.execute("SELECT url FROM content WHERE url IS NOT NULL AND url != ''"):
                u = r["url"]
                k = -(int(hashlib.sha1(u.encode()).hexdigest()[:8], 16) % 1_000_000_000) - 1
                m[str(k)] = u
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"hash map build skipped: {e}")
    return m

_HASH_TO_URL_MAP = _build_hash_map()


def fts_search(query: str, limit: int = FTS_TOP_K) -> list:
    """BM25-style FTS5 keyword search (hybrid complement)."""
    # FTS5 needs a query without weird chars; quote multi-word as phrase-ish
    tokens = re.findall(r"[\w\u0600-\u06FF]+", query)
    if not tokens:
        return []
    fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])

    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.url, c.title, c.platform, c.category, c.summary_fa, c.full_text
            FROM content_fts f
            JOIN content c ON c.id = f.rowid
            WHERE content_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        results.append({
            "content_id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "platform": r["platform"],
            "category": r["category"],
            "summary_fa": r["summary_fa"],
            "snippet": (r["full_text"] or "")[:300],
        })
    return results


def _dedupe(items):
    seen = set()
    out = []
    for it in items:
        cid = it.get("content_id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(it)
    return out


class RagEngine:
    """Answer a question using the knowledge base (hybrid RAG)."""

    def __init__(self):
        self.llm = None  # lazy AsyncOpenAI

    def _get_llm(self):
        if self.llm is None:
            self.llm = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
        return self.llm

    async def ask(self, question: str) -> str:
        """Retrieve + answer. Returns markdown text for Telegram."""
        emb = get_embedder()
        qv = emb.embed_query(question)  # prefix «query: » — الزام e5

        vector_hits = []
        if qv:
            vector_hits = vector_store.search(qv, limit=HYBRID_TOP_K * 3)

        fts_hits = fts_search(question)

        # --- گروه‌بندی سندمحور: امتیاز هر سند = بهترین chunk آن ---
        doc_best: dict[int, dict] = {}
        for hit in vector_hits:
            cid = hit.get("content_id")
            prev = doc_best.get(cid)
            if prev is None or hit.get("score", 0) > prev.get("score", 0):
                doc_best[cid] = hit

        # FTS فقط غنی‌سازی/پوشش است؛ سند برندهٔ برداری اگر در FTS هم بود تکرار نمی‌شود
        for hit in fts_hits:
            doc_best.setdefault(hit.get("content_id"), hit)

        # سندهای برداری اول (بر اساس امتیاز)، بعد FTS-ای‌های اضافه
        ranked_docs = sorted(
            (h for h in doc_best.values() if h in vector_hits or h.get("score")),
            key=lambda h: -h.get("score", 0),
        )
        fts_only = [h for h in fts_hits if h.get("content_id") in doc_best and h not in ranked_docs]
        merged = _dedupe(ranked_docs + fts_only)[:HYBRID_TOP_K]

        if not merged:
            return (
                "❌ چیزی توی دانشِ من پیدا نکردم.\n\n"
                "💡 یه پست/لینک بفرست تا ذخیره کنم، بعد می‌تونم به سؤالاتت جواب بدم."
            )

        # --- context از متن کامل سند برنده (chunk فقط مکالمه را گران می‌کند بی‌فایده) ---
        context_parts = []
        full_texts = self._full_texts([h["content_id"] for h in merged])
        for i, hit in enumerate(merged, 1):
            title = hit.get("title") or "بدون عنوان"
            url = hit.get("url") or ""
            body = full_texts.get(hit["content_id"]) or hit.get("chunk_text") or hit.get("summary_fa") or ""
            context_parts.append(
                f"[{i}] عنوان: {title}\n"
                f"لینک: {url}\n"
                f"متن: {body[:2600]}"
            )

        # --- LLM-Wiki 2b: Graph Expansion — اسناد هم‌مفهومِ برنده‌ها (+۲ سند سقف) ---
        expanded_ids, expansion_note = self._graph_expand([h["content_id"] for h in merged], [h["url"] for h in merged])
        for cid in expanded_ids:
            body = full_texts.get(cid) or ""
            if not body:
                continue
            with _get_connection() as conn:
                r = conn.execute("SELECT url, title FROM content WHERE id = ?", (cid,)).fetchone()
            if not r:
                continue
            context_parts.append(
                f"[+] عنوان: {r['title'] or 'بدون عنوان'}\n"
                f"لینک: {r['url'] or ''}\n"
                f"متن: {body[:1800]}"
            )
        if expansion_note:
            logger.info(f"RAG graph expansion: +{len(expanded_ids)} docs via shared concepts")

        context = "\n\n".join(context_parts)

        # LLM-Wiki 1c: purpose.md (چرای پایگاه) — اگر موجود باشد، اول prompt تزریق می‌شود
        purpose_block = ""
        try:
            from pathlib import Path as _Path

            _purpose_file = _Path(settings.data_dir) / "purpose.md"
            if _purpose_file.exists():
                _purpose_text = _purpose_file.read_text(encoding="utf-8").strip()
                if _purpose_text:
                    purpose_block = (
                        "🎯 هدف این پایگاه دانش (برای هم‌راستایی پاسخ):\n"
                        + _purpose_text[:1200]
                        + "\n\n"
                    )
        except Exception as e:  # noqa: BLE001 — purpose is optional enrichment
            logger.debug(f"purpose.md load skipped: {e}")

        prompt = f"""تو یک دستیار دانشی هستی. بر اساس متن‌های زیر که از پایگاه دانشِ کاربر retrieved شده، به سؤال او پاسخ بده.

{purpose_block}پایگاه دانش (داکیومنت‌های مرتبط):
{context}

سؤال کاربر:
{question}

قوانین:
- پاسخ را به فارسی روان بده.
- اگر پاسخ در متن‌ها نبود، صادقانه بگو و حدس نزن.
- اگر چند سند مرتبط هست، از همهٔ آن‌ها ترکیب کن و راهکارهای عملی را برجسته کن.
- **هر ادعا/گزاره‌ای که از یک سند می‌آید، در انتها شمارهٔ همان سند را داخل کروشه بیاور** — مثل [1] یا [2] یا [+] (برای سندهای گراف-گسترش‌یافته). این استنادها اجباری‌اند.
- **هیچ بخش «منابع» یا لیست لینک در پاسخ ننویس** — منابع جداگانه به کاربر نمایش داده می‌شود."""

        llm = self._get_llm()
        answer = ""
        last_err = None
        for attempt in range(LLM_RETRIES):
            try:
                resp = await llm.chat.completions.create(
                    model=settings.openai_model,
                    messages=[
                        {"role": "system", "content": "تو یک دستیار دانشی دقیق و فارسی‌زبان هستی."},
                        {"role": "user", "content": prompt},
                    ],
                    # gpt-5.x فقط temperature=1 (پیش‌فرض) را قبول می‌کند — پارامتر را نفرست
                )
                answer = resp.choices[0].message.content or ""
                break
            except Exception as e:
                last_err = e
                logger.warning(f"RAG LLM call failed (attempt {attempt + 1}/{LLM_RETRIES}): {e}")
                if attempt < LLM_RETRIES - 1:
                    import asyncio

                    await asyncio.sleep(LLM_BACKOFF)
        if not answer:
            logger.error(f"RAG LLM call failed after {LLM_RETRIES} attempts: {last_err}")
            # پاسخ گمراه‌کنندهٔ «چیزی پیدا نکردم» ممنوع — retrieval جواب داشته؛ فقط LLM در دسترس نبود
            busy = (
                "⏳ الان سرویس هوش مصنوعی شلوغه و جواب آماده نشد.\n"
                "🔎 ولی این سند مرتبط توی دانشت هست:\n"
            )
            # USER RULE: فقط اولین منبع
            hit = merged[0]
            title = (hit.get("title") or "بدون عنوان").split("\n")[0][:70]
            url = hit.get("url") or ""
            return busy + f"\n• {title} — {url}"

        sources = []
        seen_urls = set()
        for hit in merged:
            url = hit.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append(url)

        # 2c: فهرست استنادها — فقط سندهایی که در پاسخ واقعاً به آن‌ها ارجاع شده ([1]، [2]، [+])
        import re as _re

        cited_nums = set(_re.findall(r"\[(\d+)\]", answer))
        cited_plus = "[+]" in answer
        cite_lines = []
        for i, hit in enumerate(merged, 1):
            if str(i) not in cited_nums:
                continue
            url = hit.get("url") or ""
            title = (hit.get("title") or "منبع").split("\n")[0][:70]
            if url:
                cite_lines.append(f"[{i}] [{title}]({url})")
        if cited_plus:
            # سندهای گراف-گسترش‌یافته — عنوانشان از context_parts
            for cid in expanded_ids:
                with _get_connection() as conn:
                    r = conn.execute("SELECT url, title FROM content WHERE id = ?", (cid,)).fetchone()
                if r and r["url"]:
                    t = (r["title"] or "منبع").split("\n")[0][:70]
                    cite_lines.append(f"[+] [{t}]({r['url']})")

        # USER RULE: فقط اولین منبع (پرت relevانس) اگر استنادی استفاده نشده بود
        if cite_lines:
            footer = "\n\n📎 **منابع cited:**\n" + "\n".join(cite_lines[:4])
        elif sources:
            first_title = (merged[0].get("title") or "منبع").split("\n")[0][:70]
            footer = f"\n\n📎 **منبع اصلی:** [{first_title}]({sources[0]})"
        else:
            footer = ""
        return answer.strip() + footer

    def _full_texts(self, content_ids: list[int]) -> dict[int, str]:
        """متن کامل سندها (یک کوئری، dict بر اساس id)."""
        if not content_ids:
            return {}
        marks = ",".join("?" for _ in content_ids)
        conn = _get_connection()
        try:
            rows = conn.execute(
                f"SELECT id, full_text FROM content WHERE id IN ({marks})",
                list(content_ids),
            ).fetchall()
        finally:
            conn.close()
        return {r["id"]: (r["full_text"] or "") for r in rows}

    @staticmethod
    def _graph_expand(hit_ids: list[int], hit_urls: list[str]) -> tuple[list[int], str]:
        """2b — Graph Expansion: سندهایی که با برنده‌ها مفهوم مشترک دارند (+۲ سند).

        مفاهیم از جدول concepts (فاز 1b) — هر دو نوع doc_id (مثبت=content.id،
        منفی=-hash(url)) resolve می‌شود. خروجی: id سندهای جدید (نه خود برنده‌ها).
        """
        try:
            from ..storage.concepts import DB_PATH as _CDB

            winners_ids = set(hit_ids)
            winners_urls = {u.rstrip("/") for u in hit_urls if u}

            def _url_key(u: str) -> int:
                import hashlib

                return -(int(hashlib.sha1(u.encode()).hexdigest()[:8], 16) % 1_000_000_000) - 1

            conn = connect_db(_CDB)
            conn.row_factory = sqlite3.Row
            try:
                cand_ids: list[int] = []
                for row in conn.execute(
                    "SELECT doc_ids FROM concepts WHERE doc_count >= 2"
                ).fetchall():
                    members = [x.strip() for x in (row["doc_ids"] or "").split(",") if x.strip()]
                    pos = {int(x) for x in members if x.isdigit()}
                    neg = {x for x in members if x.lstrip("-").isdigit() and x.startswith("-")}
                    # مفهوم مشترک بین برنده و غیربرنده؟
                    if not (pos & winners_ids or any(_url_key(u) in neg for u in winners_urls)):
                        continue
                    for x in members:
                        if x.isdigit():
                            cid = int(x)
                            if cid not in winners_ids:
                                cand_ids.append(cid)
                        elif x.startswith("-"):
                            url = _HASH_TO_URL_MAP.get(x)
                            if not url:
                                continue
                            with _get_connection() as c2:
                                r2 = c2.execute(
                                    "SELECT id FROM content WHERE url IN (?, ?)", (url, url.rstrip("/"))
                                ).fetchone()
                            if r2 and r2["id"] not in winners_ids:
                                cand_ids.append(r2["id"])
            finally:
                conn.close()
            # دی‌داپ + سقف ۲
            seen: list[int] = []
            for c in cand_ids:
                if c not in seen:
                    seen.append(c)
            return seen[:2], "ok" if seen else ""
        except Exception as e:  # noqa: BLE001 — expansion is best-effort enrichment
            logger.debug(f"graph expand skipped: {e}")
            return [], ""
