"""Epic — Markdown → HTML (FR-003) + RTL per-paragraph (FR-008)."""
import pytest

from lxml import html as lxml_html

from src.models import AIAnalysis, ExtractedContent
from src.storage.odoo import OdooStorage


def _content(full_text="Body text here", title="Sample Article", platform="website", url="https://example.com/a"):
    return ExtractedContent(
        title=title,
        url=url,
        full_text=full_text,
        platform=platform,
        published_date="2026-11-01",
    )


def _analysis(summary_fa="خلاصه"):
    return AIAnalysis(summary_fa=summary_fa, category="Tech", tags=["ai"])


def _make_store():
    from src.config import Settings

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
    )
    return OdooStorage(cfg=cfg, data_dir="/tmp")


def test_markdown_to_html_renders_valid_html():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="## فصل اول\n- مورد ۱\n- مورد ۲"),
        _analysis(),
    )
    # HTML معتبر است (lxml parse می‌کند)
    lxml_html.fromstring("<div>" + html + "</div>")
    # عنوان به‌صورت h1 رندر شده
    assert "<h1>Sample Article</h1>" in html
    # بولدت به‌صورت li رندر شده
    assert "<li>" in html
    # هدینگ دوم به‌صورت h2 رندر شده
    assert "<h2>فصل اول</h2>" in html


def test_markdown_to_html_escapes_raw_html():
    # FR-003: HTML خام ورودی باید غیرفعال شود (امن).
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="<script>alert('xss')</script>"),
        _analysis(),
    )
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html


def test_rtl_per_paragraph_persian():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="این یک متن فارسی است."),
        _analysis(summary_fa="خلاصه فارسی"),
    )
    # پاراگراف فارسی باید dir="rtl" داشته باشد
    assert 'dir="rtl"' in html


def test_rtl_per_paragraph_latin():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="This is an English paragraph."),
        _analysis(summary_fa=""),
    )
    # پاراگراف انگلیسی باید dir="ltr" داشته باشد
    assert 'dir="ltr"' in html


def test_save_sets_icon_from_platform(tmp_path):
    from src.config import Settings

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.next_id = 100

        def execute_kw(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))
            if method == "search_read" and args and args[0] and args[0][0]:
                return [{"id": 5}] if args[0][0][1] == "=" else []
            if method == "create":
                new_id = self.next_id
                self.next_id += 1
                return new_id
            return 0

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
    )
    # platform شناخته‌شده
    client = FakeClient()
    store = OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))
    store.save(_content(platform="youtube", url="https://example.com/y"), _analysis())
    create_call = [c for c in client.calls if c[1] == "create"]
    assert create_call[-1][2][0]["icon"] == "▶️"
    # platform ناشناخته → پیش‌فرض
    client2 = FakeClient()
    store2 = OdooStorage(client=client2, cfg=cfg, data_dir=str(tmp_path))
    store2.save(_content(platform="mystery", url="https://example.com/m"), _analysis())
    create_call2 = [c for c in client2.calls if c[1] == "create"]
    assert create_call2[-1][2][0]["icon"] == "📄"
