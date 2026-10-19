"""Canonicalize مفاهیم موجود: مفاهیم بلند/مخصوصِ هر سند را به برچسب‌های کوتاه و مشترک تبدیل می‌کند.

چرا: پرامپت قبلی «عبارت کوتاه فارسی» می‌خواست و مدل عبارت‌های ۵–۸ کلمه‌ای مخصوصِ
هر سند می‌ساخت («مهندسی معکوس نرم‌افزار با هوش مصنوعی» در برابر «خودکارسازی مهندسی
معکوس»). نتیجه: با وجود ۴ سند، هیچ مفهومی در ۲+ سند تکرار نمی‌شد.

این اسکریپت:
  ۱) همهٔ مفاهیم ثبت‌شده را یک‌جا به LLM می‌دهد و یک نقشهٔ یکتاسازی می‌گیرد
  ۲) جدول concepts را از نو می‌سازد (per-doc canonical labels)
  ۳) تعداد مفاهیم مشترک را گزارش می‌دهد

اجرا: PYTHONPATH=. ./venv/bin/python scripts/refine_concepts.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys

from openai import AsyncOpenAI

from src.config import settings
from src.storage import sqlite_store
from src.storage import concepts as concept_store

PROMPT = """You are normalizing a knowledge-base concept index (Persian).

Input: a JSON array of concept phrases extracted from different documents.
Many are document-specific long phrases for the same underlying topic.

Task: return ONE JSON object mapping every input string to a canonical label.
Rules for canonical labels:
- Persian preferred; standard acronyms allowed (RAG, LLM, PDF, API).
- 1-3 words, the most common/general form of the term.
- Synonyms and sub-topics of the same idea MUST map to the same label
  (e.g. «خودکارسازی مهندسی معکوس» and «مهندسی معکوس نرم‌افزار با هوش مصنوعی»
  → «مهندسی معکوس»; «استخراج ساختار کد با Tree-sitter» → «تحلیل کد»).
- Do not invent labels that are absent from the input meanings.
- Every input key must appear exactly once in the output.

Input concepts:
{items}

Respond with ONLY the JSON object."""


async def canonical_map(labels: list[str]) -> dict[str, str]:
    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You normalize terminology and return strict JSON."},
            {"role": "user", "content": PROMPT.format(items=json.dumps(labels, ensure_ascii=False, indent=1))},
        ],
        temperature=0,
        max_tokens=16000,
    )
    raw = resp.choices[0].message.content or ""
    if not raw.strip():
        usage = resp.usage.model_dump() if resp.usage else {}
        raise RuntimeError(
            f"empty model content (finish={resp.choices[0].finish_reason}, usage={usage})"
        )
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"no JSON in model output: {raw[:200]}")
    data = json.loads(raw[start:end + 1])
    return {str(k): str(v).strip() for k, v in data.items() if str(v).strip()}


async def main() -> int:
    dry = "--dry-run" in sys.argv
    conn = sqlite_store._get_connection()
    rows = conn.execute("SELECT id, analysis_meta FROM content ORDER BY id").fetchall()
    conn.close()

    per_doc: dict[int, list[str]] = {}
    for r in rows:
        try:
            meta = json.loads(r["analysis_meta"] or "{}")
        except Exception:
            meta = {}
        items = [str(x).strip() for x in (meta.get("concepts") or []) if str(x).strip()]
        if items:
            per_doc[int(r["id"])] = items

    all_labels = sorted({lab for v in per_doc.values() for lab in v})
    print(f"docs={len(per_doc)} unique_concepts={len(all_labels)}")
    if not all_labels:
        print("nothing to refine")
        return 0

    mapping = await canonical_map(all_labels)
    for lab in all_labels:
        mapping.setdefault(lab, lab)

    print("\nنقشهٔ یکتاسازی:")
    for lab in all_labels:
        if mapping[lab] != lab:
            print(f"  {lab}  →  {mapping[lab]}")

    if dry:
        print("\n[dry-run] concepts table untouched")
        return 0

    # بازسازی concepts از صفر با برچسب‌های canonical
    init = concept_store.init_concepts
    init()
    conn = sqlite_store._get_connection()
    conn.execute("DELETE FROM concepts")
    conn.commit()
    conn.close()

    for doc_id, items in per_doc.items():
        canon: list[str] = []
        for lab in items:
            c = mapping.get(lab, lab)
            if c not in canon:
                canon.append(c)
        concept_store.register_doc_concepts(doc_id, canon)

    shared = concept_store.all_shared_concepts()
    print(f"\nshared concepts (2+ docs): {len(shared)}")
    for display, cnt, ids in shared[:20]:
        print(f"  {cnt}× {display} -> {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
