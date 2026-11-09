"""Tests for the Odoo mirror wiring in api_save (Epic 2 Task E1)."""
import asyncio

import pytest


@pytest.fixture
def patched_save(monkeypatch):
    """Return callables to run api_save with patched dependencies.

    Returns a dict with `run()` that executes api_save and `calls` recording
    whether OdooStorage.save was invoked.
    """
    calls = {"odoo_saved": 0}

    class FakeExtractor:
        async def extract(self, url):
            from src.models import ExtractedContent
            return ExtractedContent(
                title="T", url=url, full_text="body", platform="website"
            )

    class FakeSummarizer:
        async def analyze(self, content):
            from src.models import AIAnalysis
            return AIAnalysis(summary_fa="s", category="tech")

    class FakeStorage:
        async def save(self, content, analysis):
            return "https://notion.local/x"

    class FakeSqlite:
        @staticmethod
        def save_content(content):
            return None

        @staticmethod
        def save_analysis(content, analysis):
            return None

    import src.main as main
    monkeypatch.setattr(main, "extractors", {"website": FakeExtractor()})
    monkeypatch.setattr(main, "summarizer", FakeSummarizer())
    monkeypatch.setattr(main, "storage", FakeStorage())
    monkeypatch.setattr(main, "sqlite_store", FakeSqlite())
    # تست‌های Odoo wiring نباید به Postgres واقعی بنویسند (Epic 3 cutover)
    monkeypatch.setattr(main.settings, "pg_enabled", False)

    class FakeBot:
        @staticmethod
        def _embed_content(content):
            return

    monkeypatch.setattr(main, "bot", FakeBot())

    def run(url="https://example.com/a"):
        from src.main import api_save
        from src.models import SaveRequest
        return asyncio.run(api_save(SaveRequest(url=url)))

    return {"run": run, "calls": calls}


def test_api_save_calls_odoo_when_enabled(monkeypatch, patched_save):
    from src.config import Settings
    import src.main as main

    def fake_odoo_save(self, content, analysis):
        patched_save["calls"]["odoo_saved"] += 1
        return "http://odoo.local/article/42"

    monkeypatch.setattr(main.settings, "odoo_enabled", True)
    monkeypatch.setattr(main.settings, "outline_enabled", False)
    fake_storage = type("FakeOdooStorage", (), {"save": fake_odoo_save})
    monkeypatch.setattr("src.storage.odoo.OdooStorage", fake_storage)

    resp = patched_save["run"]()
    assert resp.success is True
    assert patched_save["calls"]["odoo_saved"] == 1
    assert resp.odoo_url == "http://odoo.local/article/42"


def test_api_save_skips_odoo_when_disabled(monkeypatch, patched_save):
    from src.config import Settings
    import src.main as main

    monkeypatch.setattr(main.settings, "odoo_enabled", False)
    monkeypatch.setattr(main.settings, "outline_enabled", False)

    def boom(*a, **k):
        raise AssertionError("OdooStorage should not be imported when disabled")

    monkeypatch.setattr("src.storage.odoo.OdooStorage", boom)
    resp = patched_save["run"]()
    assert resp.success is True
    assert resp.odoo_url is None
