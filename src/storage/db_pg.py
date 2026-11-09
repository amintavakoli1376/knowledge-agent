"""Postgres connection layer for the migration target (schema `ka`).

Epic 3: the knowledge source-of-truth moves from SQLite to Postgres
(schema `ka` on the same Odoo PG cluster). This module mirrors
`db.py`'s API so callers are agnostic to the backend during dual-write
and cutover.

Connection parameters come from `settings` (PG_* env vars) so the
enabled/failover logic stays one place.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from ..config import settings

logger = logging.getLogger(__name__)


def _dsn() -> str:
    return (
        f"host={settings.pg_host} port={settings.pg_port} "
        f"user={settings.pg_user} dbname={settings.pg_db} "
        f"options=-csearch_path={settings.pg_schema},public"
    )


def connect_pg() -> psycopg.Connection:
    """Open a new connection, search_path pinned to the `ka` schema."""
    conn = psycopg.connect(
        _dsn(),
        password=settings.pg_password or None,
        autocommit=False,
    )
    return conn


class PgUnavailable(Exception):
    """Raised when PG is expected but unreachable (fail-fast in dual-write)."""


def ping() -> bool:
    """True if a round-trip to PG succeeds."""
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
    except Exception as e:  # noqa: BLE001
        logger.warning("PG ping failed: %s", e)
        return False


def pg_get_by_url(url: str) -> dict | None:
    """Return the ka.content row for a URL (same shape as sqlite get_by_url)."""
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM ka.content WHERE url=%s LIMIT 1", (url,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("PG get_by_url failed: %s", e)
        return None


def pg_count_content() -> int:
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ka.content")
                return cur.fetchone()[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("PG count_content failed: %s", e)
        return 0


def pg_vector_search(
    qv: list[float], limit: int = 5, min_score: float = 0.3
) -> list[dict]:
    """Cosine-similarity search over ka.embeddings via pgvector (<=>).

    Mirrors vector_store.search()'s result shape (chunk text + doc meta).
    Only rows scoring >= min_score are returned, ranked desc.
    """
    vec_str = "[" + ",".join(f"{float(x):.6f}" for x in qv) + "]"
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT e.content_id, e.chunk_index, e.chunk_text,
                           c.url, c.title, c.platform, c.category,
                           COALESCE(c.summary_fa,'') AS summary_fa,
                           COALESCE(c.saved_at,'') AS saved_at,
                           (1 - (e.vector <=> %s::vector)) AS score
                    FROM ka.embeddings e
                    JOIN ka.content c ON c.id = e.content_id
                    WHERE (1 - (e.vector <=> %s::vector)) >= %s
                    ORDER BY e.vector <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_str, vec_str, min_score, vec_str, limit),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "content_id": r["content_id"],
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"],
                    "score": round(float(r["score"]), 4),
                    "url": r["url"],
                    "title": r["title"],
                    "platform": r["platform"],
                    "category": r["category"],
                    "summary_fa": r["summary_fa"],
                    "saved_at": r["saved_at"],
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("PG vector search failed: %s", e)
        return []


def pg_get_content_id_by_url(url: str) -> int | None:
    """Return ka.content.id for a URL (None if absent)."""
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM ka.content WHERE url=%s", (url,))
                row = cur.fetchone()
                return int(row[0]) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("PG get content_id failed: %s", e)
        return None


def pg_store_chunks(content_id: int, chunks: list[str], vectors: list) -> None:
    """Replace all embedding chunks for a ka.content row (idempotent)."""
    if not chunks or not vectors:
        return
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ka.embeddings WHERE content_id=%s", (content_id,))
                now = datetime.now(timezone.utc).isoformat()
                for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
                    vec_str = "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
                    cur.execute(
                        """
                        INSERT INTO ka.embeddings
                            (content_id, chunk_index, chunk_text, vector, created_at)
                        VALUES (%s,%s,%s,%s::vector,%s)
                        """,
                        (content_id, i, chunk, vec_str, now),
                    )
                conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("PG store_chunks failed: %s", e)


def pg_fts_search(query: str, limit: int = 5) -> list[dict]:
    """Keyword search over ka.content.search_ts (tsvector) — mirrors sqlite fts_search."""
    import re

    tokens = re.findall(r"[\w\u0600-\u06FF]+", query)
    if not tokens:
        return []
    # websearch_to_tsquery: OR ضمنی، -، "…"
    q = " OR ".join(tokens[:8])
    try:
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT c.id AS content_id, c.url, c.title, c.platform, c.category,
                           COALESCE(c.summary_fa,'') AS summary_fa, c.full_text,
                           ts_rank(c.search_ts, websearch_to_tsquery('simple', %s)) AS score
                    FROM ka.content c
                    WHERE c.search_ts @@ websearch_to_tsquery('simple', %s)
                    ORDER BY score DESC, c.saved_at DESC
                    LIMIT %s
                    """,
                    (q, q, limit),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "content_id": r["content_id"],
                    "url": r["url"],
                    "title": r["title"],
                    "platform": r["platform"],
                    "category": r["category"],
                    "summary_fa": r["summary_fa"],
                    "snippet": (r["full_text"] or "")[:300],
                    "score": round(float(r["score"]), 4),
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("PG FTS search failed: %s", e)
        return []


def pg_full_texts(content_ids: list[int]) -> dict[int, str]:
    """Full text for content ids (dict id -> full_text)."""
    if not content_ids:
        return {}
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                # psycopg: ساختن ردیف‌های پارامتر
                marks = ",".join(["%s"] * len(content_ids))
                cur.execute(
                    f"SELECT id, COALESCE(full_text,'') FROM ka.content WHERE id IN ({marks})",
                    list(content_ids),
                )
                return {int(r[0]): r[1] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("PG full_texts failed: %s", e)
        return {}


def pg_graph_expand(hit_ids: list[int], hit_urls: list[str]) -> list[int]:
    """Graph expansion via ka.concepts: docs sharing a concept with winners.

    doc_ids holds PG content ids (positive, already remapped) and negative
    url-hashes (from the legacy SQLite concepts). We resolve a negative hash
    to a PG content id by recomputing the URL hash over ka.content urls.
    Returns new doc ids (not the winners themselves), capped at 2.
    """
    import hashlib

    winners_ids = set(hit_ids)
    winners_urls = {u.rstrip("/") for u in hit_urls if u}

    def _url_key(u: str) -> int:
        return -(int(hashlib.sha1(u.encode()).hexdigest()[:8], 16) % 1_000_000_000) - 1

    neg_keys = {_url_key(u) for u in winners_urls}
    try:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                # index منفی hash → content id از روی urls فعلی
                cur.execute("SELECT id, url FROM ka.content")
                neg_hash_to_id: dict[int, int] = {}
                for cid, url in cur.fetchall():
                    if url:
                        neg_hash_to_id[_url_key(url)] = int(cid)
                cur.execute("SELECT doc_ids FROM ka.concepts WHERE doc_count >= 2")
                candidates: list[int] = []
                for (docids,) in cur.fetchall():
                    members = [x.strip() for x in (docids or "").split(",") if x.strip()]
                    pos = {int(x) for x in members if x.isdigit()}
                    neg = {int(x[1:]) for x in members if x.startswith("-") and x[1:].isdigit()}
                    if not (pos & winners_ids or neg & neg_keys):
                        continue
                    for x in members:
                        if x.isdigit():
                            cid = int(x)
                            if cid not in winners_ids:
                                candidates.append(cid)
                        elif x.startswith("-"):
                            cid = neg_hash_to_id.get(int(x[1:]))
                            if cid and cid not in winners_ids:
                                candidates.append(cid)
                return list(dict.fromkeys(candidates))[:2]
    except Exception as e:  # noqa: BLE001
        logger.warning("PG graph expand failed: %s", e)
        return []
