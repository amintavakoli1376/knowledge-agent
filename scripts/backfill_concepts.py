"""Backfill جدول concepts از analysis_meta اسناد موجود.

چرا: ثبت مفاهیم قبلاً فقط در مسیر Outline انجام می‌شد؛ در نصب بدون Outline
هیچ مفهومی ثبت نشده بود و صفحهٔ «مفاهیم مشترک» خالی می‌ماند.

اجرا: ./venv/bin/python scripts/backfill_concepts.py [--dry-run]
"""
from __future__ import annotations

import json
import sys

from src.storage import sqlite_store
from src.storage import concepts as concept_store


def main() -> int:
    dry = "--dry-run" in sys.argv
    conn = sqlite_store._get_connection()
    rows = conn.execute(
        "SELECT id, url, title, analysis_meta FROM content ORDER BY id"
    ).fetchall()
    conn.close()

    total_links = 0
    for r in rows:
        meta_raw = r["analysis_meta"] or ""
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except Exception:
            meta = {}
        items = meta.get("concepts") or []
        if not items:
            print(f"skip id={r['id']} (no concepts in analysis_meta)")
            continue
        print(f"id={r['id']} concepts={len(items)} → {', '.join(str(x) for x in items[:6])}")
        if not dry:
            concept_store.register_doc_concepts(int(r["id"]), list(items))
        total_links += len(items)

    shared = concept_store.all_shared_concepts()
    print(f"\nconcept links: {total_links} · shared (2+ docs): {len(shared)}")
    for display, cnt, ids in shared[:15]:
        print(f"  {cnt}× {display} -> {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
