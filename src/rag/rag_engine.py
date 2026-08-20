"""RAG engine — hybrid retrieval (vector + FTS5) + LLM answer via settings model."""
import json
import logging
import re
import sqlite3

from openai import AsyncOpenAI

from ..config import settings
from ..storage.sqlite_store import _get_connection
from .embedder import get_embedder
from . import vector_store

logger = logging.getLogger(__name__)

HYBRID_TOP_K = 4
FTS_TOP_K = 3


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
        qv = emb.embed_one(question)

        vector_hits = []
        if qv:
            vector_hits = vector_store.search(qv, limit=HYBRID_TOP_K)

        fts_hits = fts_search(question)

        # منبع اصلی: فقط نتایج برداری با امتیاز خوب (شباهت معنایی واقعی)
        strong_hits = [h for h in vector_hits if h.get("score", 0) >= 0.45]
        if not strong_hits:
            strong_hits = vector_hits[:1]  # حداقل بهترین برداری

        # متن غنی‌سازی: برداری + FTS (برای LLM، بدون امتیاز معنایی)
        merged = _dedupe(vector_hits + fts_hits)
        if not merged:
            return (
                "❌ چیزی توی دانشِ من پیدا نکردم.\n\n"
                "💡 یه پست/لینک بفرست تا ذخیره کنم، بعد می‌تونم به سؤالاتت جواب بدم."
            )

        # Build context — فقط از منابع معنایی قوی
        context_parts = []
        for i, hit in enumerate(strong_hits, 1):
            title = hit.get("title") or "بدون عنوان"
            url = hit.get("url") or ""
            summary = hit.get("summary_fa") or ""
            chunk = hit.get("chunk_text") or ""
            text_body = chunk or summary or ""
            context_parts.append(
                f"[{i}] عنوان: {title}\n"
                f"لینک: {url}\n"
                f"متن: {text_body[:1200]}"
            )
        context = "\n\n".join(context_parts)

        prompt = f"""تو یک دستیار دانشی هستی. بر اساس متن‌های زیر که از پایگاه دانشِ کاربر retrieved شده، به سؤال او پاسخ بده.

پایگاه دانش (داکیومنت‌های مرتبط):
{context}

سؤال کاربر:
{question}

قوانین:
- پاسخ را به فارسی روان بده.
- اگر پاسخ در متن‌ها نبود، صادقانه بگو و حدس نزن.
- **هیچ بخش «منابع» یا لیست لینک در پاسخ ننویس** — منابع جداگانه به کاربر نمایش داده می‌شود."""

        llm = self._get_llm()
        try:
            resp = await llm.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "تو یک دستیار دانشی دقیق و فارسی‌زبان هستی."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            answer = resp.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"RAG LLM call failed: {e}")
            answer = ""

        sources = []
        seen_urls = set()
        for hit in strong_hits:
            url = hit.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append(url)

        if answer:
            footer = "\n\n📎 **منابع:**\n" + "\n".join(f"- {u}" for u in sources) if sources else ""
            return answer.strip() + footer

        # fallback: no LLM answer, show retrieved docs (فقط منابع معنایی قوی)
        lines = ["🔎 چیزی پیدا کردم ولی مدل جواب نداد. این‌ها مرتبط‌ترین‌ها هستن:\n"]
        for hit in strong_hits[:3]:
            title = hit.get("title") or "بدون عنوان"
            url = hit.get("url") or ""
            lines.append(f"• {title} — {url}")
        return "\n".join(lines)