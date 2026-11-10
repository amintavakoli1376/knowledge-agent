"""Tests for OdooStorage mirror save() (Epic 2 Task B1–B4)."""
import pytest

from src.models import AIAnalysis, ExtractedContent
from src.storage.odoo import OdooStorage
from src.storage.odoo_client import OdooClient


def _content(platform="website", url="https://example.com/a"):
    return ExtractedContent(
        title="Sample Article",
        url=url,
        full_text="Body text here",
        platform=platform,
        published_date="2026-11-01",
    )


def _analysis():
    return AIAnalysis(
        summary_fa="خلاصه",
        category="Tech",
        tags=["ai", "python"],
    )


class FakeClient:
    """Records execute_kw calls; simulates search -> create -> id mapping."""
    def __init__(self, parent_ids=None):
        self.calls = []
        self.parent_ids = parent_ids or {}  # platform -> id
        self.next_id = 100
        self.created = []

    def execute_kw(self, model, method, args, kwargs=None):
        self.calls.append((model, method, args, kwargs))
        # search_read on knowledge.article to find parent by name
        if model == "knowledge.article" and method == "search_read":
            name = args[0][0][1] if args and args[0] and args[0][0] else None
            # domain: [['name','=',platform]]
            for dom in (args[0] if args else []):
                if dom and dom[0] == "name" and dom[1] == "=":
                    pid = self.parent_ids.get(dom[2])
                    return [{"id": pid}] if pid else []
            return []
        if model == "knowledge.article" and method == "create":
            self.created.append(args[0])
            new_id = self.next_id
            self.next_id += 1
            return new_id
        if model == "knowledge.article" and method == "search_count":
            return 0
        raise AssertionError(f"Unexpected call: {model}.{method}")


def _make_store(client, tmp_path):
    from src.config import Settings

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
    )
    return OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))


def test_save_creates_article_and_returns_url(monkeypatch, tmp_path):
    client = FakeClient(parent_ids={"website": 5})
    store = _make_store(client, tmp_path)
    out = store.save(_content(), _analysis())

    # returns a URL-ish string
    assert isinstance(out, str)
    assert out

    # create was called with vals
    create_call = [c for c in client.calls if c[1] == "create"]
    assert create_call, "expected knowledge.article create call"
    vals = create_call[-1][2][0]
    assert vals["name"] == "Sample Article"
    assert vals["article_url"] == "https://example.com/a"
    assert vals["parent_id"] == 5
    assert vals["body"]


def test_save_dedup_returns_existing(tmp_path):
    # Two saves of the same URL must not create twice.
    client = FakeClient(parent_ids={"website": 5})
    store = _make_store(client, tmp_path)
    first = store.save(_content(), _analysis())
    creates_after_first = [c for c in client.calls if c[1] == "create"]
    second = store.save(_content(), _analysis())
    creates_after_second = [c for c in client.calls if c[1] == "create"]
    assert len(creates_after_second) == len(creates_after_first)  # no new create
    assert first == second


def test_get_or_create_parent_reuses_existing(tmp_path):
    parent = FakeClient(parent_ids={"telegram": 12})
    store = _make_store(parent, tmp_path)
    pid = store._get_or_create_parent("telegram")
    assert pid == 12
    # no create happened
    assert not [c for c in parent.calls if c[1] == "create"]


def test_get_or_create_parent_creates_when_missing(tmp_path):
    client = FakeClient(parent_ids={})
    store = _make_store(client, tmp_path)
    pid = store._get_or_create_parent("telegram")
    assert pid == 100  # first created id
    created = [c for c in client.calls if c[1] == "create"]
    assert len(created) == 1
    assert created[0][2][0]["name"] == "telegram"


def test_get_or_create_parent_under_workspace_root(tmp_path):
    """Epic 2 / 2F-2: when odoo_workspace_root_id is set, the parent is
    created as a child of the workspace root (Q5 visibility)."""
    from src.config import Settings

    client = FakeClient(parent_ids={})
    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
        odoo_workspace_root_id=37,
    )
    store = OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))
    pid = store._get_or_create_parent("telegram")
    assert pid == 100
    created = [c for c in client.calls if c[1] == "create"]
    assert len(created) == 1
    assert created[0][2][0]["name"] == "telegram"
    assert created[0][2][0]["parent_id"] == 37
    # 2F-2: والد زیر ریشه باید اسکیمای article_properties را داشته باشد
    assert created[0][2][0]["article_properties_definition"]


def test_save_includes_article_properties(monkeypatch, tmp_path):
    """Epic 2 / 2F-2: source_url/tags/source_date land in article_properties."""
    client = FakeClient(parent_ids={"website": 5})
    store = _make_store(client, tmp_path)
    store.save(_content(), _analysis())
    create_call = [c for c in client.calls if c[1] == "create"]
    vals = create_call[-1][2][0]
    props = vals.get("article_properties", {})
    assert props.get("source_url") == "https://example.com/a"
    assert props.get("platform") == "website"
    assert props.get("tags") == "ai, python"
    assert props.get("source_date") == "2026-11-01"
