"""Vector store — keeps embeddings in SQLite (knowledge.db), cosine search in Python.

Chosen for phase 1: zero extra infra, same DB as content/FTS5.
Swap to Qdrant in phase 2 behind the same interface if resources grow.
"""
import json
import logging
import sqlite3
import numpy as np
from typing import List, Dict, Any

from ..storage.sqlite_store import _get_connection
from ..rag.embedder import EMBEDDING_DIM

logger = logging.getLogger(__name__)


def init_vector_tables() -> None:
    """Create embeddings table if missing."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                vector BLOB NOT NULL,          -- float32 little-endian
                created_at TEXT NOT NULL,
                FOREIGN KEY (content_id) REFERENCES content(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_content ON embeddings(content_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _vec_to_blob(vector) -> bytes:
    arr = np.asarray(vector, dtype="float32")
    return arr.tobytes()


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="float32").copy()


def store_chunks(content_id: int, chunks: List[str], vectors: List) -> None:
    """Replace all chunks for a content row with fresh ones (idempotent upsert)."""
    if not chunks or not vectors:
        return
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM embeddings WHERE content_id = ?", (content_id,))
        conn.executemany(
            """
            INSERT INTO embeddings (content_id, chunk_index, chunk_text, vector, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            [
                (content_id, i, chunks[i][:5000], _vec_to_blob(vectors[i]))
                for i in range(len(chunks))
            ],
        )
        conn.commit()
        logger.info(f"Stored {len(chunks)} chunks for content_id={content_id}")
    finally:
        conn.close()


def search(query_vec, limit: int = 5, min_score: float = 0.3) -> List[Dict[str, Any]]:
    """Cosine similarity search over all chunk vectors. Returns top-K with meta."""
    qv = np.asarray(query_vec, dtype="float32")

    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT e.content_id, e.chunk_index, e.chunk_text, e.vector,
                   c.url, c.title, c.platform, c.category, c.summary_fa, c.saved_at
            FROM embeddings e
            JOIN content c ON c.id = e.content_id
            ORDER BY e.content_id, e.chunk_index
            """
        ).fetchall()
    finally:
        conn.close()

    scores = []
    for r in rows:
        vec = _blob_to_vec(r["vector"])
        sim = float(qv @ vec)
        if sim >= min_score:
            scores.append({
                "content_id": r["content_id"],
                "chunk_index": r["chunk_index"],
                "chunk_text": r["chunk_text"],
                "score": round(sim, 4),
                "url": r["url"],
                "title": r["title"],
                "platform": r["platform"],
                "category": r["category"],
                "summary_fa": r["summary_fa"],
                "saved_at": r["saved_at"],
            })

    scores.sort(key=lambda x: -x["score"])
    return scores[:limit]


def count_embeddings() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    finally:
        conn.close()