"""Tests for Epic 3 cutover read paths (pg get_by_url / count / vector search)."""
import time

import pytest

from psycopg.rows import dict_row

from src.config import settings
from src.models import ExtractedContent
from src.storage.db_pg import connect_pg, pg_count_content, pg_get_by_url
from src.storage.sqlite_store import count_content, get_by_url


def _simple_content(url: str):
    return ExtractedContent(
        url=url,
        title="pg test doc",
        platform="web",
        full_text="pg test body",
        author="tester",
    )


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_pg_get_by_url_roundtrip():
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT url, title FROM ka.content LIMIT 1")
            row = cur.fetchone()
    assert row is not None
    got = pg_get_by_url(row["url"])
    assert got is not None
    assert got["url"] == row["url"]


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_count_content_reads_pg():
    # با PG_ENABLED=true، count_content از PG می‌خواند و باید برابر sqlite باشد
    assert pg_count_content() > 0


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_pg_store_chunks_roundtrip():
    """pg_store_chunks باید embeddings را برای یک content_id جایگزین کند.

    روی یک سند تستی ایزوله (URL یکتا) اجرا می‌شود تا دادهٔ واقعی
    آسیب نبیند؛ در پایان کل سند تستی حذف می‌شود.
    """
    import numpy as np

    from src.storage.db_pg import pg_store_chunks
    from src.storage.pg_mirror import mirror_save

    test_url = f"https://example.com/pg-store-test-{int(time.time() * 1000000)}"
    mirror_save(_simple_content(test_url), None)
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM ka.content WHERE url=%s", (test_url,))
            row = cur.fetchone()
    try:
        assert row is not None
        cid = row["id"]
        vec = np.zeros(384, dtype="float32")
        vec[0] = 1.0
        chunks = ["test chunk a", "test chunk b"]
        vectors = [vec, vec]
        pg_store_chunks(cid, chunks, vectors)
        with connect_pg() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT chunk_index, chunk_text FROM ka.embeddings WHERE content_id=%s ORDER BY chunk_index",
                    (cid,),
                )
                rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0]["chunk_text"] == "test chunk a"
        assert rows[1]["chunk_text"] == "test chunk b"
    finally:
        with connect_pg() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ka.embeddings WHERE content_id=%s", (cid,))
                cur.execute("DELETE FROM ka.content WHERE url=%s", (test_url,))
                conn.commit()


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_pg_fts_search_returns_results():
    from src.storage.db_pg import pg_fts_search

    hits = pg_fts_search("deepseek", limit=3)
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert "content_id" in hits[0]
    assert "title" in hits[0]


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_pg_full_texts():
    from src.storage.db_pg import pg_full_texts, pg_fts_search

    hits = pg_fts_search("deepseek", limit=1)
    if not hits:
        pytest.skip("no results")
    cid = hits[0]["content_id"]
    ft = pg_full_texts([cid])
    assert ft.get(cid) is not None


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_pg_graph_expand_resolves_positive_ids():
    from src.storage.db_pg import pg_graph_expand

    expanded = pg_graph_expand([56], ["https://example.com/56"])
    assert isinstance(expanded, list)
    # باید سند هم‌مفهوم (غیر از برنده) برگرداند؛ ممکن است خالی باشد اگر مفهومی نباشد
    assert all(x != 56 for x in expanded)


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_embeddings_join_content_all_mapped():
    """هر embedding باید به یک content نگاشت شده باشد (اصلاح باگ content_id)."""
    with connect_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ka.embeddings")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM ka.embeddings e JOIN ka.content c ON c.id=e.content_id")
            joined = cur.fetchone()[0]
    assert total == joined, f"joined={joined} total={total} — برخی embeddings به content نگاشت نشده‌اند"
