"""Tests for the Epic 3 dual-write mirror (pg_mirror.py)."""
import pytest

from psycopg.rows import dict_row

from src.config import settings
from src.models import ExtractedContent, AIAnalysis
from src.storage.pg_mirror import mirror_analysis, mirror_save
from src.storage.db_pg import connect_pg


def _sample_content(url="https://example.com/dual-write-test"):
    return ExtractedContent(
        url=url,
        title="Dual-write test doc",
        platform="web",
        full_text="This is a dual-write test document body.",
        author="tester",
    )


def _sample_analysis():
    return AIAnalysis(
        summary_fa="خلاصه تست دو-نویسی",
        summary_en="dual-write test summary",
        key_points=["point one", "point two"],
        category="test",
        tags=["t1", "t2"],
        priority="high",
        entities=["X"],
        concepts=["مفهوم"],
        connections=[],
        contradictions=[],
    )


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_mirror_save_upsert_roundtrip():
    url = "https://example.com/dual-write-test"
    mirror_save(_sample_content(url), None)
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT url, title, full_text FROM ka.content WHERE url=%s", (url,))
            row = cur.fetchone()
    assert row is not None
    assert row["title"] == "Dual-write test doc"
    # cleanup
    with connect_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ka.content WHERE url=%s", (url,))
            conn.commit()


@pytest.mark.skipif(not settings.pg_enabled, reason="PG_ENABLED=false")
def test_mirror_analysis_updates_fields():
    url = "https://example.com/dual-write-test2"
    mirror_save(_sample_content(url), None)
    mirror_analysis(url, _sample_analysis())
    with connect_pg() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT summary_fa, category, tags, key_points FROM ka.content WHERE url=%s",
                (url,),
            )
            row = cur.fetchone()
    assert row is not None
    assert row["summary_fa"] == "خلاصه تست دو-نویسی"
    assert row["category"] == "test"
    assert "t1" in row["tags"]
    with connect_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ka.content WHERE url=%s", (url,))
            conn.commit()
