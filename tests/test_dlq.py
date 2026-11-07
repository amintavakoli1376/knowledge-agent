"""Tests for DLQ (dead-letter queue) for Odoo mirror (Epic 2 Task C3)."""
from src.models import AIAnalysis, ExtractedContent
from src.storage.dlq import DLQ
from src.storage.odoo import OdooStorage


def _content(url="https://example.com/a"):
    return ExtractedContent(title="T", url=url, full_text="body", platform="website")


def _analysis():
    return AIAnalysis(summary_fa="s")


class FailingClient:
    """Always raises, to force the DLQ path."""
    def execute_kw(self, *a, **k):
        raise RuntimeError("odoo down")


class OkClient:
    def __init__(self):
        self.creates = []

    def execute_kw(self, model, method, args, kwargs=None):
        if model == "knowledge.article" and method == "search_read":
            return []
        if model == "knowledge.article" and method == "create":
            self.creates.append(args[0])
            return 42
        return 0


def test_dlq_enqueue_and_count(tmp_path):
    q = DLQ(data_dir=str(tmp_path))
    q.enqueue("https://example.com/x", {"name": "x"}, "boom")
    assert q.count() == 1


def test_dlq_requeue_preserves_attempts(tmp_path):
    q = DLQ(data_dir=str(tmp_path))
    q.enqueue("https://example.com/x", {"name": "x"}, "boom")
    rows = q.pending(limit=10)
    assert len(rows) == 1
    assert rows[0]["attempts"] == 1
    q.mark_failed(rows[0]["id"], "boom again")
    rows2 = q.pending(limit=10)
    assert rows2[0]["attempts"] == 2


def test_dlq_retry_releases_on_success(tmp_path):
    q = DLQ(data_dir=str(tmp_path))
    q.enqueue("https://example.com/x", {"name": "x"}, "boom")
    rows = q.pending(limit=10)
    q.mark_success(rows[0]["id"])
    assert q.count() == 0


def test_save_falls_back_to_dlq_on_failure(tmp_path):
    q = DLQ(data_dir=str(tmp_path))
    from src.config import Settings

    cfg = Settings(odoo_enabled=True, odoo_url="https://o", odoo_db="k",
                   odoo_api_key="k", odoo_uid=6)
    store = OdooStorage(client=FailingClient(), cfg=cfg, data_dir=str(tmp_path),
                        dlq=q)
    out = store.save(_content(), _analysis())
    assert out is None
    assert q.count() == 1
