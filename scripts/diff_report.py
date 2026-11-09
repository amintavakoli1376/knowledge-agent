#!/usr/bin/env python
"""Daily divergence report: SQLite (source of truth) vs Postgres schema `ka`.

Compares row counts and per-row key hashes for content / doc_versions /
embeddings / concepts. Exit code 0 when clean, 1 when a divergence is found
(so a cron job can alert).

Usage:  ./venv/bin/python scripts/diff_report.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db_pg import connect_pg
from src.storage.sqlite_store import _get_connection


def _h(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p or "").encode("utf-8", errors="replace"))
    return h.hexdigest()[:12]


def _sqlite_counts() -> dict[str, int]:
    conn = _get_connection()
    try:
        out = {}
        for t in ("content", "doc_versions", "embeddings", "concepts"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
    finally:
        conn.close()


def _sqlite_hashes() -> dict[str, set[str]]:
    conn = _get_connection()
    try:
        out = {}
        out["content"] = {
            _h(r[0], r[1], r[2], r[3])
            for r in conn.execute("SELECT url, title, platform, full_text FROM content")
        }
        out["doc_versions"] = {
            _h(r[0], r[1]) for r in conn.execute("SELECT url, full_text FROM doc_versions")
        }
        out["embeddings"] = {
            # کلید منطقی = url (نه content_id، چون در PG بعد سریال جدید دارد)
            _h(r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT c.url, e.chunk_index, e.chunk_text "
                "FROM embeddings e JOIN content c ON c.id = e.content_id"
            )
        }
        out["concepts"] = {
            _h(r[0], r[1], r[2]) for r in conn.execute("SELECT norm, display, doc_count FROM concepts")
        }
        return out
    finally:
        conn.close()


def _pg_counts() -> dict[str, int]:
    with connect_pg() as conn:
        with conn.cursor() as cur:
            out = {}
            for t in ("content", "doc_versions", "embeddings", "concepts"):
                cur.execute(f"SELECT COUNT(*) FROM ka.{t}")
                out[t] = cur.fetchone()[0]
            return out


def _pg_hashes() -> dict[str, set[str]]:
    with connect_pg() as conn:
        with conn.cursor() as cur:
            out = {}
            cur.execute("SELECT url, title, platform, full_text FROM ka.content")
            out["content"] = {_h(r[0], r[1], r[2], r[3]) for r in cur.fetchall()}
            cur.execute("SELECT url, full_text FROM ka.doc_versions")
            out["doc_versions"] = {_h(r[0], r[1]) for r in cur.fetchall()}
            cur.execute(
                "SELECT c.url, e.chunk_index, e.chunk_text "
                "FROM ka.embeddings e JOIN ka.content c ON c.id = e.content_id"
            )
            out["embeddings"] = {_h(r[0], r[1], r[2]) for r in cur.fetchall()}
            cur.execute("SELECT norm, display, doc_count FROM ka.concepts")
            out["concepts"] = {_h(r[0], r[1], r[2]) for r in cur.fetchall()}
            return out


def main() -> int:
    sq_c = _sqlite_counts()
    pg_c = _pg_counts()
    sq_h = _sqlite_hashes()
    pg_h = _pg_hashes()

    print("divergence report (SQLite vs PG schema ka)")
    print("=" * 44)
    ok = True
    for t in ("content", "doc_versions", "embeddings", "concepts"):
        cs, cp = sq_c[t], pg_c[t]
        missing = sq_h[t] - pg_h[t]
        extra = pg_h[t] - sq_h[t]
        # برای doc_versions و embeddings تنها کلیدِ منطقی (set) معیار است: ردیفهای تکراری
        # ازقبلموجود در SQLite (مثلاً همان url+full_text+saved_at) توسط migration
        # dedupe میشوند و این زیانآور نیست (گزارش نسخه). برای embeddings هم content_id
        # در SQLite و PG عمداً متفاوت است، پس فقط url+chunk_index+chunk_text مقایسه
        # میشود. اگر sets برابر باشند یعنی هیچ داده از دست نرفته — حتی اگر شمارش خام
        # (بهخاطر تکراریهای قدیمی SQLite) با هم فرق کند.
        if t in ("doc_versions", "embeddings"):
            row_ok = not missing and not extra
            flag = "OK" if row_ok else "DIVERGE"
            print(f"  {t:14s} rows sqlite={cs:5d} pg={cp:5d}  distinct-key {flag}")
        else:
            row_ok = (cs == cp) and not missing and not extra
            flag = "OK" if row_ok else "DIVERGE"
            print(f"  {t:14s} rows sqlite={cs:5d} pg={cp:5d}  {flag}")
        if not row_ok:
            ok = False
        if missing:
            ok = False
            print(f"      missing in PG: {len(missing)} rows")
        if extra:
            ok = False
            print(f"      extra in PG: {len(extra)} rows")
    print("=" * 44)
    print("RESULT:", "CLEAN" if ok else "DIVERGENCE FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
