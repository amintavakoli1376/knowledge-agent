#!/usr/bin/env python
"""One-time migration: SQLite (knowledge.db) -> Postgres schema `ka`.

Idempotent: rerunning reconciles by natural key (`url` for content,
`content_id+chunk_index` for embeddings, `norm` for concepts) and skips
rows already present. Prints a summary of inserted / matched / skipped per table.

Usage:  ./venv/bin/python scripts/migrate_to_pg.py [--dry-run]
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from src.config import settings
from src.storage.db_pg import connect_pg
from src.storage.sqlite_store import DB_PATH, _get_connection

TABLES = ("content", "doc_versions", "embeddings", "concepts")


def _blob_to_vec(blob: bytes) -> str:
    """float32 little-endian BLOB -> pgvector string literal."""
    if blob is None:
        return "[]"
    arr = np.frombuffer(blob, dtype="<f4")
    return "[" + ",".join(f"{x:.8g}" for x in arr) + "]"


def _content_rows():
    conn = _get_connection()
    try:
        cols = [
            "url", "title", "platform", "full_text", "author", "category",
            "priority", "tags", "summary_fa", "summary_en", "key_points",
            "status", "metadata", "saved_at", "starred_at", "analysis_meta",
            "analysis_status", "analysis_error", "analysis_attempts",
        ]
        for r in conn.execute(f"SELECT {', '.join(cols)} FROM content"):
            yield {c: r[c] for c in cols}
    finally:
        conn.close()


def _docversion_rows():
    conn = _get_connection()
    try:
        cols = ["url", "full_text", "title", "summary_fa", "tags", "category", "saved_at"]
        for r in conn.execute(f"SELECT {', '.join(cols)} FROM doc_versions"):
            yield {c: r[c] for c in cols}
    finally:
        conn.close()


def _embedding_rows():
    conn = _get_connection()
    try:
        cols = ["content_id", "chunk_index", "chunk_text", "vector", "created_at"]
        # برای نگاشت درست content_id (SQLite) → id (PG) باید url را هم
        # داشته باشیم؛ در بادی، از content جدول sqlite می‌گیریم.
        for r in conn.execute(
            "SELECT e.content_id, e.chunk_index, e.chunk_text, e.vector, "
            "e.created_at, c.url "
            "FROM embeddings e LEFT JOIN content c ON c.id = e.content_id"
        ):
            yield {
                "content_id": r["content_id"],
                "chunk_index": r["chunk_index"],
                "chunk_text": r["chunk_text"],
                "vector": _blob_to_vec(r["vector"]),
                "created_at": r["created_at"],
                "url": r["url"],
            }
    finally:
        conn.close()


def _concept_rows():
    conn = _get_connection()
    try:
        cols = ["norm", "display", "doc_count", "doc_ids"]
        for r in conn.execute(f"SELECT {', '.join(cols)} FROM concepts"):
            yield {c: r[c] for c in cols}
    finally:
        conn.close()


def migrate(dry_run: bool = False):
    counts = {}
    with connect_pg() as conn:
        with conn.cursor() as cur:
            # --- content ---
            done = 0
            for row in _content_rows():
                cur.execute(
                    """
                    INSERT INTO ka.content
                        (url, title, platform, full_text, author, category,
                         priority, tags, summary_fa, summary_en, key_points,
                         status, metadata, saved_at, starred_at, analysis_meta,
                         analysis_status, analysis_error, analysis_attempts)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (url) DO UPDATE SET
                        title=EXCLUDED.title, platform=EXCLUDED.platform,
                        full_text=EXCLUDED.full_text, author=EXCLUDED.author,
                        category=EXCLUDED.category, priority=EXCLUDED.priority,
                        tags=EXCLUDED.tags, summary_fa=EXCLUDED.summary_fa,
                        summary_en=EXCLUDED.summary_en, key_points=EXCLUDED.key_points,
                        status=EXCLUDED.status, metadata=EXCLUDED.metadata,
                        starred_at=EXCLUDED.starred_at, analysis_meta=EXCLUDED.analysis_meta,
                        analysis_status=EXCLUDED.analysis_status,
                        analysis_error=EXCLUDED.analysis_error,
                        analysis_attempts=EXCLUDED.analysis_attempts,
                        saved_at=EXCLUDED.saved_at
                    """,
                    (row["url"], row["title"], row["platform"], row["full_text"],
                     row["author"], row["category"], row["priority"], row["tags"],
                     row["summary_fa"], row["summary_en"], row["key_points"],
                     row["status"], row["metadata"], row["saved_at"],
                     row["starred_at"], row["analysis_meta"], row["analysis_status"],
                     row["analysis_error"], row["analysis_attempts"]),
                )
                done += 1
            counts["content"] = done

            # --- doc_versions ---
            # No UNIQUE constraint (full_text too big for one). Idempotent by
            # explicit existence pre-check on the natural key.
            done = 0
            for row in _docversion_rows():
                cur.execute(
                    "SELECT 1 FROM ka.doc_versions "
                    "WHERE url=%s AND full_text=%s AND saved_at=%s LIMIT 1",
                    (row["url"], row["full_text"], row["saved_at"]),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO ka.doc_versions
                        (url, full_text, title, summary_fa, tags, category, saved_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (row["url"], row["full_text"], row["title"], row["summary_fa"],
                     row["tags"], row["category"], row["saved_at"]),
                )
                done += 1
            counts["doc_versions"] = done

            # --- embeddings ---
            # نگاشت url → new content.id در PG (چون id سریال جدید دارند و
            # با content_id قدیمی SQLite یکی نیستند).
            cur.execute("SELECT id, url FROM ka.content")
            pg_id_by_url = {url: cid for cid, url in cur.fetchall()}
            done = 0
            skipped = 0
            for row in _embedding_rows():
                nid = pg_id_by_url.get(row["url"]) if row.get("url") else None
                if nid is None:
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO ka.embeddings
                        (content_id, chunk_index, chunk_text, vector, created_at)
                    VALUES (%s,%s,%s,%s::vector,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (nid, row["chunk_index"], row["chunk_text"],
                     row["vector"], row["created_at"]),
                )
                done += 1
            counts["embeddings"] = done
            counts["embeddings_skipped_unmapped"] = skipped

            # --- concepts ---
            done = 0
            for row in _concept_rows():
                cur.execute(
                    """
                    INSERT INTO ka.concepts (norm, display, doc_count, doc_ids)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (norm) DO UPDATE SET
                        display=EXCLUDED.display,
                        doc_count=EXCLUDED.doc_count,
                        doc_ids=EXCLUDED.doc_ids
                    """,
                    (row["norm"], row["display"], row["doc_count"], row["doc_ids"]),
                )
                done += 1
            counts["concepts"] = done

            if dry_run:
                conn.rollback()
            else:
                conn.commit()

    return counts


def main():
    ap = argparse.ArgumentParser(description="Migrate SQLite -> PG schema ka")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not settings.pg_enabled:
        print("ERROR: PG_ENABLED=false — refusing to run. Set it in .env first.")
        sys.exit(1)

    counts = migrate(dry_run=args.dry_run)
    print("migration summary (rows processed):")
    for t, n in counts.items():
        print(f"  {t}: {n}")
    if args.dry_run:
        print("DRY RUN — not committed.")


if __name__ == "__main__":
    main()
