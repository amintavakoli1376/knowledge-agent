"""مقاوم‌سازی 429-quota (t_c270e218) — تست رفتاری.

اجرا:  PYTHONPATH=venv/lib/python3.11/site-packages /usr/bin/pytest tests/test_quota_recovery.py -q
(بدون pytest-asyncio — تست‌های async با asyncio.run اجرا می‌شوند.)
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models import AIAnalysis, ExtractedContent
from src.processors.summarizer import (
    ContentSummarizer,
    QuotaExceededError,
    SummarizationError,
    _quota_pending_analysis,
    is_quota_error,
)
from src.processors import recovery
from src.storage import sqlite_store


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeAPIError(Exception):
    """شبیه‌سازی خطای openai SDK — status_code/body مثل APIStatusError."""

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def make_content(**kw):
    defaults = dict(
        url="https://example.com/quota-test",
        title="Quota test doc",
        full_text="متن نمونهٔ محتوا برای تست. " * 20,
        platform="website",
    )
    defaults.update(kw)
    return ExtractedContent(**defaults)


def make_pending_analysis():
    return _quota_pending_analysis(FakeAPIError("Error code: 429 - Monthly spending limit reached", 429))


def fake_llm_response(payload: dict):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    """DB موقت برای تست‌های sqlite_store — DB واقعی دست‌نخورده می‌ماند."""
    monkeypatch.setattr(sqlite_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sqlite_store, "DB_PATH", tmp_path / "knowledge.db")
    return sqlite_store


# ---------------------------------------------------------------------------
# 1) تشخیص خطای سهمیه
# ---------------------------------------------------------------------------

class TestQuotaDetection:
    def test_429_with_monthly_spending_limit(self):
        e = FakeAPIError(
            "Error code: 429 - {'error': {'message': 'Monthly spending limit reached'}}",
            status_code=429,
        )
        assert is_quota_error(e) is True

    def test_429_with_insufficient_quota_marker(self):
        e = FakeAPIError("Error code: 429 - insufficient_quota", status_code=429)
        assert is_quota_error(e) is True

    def test_free_usage_limit_in_message(self):
        e = FakeAPIError("FreeUsageLimit: you've used all your free credits")
        assert is_quota_error(e) is True

    def test_generic_429_without_quota_marker_is_not_quota(self):
        e = FakeAPIError("Error code: 429 - too many requests", status_code=429)
        assert is_quota_error(e) is False

    def test_unrelated_error_is_not_quota(self):
        assert is_quota_error(ValueError("boom")) is False
        assert is_quota_error(FakeAPIError("server exploded", status_code=500)) is False

    def test_wrapped_exception_chain(self):
        inner = FakeAPIError("Monthly spending limit reached", status_code=429)
        try:
            try:
                raise inner
            except Exception as exc:
                raise RuntimeError("chat failed") from exc
        except RuntimeError as wrapped:
            assert is_quota_error(wrapped) is True


# ---------------------------------------------------------------------------
# 2) analyze با 429 → pending با خلاصهٔ خالی (نه متن خطا)
# ---------------------------------------------------------------------------

class TestAnalyzeQuotaBehavior:
    def _summarizer_with_error(self, err):
        s = ContentSummarizer()
        s.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=pytest.fail if False else None)
            )
        )
        s.client.chat.completions.create = self._raising(err)
        return s

    @staticmethod
    def _raising(err):
        async def _raise(*a, **kw):
            raise err
        return _raise

    def test_analyze_quota_raises_quota_exceeded(self):
        err = FakeAPIError(
            "Error code: 429 - {'error': {'message': 'Monthly spending limit reached'}}",
            status_code=429,
        )
        s = self._summarizer_with_error(err)
        with pytest.raises(QuotaExceededError):
            asyncio.run(s.analyze(make_content()))

    def test_analyze_nonquota_error_raises_instead_of_storing_error_text(self):
        """خطای غیرسهمیه‌ای — استثنا بالا می‌رود؛ هیچ متن خطایی در summary ذخیره نمی‌شود."""
        s = self._summarizer_with_error(FakeAPIError("connection reset", status_code=500))
        with pytest.raises(SummarizationError):
            asyncio.run(s.analyze(make_content()))

    def test_pending_analysis_has_empty_summaries_and_flag(self):
        p = make_pending_analysis()
        assert p.summary_fa == ""
        assert p.summary_en == ""
        assert p.analysis_status == "pending_resummarize"
        assert p.analysis_attempts == 1
        assert p.analysis_error  # پیام کوتاه خطا برای دیباگ
        assert "Monthly spending limit" not in p.summary_fa  # متن خطا در summary نیست

    def test_analyze_success_has_no_flags(self):
        """مسیر موفق — رفتار قدیمی کامل (خلاصه + پاس ۱) بدون flag."""
        s = ContentSummarizer()
        step2 = fake_llm_response({
            "summary_fa": "خلاصهٔ فارسی تست",
            "summary_en": "English test summary",
            "key_points": ["نکتهٔ اول"],
            "category": "AI/ML",
            "tags": ["test"],
            "priority": "High",
        })
        step1 = fake_llm_response({
            "entities": ["OpenAI"],
            "concepts": ["گراف دانش"],
            "connections": [],
            "contradictions": [],
        })
        calls = []

        async def fake_create(*a, **kw):
            calls.append(kw)
            return step2 if len(calls) == 1 else step1

        s.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
        result = asyncio.run(s.analyze(make_content()))
        assert result.summary_fa == "خلاصهٔ فارسی تست"
        assert result.summary_en == "English test summary"
        assert result.category == "AI/ML"
        assert result.concepts == ["گراف دانش"]
        assert result.analysis_status == ""


# ---------------------------------------------------------------------------
# 3) SQLite — ذخیرهٔ flag + کوئری pending + ریکاوری
# ---------------------------------------------------------------------------

class TestSqlitePendingFlow:
    def test_save_and_roundtrip_pending_flags(self, tmp_store):
        content = make_content()
        tmp_store.save_content(content, make_pending_analysis())
        row = tmp_store.get_by_url(content.url)
        assert row is not None
        assert row["summary_fa"] == ""          # متن خطا ذخیره نشده
        assert row["summary_en"] == ""
        assert row["analysis_status"] == "pending_resummarize"
        assert row["analysis_attempts"] == 1
        assert "Monthly spending limit" in row["analysis_error"]

    def test_save_analysis_persists_flags(self, tmp_store):
        content = make_content()
        tmp_store.save_content(content)  # بدون analysis
        tmp_store.save_analysis(content.url, make_pending_analysis())
        row = tmp_store.get_by_url(content.url)
        assert row["analysis_status"] == "pending_resummarize"
        assert row["summary_fa"] == ""

    def test_success_clears_flag(self, tmp_store):
        content = make_content()
        tmp_store.save_content(content, make_pending_analysis())
        good = AIAnalysis(
            summary_fa="خلاصهٔ نهایی", summary_en="final summary",
            category="AI/ML", tags=["x"], priority="High",
            analysis_status="", analysis_error="", analysis_attempts=0,
        )
        tmp_store.mark_analysis_complete(content.url, good)
        row = tmp_store.get_by_url(content.url)
        assert row["summary_fa"] == "خلاصهٔ نهایی"
        assert row["analysis_status"] == ""
        assert tmp_store.get_pending_resummarize() == []

    def test_get_pending_and_attempt_cap(self, tmp_store):
        c1 = make_content(url="https://example.com/p1")
        c2 = make_content(url="https://example.com/p2")
        tmp_store.save_content(c1, make_pending_analysis())
        tmp_store.save_content(c2, make_pending_analysis())

        pending = tmp_store.get_pending_resummarize()
        assert [r["url"] for r in pending] == [c1.url, c2.url]  # FIFO

        # تلاش‌ها تا سقف ۵
        for attempts in (2, 3, 4, 5):
            tmp_store.mark_analysis_failed(c1.url, "quota still", attempts)
        assert tmp_store.get_by_url(c1.url)["analysis_attempts"] == 5
        remaining = tmp_store.get_pending_resummarize(max_attempts=5)
        assert [r["url"] for r in remaining] == [c2.url]  # c1 از سقف گذشته

    def test_existing_rows_unaffected_by_migration(self, tmp_store):
        """دیتای legacy (بدون flag) دست‌نخورده می‌ماند و pending حساب نمی‌شود."""
        content = make_content(url="https://example.com/legacy")
        good = AIAnalysis(summary_fa="قدیمی", summary_en="old")
        tmp_store.save_content(content, good)
        row = tmp_store.get_by_url(content.url)
        assert row["summary_fa"] == "قدیمی"
        assert (row["analysis_status"] or "") == ""
        assert tmp_store.get_pending_resummarize() == []


# ---------------------------------------------------------------------------
# 4) recovery worker — end-to-end با فیک
# ---------------------------------------------------------------------------

class FakeSummarizer:
    def __init__(self, results):
        self.results = list(results)  # AIAnalysis یا Exception به‌ترتیب
        self.calls = []

    async def summarize_ai(self, content):
        self.calls.append(content.url)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeNotionStorage:
    def __init__(self, page_id):
        self.page_id = page_id
        self.patched = []

    async def find_page_by_url(self, url):
        return self.page_id

    async def patch_summary(self, page_id, analysis):
        self.patched.append((page_id, analysis))
        return True


class TestRecoveryWorker:
    def test_recovery_success_updates_db_and_notion(self, tmp_store):
        content = make_content(url="https://example.com/rec1")
        tmp_store.save_content(content, make_pending_analysis())

        good = AIAnalysis(
            summary_fa="خلاصهٔ بازیابی‌شده", summary_en="recovered",
            category="Technology", tags=["rec"], priority="Medium",
        )
        fake_s = FakeSummarizer([good])
        fake_n = FakeNotionStorage(page_id="page-abc")

        out = asyncio.run(recovery.recover_pending(
            summarizer=fake_s, storage=fake_n, sleep_seconds=0
        ))
        assert out["recovered"] == [content.url]
        assert fake_s.calls == [content.url]

        row = tmp_store.get_by_url(content.url)
        assert row["summary_fa"] == "خلاصهٔ بازیابی‌شده"
        assert row["analysis_status"] == ""
        assert tmp_store.get_pending_resummarize() == []

        # patch در Notion با همان analysis
        assert fake_n.patched and fake_n.patched[0][0] == "page-abc"
        assert fake_n.patched[0][1].summary_fa == "خلاصهٔ بازیابی‌شده"

    def test_recovery_quota_still_down_stops_and_counts_attempt(self, tmp_store):
        content = make_content(url="https://example.com/rec2")
        tmp_store.save_content(content, make_pending_analysis())

        fake_s = FakeSummarizer([QuotaExceededError("Monthly spending limit reached")])
        out = asyncio.run(recovery.recover_pending(
            summarizer=fake_s, storage=FakeNotionStorage("p"), sleep_seconds=0
        ))
        assert out["recovered"] == []
        assert len(out["failed"]) == 1 and "quota" in out["failed"][0]["error"]
        # اولین خطای سهمیه‌ای → حلقه تمام می‌شود (تلاش بقیه بی‌فایده است)
        assert fake_s.calls == [content.url]

        row = tmp_store.get_by_url(content.url)
        assert row["analysis_status"] == "pending_resummarize"
        assert row["analysis_attempts"] == 2  # 1 (اولیه) + 1 (این تلاش)

    def test_recovery_skips_empty_text_and_clears_flag(self, tmp_store):
        content = make_content(url="https://example.com/empty", full_text="")
        tmp_store.save_content(content, make_pending_analysis())
        fake_s = FakeSummarizer([])
        out = asyncio.run(recovery.recover_pending(
            summarizer=fake_s, storage=None, sleep_seconds=0
        ))
        assert out["skipped"] == [content.url]
        assert fake_s.calls == []  # خلاصه‌ساز صدا نشد
        assert tmp_store.get_by_url(content.url)["analysis_status"] == ""

    def test_row_to_content_strips_title_prefix(self):
        title = "تیتر تستی"
        row = {"url": "u", "title": title, "full_text": title + "\nبدنهٔ متن", "platform": "telegram"}
        c = recovery._row_to_content(row)
        assert c.full_text == "بدنهٔ متن"


# ---------------------------------------------------------------------------
# 5) Telegram UX — پیام صادقانه، بدون متن خطا
# ---------------------------------------------------------------------------

class TestTelegramPendingNotice:
    def test_pending_message_honest_and_clean(self):
        from src.gateway.telegram_bot import TelegramBot

        content = make_content(title="مقالهٔ مهم")
        msg = TelegramBot._pending_message(content, "https://example.notion.site/p1")
        assert "ذخیره شد" in msg
        assert "سهمیه" in msg
        assert "خودکار" in msg
        assert "https://example.notion.site/p1" in msg
        # متن خطای خام لو نرود
        assert "Monthly spending limit" not in msg
        assert "429" not in msg

    def test_pending_message_without_notion_url(self):
        from src.gateway.telegram_bot import TelegramBot

        msg = TelegramBot._pending_message(make_content(), "")
        assert "http" not in msg.lower()  # هیچ لینکی به پیام چسبیده نیست
        assert "暂" not in msg  # sanity: متن فارسی سالم
